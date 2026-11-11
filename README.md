# vigil: OneUptime on Coolify

Self-hosted [OneUptime](https://oneuptime.com) (uptime monitors, status pages, incidents, on-call, optional telemetry), deployed as a Docker Compose application on our Coolify.

Coolify's Traefik terminates TLS; OneUptime's `ingress` runs behind it on container port 7849. Single server, no HA. The spec is `.scratch/oneuptime-coolify/spec.md`.

**Current bundle: OneUptime 14.0.107**, rendered from upstream tag `14.0.107`.

## What's here

| Path | What it is |
| --- | --- |
| `docker-compose.yml` | The bundle Coolify deploys. **Generated: don't edit by hand.** |
| `overrides.env` | Our settings over upstream's `config.example.env`, including `APP_TAG` |
| `backups.yml` | Backup sidecars merged into the bundle |
| `Clickhouse/`, `packages/Probe/` | Files the compose bind-mounts, copied from upstream |
| `scripts/render.py` | Rebuilds `docker-compose.yml` from upstream |
| `scripts/check.py` | Static check of the bundle |
| `scripts/smoke.py` | Smoke check of a running deploy |
| `tests/` | Tests for the checks and the bundle, plus the local smoke test |

## Everyday commands

```sh
make test          # unit tests + bundle contract tests (needs Docker)
make check         # static check of docker-compose.yml
make render        # re-render docker-compose.yml from upstream at APP_TAG
make smoke-local   # start the core services locally and smoke check them
scripts/smoke.py https://uptime.<our-domain>   # after every deploy/upgrade
```

The repo needs only Python 3 and Docker Compose v2, with no Python packages.

## How the bundle is rendered

`scripts/render.py` clones upstream at the tag named by `APP_TAG`, then:

1. **Flattens the compose.** Docker Compose resolves upstream's `docker-compose.yml` (with `extends`, anchors and merges) together with `backups.yml`. It interpolates upstream's `config.example.env` overlaid with `overrides.env`.
2. **Keeps some values as variables.** The variables Coolify must own (below) go in as sentinels and come back out as `${VAR:?}` or `${VAR:-default}`, so no secret is ever committed.
3. **Removes what Coolify manages.** Published ports, `container_name`, the custom network and fixed volume names all go. Volume names must stay unfixed so each Coolify resource gets its own volumes.
4. **Copies the mounted files.** Every file the compose bind-mounts is copied from upstream at the same relative path.
5. **Checks the result.** It writes `docker-compose.yml` and runs `scripts/check.py`.

`scripts/check.py` fails on any of these:
- leftover `extends`, anchors or merge keys
- published `ports`, `container_name`, or a custom network
- a OneUptime image not tagged from `APP_TAG`, or another image that isn't pinned. "Pinned" only means not empty and not `latest`: upstream's `postgres:15`, `clickhouse-server:26.7` and `valkey:9.1-alpine` still float across patch releases.
- a literal secret in an environment variable or a `--requirepass`/`-a` argument
- a bind-mount source missing from the repo
- a host-network service pointed at a compose service name

## Coolify variables

Set these in the resource's **Environment Variables** tab. Coolify refuses to deploy while a required one is empty.

**Never rename or regenerate a secret after the first deploy.** Coolify would hand out a new value. A new `ENCRYPTION_SECRET` makes stored secrets and sign-in tokens unreadable for good. Save every value in the password manager before the first deploy.

| Variable | Required | Value |
| --- | --- | --- |
| `HOST` | yes | `uptime.<our-domain>` (no scheme) |
| `ONEUPTIME_SECRET` | yes | `openssl rand -hex 32` |
| `ENCRYPTION_SECRET` | yes | `openssl rand -hex 32`, **unrecoverable if lost** |
| `DATABASE_PASSWORD` | yes | `openssl rand -hex 32` |
| `CLICKHOUSE_PASSWORD` | yes | `openssl rand -hex 32` |
| `VALKEY_PASSWORD` | yes | `openssl rand -hex 32` |
| `REGISTER_PROBE_KEY` | yes | `openssl rand -hex 32` |
| `GLOBAL_PROBE_1_KEY` | yes | `openssl rand -hex 32` |
| `GLOBAL_RUNNER_KEY` | yes | `openssl rand -hex 32` |
| `S3_ENDPOINT` | yes | S3 host without scheme, e.g. `s3.eu-central-1.amazonaws.com` |
| `S3_BUCKET` | yes | The bucket Coolify already backs up to |
| `S3_ACCESS_KEY_ID` | yes | Key with write access to `S3_BUCKET/BACKUP_PREFIX` |
| `S3_SECRET_ACCESS_KEY` | yes | Secret for that key |
| `APP_TAG` | no (default in bundle) | Leave unset; upgrades change the default via a PR |
| `BACKUP_PREFIX` | no (default `oneuptime`) | Path in the bucket; override only for the restore drill |
| `BACKUP_SCOPE` | no (default `oneuptime`) | Label scope for backup hooks; override only for the restore drill |

These are plain variables rather than Coolify magic variables (`SERVICE_PASSWORD_*`), so their values are visible and copyable on day one.

## Deploy (Phase 2)

Prerequisites, all outside this repo:
- a dedicated server added to Coolify, with its proxy (Traefik) running
- DNS for `uptime.` (and `status.`)
- provider firewall open only on 22/80/443
- SMTP credentials, the S3 bucket, a password-manager entry
- this repo pushed to a private remote and connected to Coolify

1. In Coolify, create the resource: Project "Monitoring" → environment `production` → New Resource → Private repository → this repo. Choose Build Pack **Docker Compose** with compose path `/docker-compose.yml`.
2. Set a domain on the **`ingress` service only**: `https://uptime.<our-domain>:7849`. The `:7849` tells Coolify which container port to route to; Traefik still serves 443.
3. Fill in the variables above and save them to the password manager.
4. Check Persistent Storage: the `postgres`, `clickhouse` and `postgres-dump` volumes should be listed.
5. Deploy. First boot runs migrations; follow the `app` logs for several minutes.
6. Run `scripts/smoke.py https://uptime.<our-domain>`. It checks the TLS certificate, that `/version` matches `APP_TAG`, and that `/status/ready` returns 200.

Compose resolves the probe's seccomp profile path relative to the compose file, not the working directory. This was verified locally with Compose v5.6, so it should hold however Coolify invokes Compose.

**Fallback:** if relative bind mounts give trouble, create a Service → Docker Compose Empty, paste `docker-compose.yml`, and recreate the files under `Clickhouse/` and `packages/Probe/` as file mounts in the Storages tab.

## First boot (Phase 3)

Do these in order, before sharing the URL:

1. **Register the admin account immediately.** The first account to register becomes admin.
2. Admin Dashboard → Settings → Authentication: turn off "Let people sign up" and emailed sign-in links. Invite teammates instead.
3. Settings → Notifications → Emails: configure SMTP and send a test.
4. Confirm `probe-1` is connected. Create one HTTP monitor on a known-good endpoint and one that must fail, and check that the alert reaches email and our chat/on-call channel.
5. Set telemetry retention per project before sending any telemetry. Send OpenTelemetry over **OTLP/HTTP to `https://uptime.<our-domain>/otlp`**; OTLP/gRPC doesn't work behind Traefik.
6. For each custom status-page domain, add it both as an extra domain on `ingress` in Coolify and in the status page's settings.
7. Add an external check on `https://uptime.<our-domain>` from outside this server.
8. Check which edition (Community or Enterprise) includes SAML/OIDC before promising SSO.

## Backups (Phase 4)

| What | Sidecar | Schedule | Kept | S3 path |
| --- | --- | --- | --- | --- |
| Postgres (`pg_dump -Fc`) | `backup-postgres` | daily 02:00 | 56 days | `$BACKUP_PREFIX/postgres-*.tar.gz` |
| ClickHouse volume (ClickHouse stopped during copy) | `backup-clickhouse` | Sundays 03:00 | 28 days | `$BACKUP_PREFIX/clickhouse-*.tar.gz` |
| Secrets | password manager | at first deploy | forever | n/a |

Both sidecars are `offen/docker-volume-backup` and mount the Docker socket read-only. They need it to run `pg_dump` inside `postgres` and to stop and restart `clickhouse`.

While the ClickHouse backup runs, the sidecar builds the archive on local disk before uploading it, so the ClickHouse volume briefly needs about double its size in free space. This matters once telemetry is on; the disk alert should leave room for it.

`offen/docker-volume-backup` supports a single retention window, so the Postgres backup keeps 56 days of dailies. That's a superset of the spec's daily×14 + weekly×8; the dumps are small.

To take a backup now: `docker exec <backup-postgres container> backup`.

### Restore drill

Run it before go-live and after any change to `backups.yml`. These commands haven't been run yet: correct them during the first drill.

**The drill's database is a copy of production:** same monitors, SMTP settings and on-call policies. A running drill can send real alerts. Use a different server if you can, warn on-call before you start, and keep it short.

1. Deploy this repo as a second, temporary Coolify resource. Use the same variables as production except:
    - `BACKUP_SCOPE=oneuptime-drill` and `BACKUP_PREFIX=oneuptime-drill`. Without these, production's hooks would act on the drill's containers.
    - `HOST` set to a throwaway hostname.

   Keep production's `ENCRYPTION_SECRET` and `ONEUPTIME_SECRET`, or the restored data can't be decrypted.

   As soon as it's up, stop the drill's monitoring so it can't run production's monitors. On the same server its host-network probe would also clash with production's port:
   ```sh
   docker stop <drill probe-1 container> <drill global-runner container>
   ```
2. Download the latest `postgres-*.tar.gz` from the bucket and extract it; it contains `backup/postgres-dump/oneuptime.dump`.
3. Restore into the drill's Postgres:
   ```sh
   docker cp backup/postgres-dump/oneuptime.dump <drill postgres container>:/tmp/oneuptime.dump
   docker exec <drill postgres container> pg_restore --clean --if-exists --no-owner -U postgres -d oneuptimedb /tmp/oneuptime.dump
   ```
4. Restart the drill's `app`, log in, and confirm monitors and status pages are there.
5. Optionally restore ClickHouse (telemetry history) too:
   ```sh
   docker stop <drill clickhouse container>
   # The archive holds backup/clickhouse/<volume contents>.
   docker run --rm -v <drill clickhouse volume>:/restore -v "$PWD":/archive alpine \
     sh -c 'rm -rf /restore/* && tar -xzf /archive/clickhouse-<date>.tar.gz -C /restore --strip-components 2'
   docker start <drill clickhouse container>
   ```
6. Delete the temporary resource and its volumes.

## Upgrades (Phase 5)

Every 1–2 weeks, one major version at a time. Minor and patch versions can be skipped.

1. Confirm last night's Postgres backup landed in S3, or take one now.
2. Read the release notes and upgrading guide for any majors in between.
3. Bump `APP_TAG` in `overrides.env`, then run `make render && make test`.
4. Review the diff of `docker-compose.yml` and the copied files. A tag-only change touches only the image defaults. Anything else means upstream changed structure (new services, variables or mounted files), so review it closely.
5. Merge, redeploy in Coolify, then run `scripts/smoke.py https://uptime.<our-domain>` and check the `app` logs for migration errors.

**Rollback:** redeploy the previous commit. If the new version already ran migrations, also restore Postgres from the pre-upgrade dump.

## Must-knows

- Never rename or regenerate a secret variable after the first deploy.
- Claim the admin account before sharing the URL.
- The probe uses host networking and reaches OneUptime via `https://${HOST}`. The runner talks to `app` internally.
- A missing bind-mounted file stops its container, and `make check` catches it.
- OTLP/gRPC doesn't work behind Traefik; use OTLP/HTTP at `/otlp`.
- ClickHouse disk is what fills up. Set retention before enabling telemetry, and alert on disk.
- Never run OneUptime's own installer on a Coolify host; it fights Traefik for 80/443.
