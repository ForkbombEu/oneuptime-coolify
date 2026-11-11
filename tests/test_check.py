import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check import check_compose  # noqa: E402

CLEAN = """
services:
  app:
    image: oneuptime/app:${APP_TAG:-14.0.107}
    environment:
      ENCRYPTION_SECRET: ${ENCRYPTION_SECRET:?}
      LOG_LEVEL: ERROR
  postgres:
    image: postgres:15
    volumes:
      - postgres:/var/lib/postgresql/data
volumes:
  postgres:
"""


class CheckComposeTest(unittest.TestCase):
    def check(self, compose, files=()):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docker-compose.yml").write_text(textwrap.dedent(compose))
            for name in files:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x")
            return check_compose(root / "docker-compose.yml")

    def assertProblem(self, problems, *words):
        matching = [p for p in problems if all(w in p for w in words)]
        self.assertTrue(matching, f"no problem mentioning {words} in {problems}")

    def test_clean_bundle_has_no_problems(self):
        self.assertEqual(self.check(CLEAN), [])

    def test_published_port_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "  postgres:\n    image: postgres:15\n",
            "  postgres:\n    image: postgres:15\n    ports:\n      - \"127.0.0.1:5400:5432\"\n",
        ))
        self.assertProblem(problems, "postgres", "ports")

    def test_container_name_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "    image: postgres:15\n", "    image: postgres:15\n    container_name: db\n",
        ))
        self.assertProblem(problems, "postgres", "container_name")

    def test_leftover_extends_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "\nvolumes:\n", "\n  replica:\n    extends:\n      service: postgres\nvolumes:\n",
        ))
        self.assertProblem(problems, "extends")

    def test_yaml_anchors_are_a_problem(self):
        problems = self.check(CLEAN.replace(
            "    environment:\n", "    environment: &common\n",
        ).replace(
            "    image: postgres:15\n", "    image: postgres:15\n    environment:\n      <<: *common\n",
        ))
        self.assertProblem(problems, "anchor")

    def test_oneuptime_image_not_tagged_from_app_tag_is_a_problem(self):
        problems = self.check(CLEAN.replace("oneuptime/app:${APP_TAG:-14.0.107}", "oneuptime/app:release"))
        self.assertProblem(problems, "app", "APP_TAG")

    def test_untagged_or_latest_image_is_a_problem(self):
        problems = self.check(CLEAN.replace("postgres:15", "postgres:latest"))
        self.assertProblem(problems, "postgres", "pinned")

    BIND_MOUNTS = CLEAN.replace(
        "      - postgres:/var/lib/postgresql/data\n",
        "      - postgres:/var/lib/postgresql/data\n"
        "      - ./Clickhouse/config.d/cluster.xml:/etc/cluster.xml\n"
        "    security_opt:\n"
        "      - seccomp=./packages/Probe/seccomp_profile.json\n",
    )

    def test_present_bind_mount_sources_are_fine(self):
        problems = self.check(self.BIND_MOUNTS, files=[
            "Clickhouse/config.d/cluster.xml", "packages/Probe/seccomp_profile.json",
        ])
        self.assertEqual(problems, [])

    def test_missing_bind_mount_source_is_a_problem(self):
        problems = self.check(self.BIND_MOUNTS, files=["packages/Probe/seccomp_profile.json"])
        self.assertProblem(problems, "postgres", "Clickhouse/config.d/cluster.xml")

    def test_missing_seccomp_profile_is_a_problem(self):
        problems = self.check(self.BIND_MOUNTS, files=["Clickhouse/config.d/cluster.xml"])
        self.assertProblem(problems, "postgres", "seccomp_profile.json")

    def test_literal_secret_in_environment_is_a_problem(self):
        problems = self.check(CLEAN.replace("${ENCRYPTION_SECRET:?}", "s3cr3t-value"))
        self.assertProblem(problems, "app", "ENCRYPTION_SECRET")

    def test_empty_optional_secret_is_fine(self):
        problems = self.check(CLEAN.replace(
            "      LOG_LEVEL: ERROR\n", "      LOG_LEVEL: ERROR\n      SLACK_APP_CLIENT_SECRET: \"\"\n",
        ))
        self.assertEqual(problems, [])

    def test_literal_password_in_command_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "    image: postgres:15\n",
            "    image: postgres:15\n    command: valkey-server --requirepass \"hunter2\" --save \"\"\n",
        ))
        self.assertProblem(problems, "postgres", "requirepass")

    def test_literal_password_in_cli_healthcheck_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "    image: postgres:15\n",
            "    image: postgres:15\n    healthcheck:\n      test: [\"CMD\", \"valkey-cli\", \"-a\", \"hunter2\", \"ping\"]\n",
        ))
        self.assertProblem(problems, "postgres", "-a")

    def test_shell_variable_password_in_command_is_fine(self):
        problems = self.check(CLEAN.replace(
            "    image: postgres:15\n",
            "    image: postgres:15\n    command: sh -c 'uname -a && valkey-server --requirepass \"$$VALKEY_PASSWORD\"'\n",
        ))
        self.assertEqual(problems, [])

    PROBE = CLEAN.replace("\nvolumes:\n", """
  probe-1:
    image: oneuptime/probe:${APP_TAG:-14.0.107}
    network_mode: host
    environment:
      ONEUPTIME_URL: https://${HOST:?}
volumes:
""")

    def test_host_network_service_on_public_url_is_fine(self):
        self.assertEqual(self.check(self.PROBE), [])

    def test_host_network_service_on_service_name_is_a_problem(self):
        problems = self.check(self.PROBE.replace("https://${HOST:?}", "http://app:3002"))
        self.assertProblem(problems, "probe-1", "app")

    def test_custom_network_is_a_problem(self):
        problems = self.check(CLEAN.replace(
            "    image: postgres:15\n", "    image: postgres:15\n    networks:\n      - oneuptime\n",
        ) + "networks:\n  oneuptime:\n    driver: bridge\n")
        self.assertProblem(problems, "network", "oneuptime")


if __name__ == "__main__":
    unittest.main()
