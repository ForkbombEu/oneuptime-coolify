#!/usr/bin/env python3
"""Static check for the Coolify-ready OneUptime compose bundle.

Usage: scripts/check.py [docker-compose.yml]

Exits non-zero and prints one line per problem when the bundle breaks a
contract from the spec (.scratch/oneuptime-coolify/spec.md).
"""

import json
import re
import subprocess
import sys
from pathlib import Path


def load(compose_path):
    """Parse the compose file with Docker Compose itself, without interpolating."""
    result = subprocess.run(
        [
            "docker", "compose",
            "--project-directory", str(compose_path.parent),
            "-f", str(compose_path),
            "config", "--no-interpolate", "--no-path-resolution", "--format", "json",
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def image_problems(name, image):
    slash = image.rfind("/", 0, image.find("${") if "${" in image else len(image))
    repository, _, tag = image[slash + 1:].partition(":")
    repository = image[:slash + 1] + repository
    if repository.startswith("oneuptime/"):
        if not tag.startswith("${APP_TAG"):
            return [f"{name}: image {image} must take its tag from APP_TAG"]
    elif tag in ("", "latest"):
        return [f"{name}: image {image} is not pinned to a version"]
    return []


SECRET_NAME = re.compile(r"(SECRET|PASSWORD|PASSPHRASE|TOKEN|_KEY)$")
# valkey-server --requirepass X / valkey-cli -a X (and the redis equivalents).
PASSWORD_ARG = re.compile(
    r"(?:(?<!\S)--requirepass|(?:valkey|redis)-cli\b[^;&|]*?(?<!\S)-a)\s+[\"']?([^\"'\s]+)"
)


def is_literal(value):
    """A non-empty value with no compose (${VAR}) or shell ($VAR) reference in it."""
    return bool(value) and "$" not in value


def secret_problems(name, service):
    problems = []
    for key, value in (service.get("environment") or {}).items():
        if SECRET_NAME.search(key) and "PUBLIC" not in key and is_literal(value):
            problems.append(f"{name}: {key} holds a literal value; use a Coolify variable")
    for field in ("command", "healthcheck"):
        raw = service.get(field) or ""
        if field == "healthcheck":
            raw = raw.get("test", "") if raw else ""
        text = raw if isinstance(raw, str) else " ".join(raw)
        for match in PASSWORD_ARG.finditer(text):
            if is_literal(match.group(1)):
                flag = "--requirepass" if "--requirepass" in match.group(0) else "-a"
                problems.append(f"{name}: {field} passes a literal password ({flag})")
    return problems


def host_network_problems(name, service, services):
    """Host-network containers can't resolve compose service names."""
    problems = []
    for key, value in (service.get("environment") or {}).items():
        match = re.search(r"://([\w.-]+)", value or "")
        if match and match.group(1) in services:
            problems.append(
                f"{name}: {key} points at service '{match.group(1)}', unreachable from host "
                "networking; use https://${HOST}"
            )
    return problems


def repo_file_sources(service):
    """Relative paths of files the service needs from the repo checkout.

    Absolute bind sources (like the Docker socket) are host paths, not repo files.
    """
    for volume in service.get("volumes", []):
        if volume.get("type") == "bind" and not volume["source"].startswith("/"):
            yield volume["source"]
    for option in service.get("security_opt", []):
        if option.startswith("seccomp=") and option != "seccomp=unconfined":
            yield option.removeprefix("seccomp=")


def check_compose(compose_path):
    compose_path = Path(compose_path)
    model = load(compose_path)
    text = compose_path.read_text()
    problems = []
    if re.search(r"^\s*extends:", text, re.MULTILINE):
        problems.append("uses extends; the bundle must be one flat file")
    if re.search(r"(:|-)\s+[&*][\w-]+\s*$|^\s*<<:", text, re.MULTILINE):
        problems.append("uses YAML anchors or merge keys; the bundle must be one flat file")
    for network in model.get("networks", {}):
        if network != "default":
            problems.append(f"declares network '{network}'; Coolify manages networks")
    for name, service in model.get("services", {}).items():
        if service.get("ports"):
            problems.append(f"{name}: publishes ports; Traefik reaches services internally")
        if "container_name" in service:
            problems.append(f"{name}: sets container_name; Coolify names containers")
        problems += image_problems(name, service.get("image", ""))
        problems += secret_problems(name, service)
        if service.get("network_mode") == "host":
            problems += host_network_problems(name, service, model["services"])
        for source in repo_file_sources(service):
            if not (compose_path.parent / source).is_file():
                problems.append(f"{name}: mounts {source}, which is missing from the repo")
    return problems


def main(argv):
    compose_path = Path(argv[1] if len(argv) > 1 else "docker-compose.yml")
    problems = check_compose(compose_path)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
