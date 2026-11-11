#!/usr/bin/env python3
"""Render OneUptime's official compose into the Coolify-ready docker-compose.yml.

Usage: scripts/render.py [--upstream DIR]

Without --upstream, clones github.com/OneUptime/oneuptime at the tag named by
APP_TAG in overrides.env. Then:

1. Docker Compose flattens upstream's compose (extends, anchors, merges),
   interpolating upstream's config.example.env overlaid with overrides.env.
   Variables that must stay Coolify variables are fed in as sentinels and
   turned back into ${VAR} references afterwards.
   backups.yml adds the backup sidecars.
2. Published ports, container names and the custom network are removed.
3. The files the compose mounts from the checkout are copied into this repo.
4. The result is written to docker-compose.yml and run through check.py.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check import check_compose, repo_file_sources

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "overrides.env"
BACKUPS = ROOT / "backups.yml"
OUTPUT = ROOT / "docker-compose.yml"
UPSTREAM_REPO = "https://github.com/OneUptime/oneuptime.git"

# Set in Coolify's Environment Variables tab. Rendered as ${VAR:?} so a deploy
# with one unset fails instead of starting with an empty secret.
# Never rename these after the first deploy: ENCRYPTION_SECRET can't be recovered.
REQUIRED = [
    "HOST",
    "ONEUPTIME_SECRET",
    "ENCRYPTION_SECRET",
    "DATABASE_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "VALKEY_PASSWORD",
    "REGISTER_PROBE_KEY",
    "GLOBAL_PROBE_1_KEY",
    "GLOBAL_RUNNER_KEY",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
]
# Coolify variables with a committed default (from overrides.env), rendered as ${VAR:-default}.
DEFAULTED = ["APP_TAG", "BACKUP_PREFIX", "BACKUP_SCOPE"]

SENTINEL = re.compile(r"VIGIL__([A-Z0-9_]+?)__")


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def clone_upstream(tag, into):
    subprocess.run(
        ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", tag, UPSTREAM_REPO, str(into)],
        check=True,
    )
    return into


def flatten(upstream):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST") if key in os.environ}
    env.update({name: f"VIGIL__{name}__" for name in REQUIRED + DEFAULTED})
    result = subprocess.run(
        [
            "docker", "compose",
            "--project-directory", str(upstream),
            "-f", str(upstream / "docker-compose.yml"),
            "-f", str(BACKUPS),
            "--env-file", str(upstream / "config.example.env"),
            "--env-file", str(OVERRIDES),
            "config", "--no-path-resolution", "--format", "json",
        ],
        capture_output=True, text=True, env=env, check=True,
    )
    return json.loads(result.stdout)


def clean(model):
    model.pop("name", None)
    model.pop("networks", None)
    for key in [k for k in model if k.startswith("x-")]:
        del model[key]
    # Compose derives these from the project name; Coolify must scope volumes per
    # resource, or the restore-drill resource would mount production's data.
    model["volumes"] = {name: {} for name in model.get("volumes", {})}
    for name, service in model["services"].items():
        for key in ("ports", "container_name", "networks"):
            service.pop(key, None)
        if "volumes" in service:
            service["volumes"] = [short_volume(v) for v in service["volumes"]]
        model["services"][name] = {k: v for k, v in service.items() if v is not None}
    return model


def short_volume(volume):
    source = volume["source"]
    if volume["type"] == "bind" and not source.startswith("/"):
        source = "./" + source
    return f"{source}:{volume['target']}" + (":ro" if volume.get("read_only") else "")


def restore_variables(value, defaults):
    if isinstance(value, dict):
        return {k: restore_variables(v, defaults) for k, v in value.items()}
    if isinstance(value, list):
        return [restore_variables(v, defaults) for v in value]
    if isinstance(value, str):
        def reference(match):
            name = match.group(1)
            return f"${{{name}:-{defaults[name]}}}" if name in DEFAULTED else f"${{{name}:?}}"
        return SENTINEL.sub(reference, value)
    return value


def to_yaml(value, indent=0):
    """Minimal YAML emitter: block style, every string double-quoted (JSON is valid YAML)."""
    pad = "  " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {scalar(item)}")
    else:
        for item in value:
            if isinstance(item, (dict, list)) and item:
                nested = to_yaml(item, indent + 1).lstrip()
                lines.append(f"{pad}- {nested}")
            else:
                lines.append(f"{pad}- {scalar(item)}")
    return "\n".join(lines)


def scalar(value):
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return json.dumps(value)


def copy_mounted_files(model, upstream):
    for service in model["services"].values():
        for source in repo_file_sources(service):
            target = ROOT / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(upstream / source, target)


def render(upstream):
    overrides = read_env(OVERRIDES)
    model = flatten(upstream)
    copy_mounted_files(model, upstream)
    model = restore_variables(clean(model), overrides)
    header = (
        f"# GENERATED by scripts/render.py from OneUptime {overrides['APP_TAG']}. Don't edit by hand:\n"
        "# change overrides.env or scripts/render.py and re-render.\n"
    )
    OUTPUT.write_text(header + to_yaml(model) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", type=Path, help="existing checkout of OneUptime at APP_TAG")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        upstream = args.upstream or clone_upstream(read_env(OVERRIDES)["APP_TAG"], Path(tmp) / "oneuptime")
        render(upstream.resolve())
    problems = check_compose(OUTPUT)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
