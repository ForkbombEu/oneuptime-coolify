"""Contracts the committed bundle must meet (see .scratch/oneuptime-coolify/spec.md)."""

import unittest

from check import check_compose, load
from common import ROOT, compose_config

CORE = ROOT / "docker-compose.yml"
WITH_BACKUPS = ROOT / "docker-compose.backups.yml"


def load_unnormalized(path):
    """The model as written: no project-derived names, volumes left in short syntax."""
    return compose_config([path], "--no-interpolate", "--no-normalize", "--no-path-resolution")


class BundleContract:
    """Contracts both bundles meet. Subclasses set BUNDLE."""

    @classmethod
    def setUpClass(cls):
        cls.model = load(cls.BUNDLE)
        cls.services = cls.model["services"]

    def test_bundle_passes_the_static_check(self):
        self.assertEqual(check_compose(self.BUNDLE), [])

    def test_volumes_are_scoped_to_the_coolify_resource(self):
        # A fixed name would make the restore-drill resource share production's volumes.
        for name, volume in load_unnormalized(self.BUNDLE)["volumes"].items():
            self.assertNotIn("name", volume or {}, f"volume {name} has a fixed name")

    def test_no_leftover_extension_blocks(self):
        self.assertFalse([key for key in self.model if key.startswith("x-")])

    def test_no_empty_environment_variables_except_nginx_template_inputs(self):
        # nginx's envsubst leaves an unset ${VAR} in the config literally, so ingress
        # keeps the empty values its template reads; nothing else keeps empty values.
        for name, service in self.services.items():
            empty = {key for key, value in (service.get("environment") or {}).items() if value in ("", None)}
            if name == "ingress":
                self.assertEqual(empty, {"NGINX_LISTEN_ADDRESS", "NGINX_LISTEN_OPTIONS"}, name)
            else:
                self.assertEqual(empty, set(), name)

    def test_ingress_runs_behind_traefik(self):
        env = self.services["ingress"]["environment"]
        self.assertEqual(env["PROVISION_SSL"], "false")
        self.assertEqual(env["HTTP_PROTOCOL"], "https")
        self.assertEqual(env["TRUSTED_PROXY_HOPS"], "2")
        self.assertEqual(env["HOST"], "${HOST:?}")

    def test_secrets_are_required_coolify_variables_with_frozen_names(self):
        # Renaming any of these after first deploy makes Coolify mint a new value.
        expected = {
            ("app", "ENCRYPTION_SECRET"): "${ENCRYPTION_SECRET:?}",
            ("app", "ONEUPTIME_SECRET"): "${ONEUPTIME_SECRET:?}",
            ("app", "DATABASE_PASSWORD"): "${DATABASE_PASSWORD:?}",
            ("app", "CLICKHOUSE_PASSWORD"): "${CLICKHOUSE_PASSWORD:?}",
            ("app", "VALKEY_PASSWORD"): "${VALKEY_PASSWORD:?}",
            ("app", "REGISTER_PROBE_KEY"): "${REGISTER_PROBE_KEY:?}",
            ("postgres", "POSTGRES_PASSWORD"): "${DATABASE_PASSWORD:?}",
            ("probe-1", "PROBE_KEY"): "${GLOBAL_PROBE_1_KEY:?}",
            ("global-runner", "ONEUPTIME_RUNNER_KEY"): "${GLOBAL_RUNNER_KEY:?}",
        }
        for (service, key), value in expected.items():
            self.assertEqual(self.services[service]["environment"][key], value, f"{service}.{key}")

    def test_probe_reaches_oneuptime_through_the_public_url(self):
        self.assertEqual(self.services["probe-1"]["environment"]["ONEUPTIME_URL"], "https://${HOST:?}")

    def test_oneuptime_images_are_pinned_to_one_version(self):
        for name in ("app", "ingress", "probe-1", "global-runner"):
            self.assertRegex(self.services[name]["image"], r":\$\{APP_TAG:-\d+\.\d+\.\d+\}$", name)

    def test_every_container_caps_its_logs(self):
        for name, service in self.services.items():
            self.assertTrue(service.get("logging", {}).get("options", {}).get("max-size"), name)


class CoreBundleTest(BundleContract, unittest.TestCase):
    BUNDLE = CORE

    def test_core_bundle_has_no_backups(self):
        self.assertFalse([name for name in self.services if name.startswith("backup-")])
        self.assertNotIn("S3_", CORE.read_text())
        self.assertNotIn("docker-volume-backup", CORE.read_text())


class BackupsBundleTest(BundleContract, unittest.TestCase):
    BUNDLE = WITH_BACKUPS

    def test_postgres_is_dumped_daily_to_s3(self):
        postgres, backup = self.services["postgres"], self.services["backup-postgres"]
        self.assertRegex(backup["image"], r"^offen/docker-volume-backup:v\d+\.\d+\.\d+$")
        self.assertEqual(backup["environment"]["BACKUP_CRON_EXPRESSION"], "0 2 * * *")
        self.assertIn("pg_dump", postgres["labels"]["docker-volume-backup.archive-pre"])
        self.assertIn("-Fc", postgres["labels"]["docker-volume-backup.archive-pre"])
        # The sidecar only runs the dump hook in containers carrying its exec label.
        self.assertEqual(postgres["labels"]["docker-volume-backup.exec-label"], backup["environment"]["EXEC_LABEL"])
        # The dump lands on a volume the sidecar archives.
        dump_volume = next(v["source"] for v in postgres["volumes"] if v["target"] == "/dumps")
        self.assertIn(dump_volume, [v["source"] for v in backup["volumes"] if v["target"].startswith("/backup")])

    def test_clickhouse_volume_is_backed_up_weekly_while_stopped(self):
        clickhouse, backup = self.services["clickhouse"], self.services["backup-clickhouse"]
        self.assertRegex(backup["image"], r"^offen/docker-volume-backup:v\d+\.\d+\.\d+$")
        self.assertEqual(backup["environment"]["BACKUP_CRON_EXPRESSION"], "0 3 * * 0")
        self.assertEqual(backup["environment"]["BACKUP_RETENTION_DAYS"], "28")
        self.assertEqual(
            clickhouse["labels"]["docker-volume-backup.stop-during-backup"],
            backup["environment"]["BACKUP_STOP_DURING_BACKUP_LABEL"],
        )
        self.assertIn("clickhouse", [v["source"] for v in backup["volumes"] if v["target"].startswith("/backup")])

    def test_backups_go_to_s3_under_their_own_prefix(self):
        for name in ("backup-postgres", "backup-clickhouse"):
            env = self.services[name]["environment"]
            self.assertEqual(env["AWS_S3_BUCKET_NAME"], "${S3_BUCKET:?}", name)
            self.assertEqual(env["AWS_S3_PATH"], "${BACKUP_PREFIX:-oneuptime}", name)
            self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "${S3_SECRET_ACCESS_KEY:?}", name)


if __name__ == "__main__":
    unittest.main()
