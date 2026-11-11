#!/usr/bin/env python3
"""Static check for the Coolify-ready OneUptime compose bundle.

Usage: scripts/check.py [docker-compose.yml]

Exits non-zero and prints one line per problem when the bundle breaks a
contract from the spec (.scratch/oneuptime-coolify/spec.md).
"""

import re
import sys
from pathlib import Path

from common import compose_config, report


def load(compose_path):
    """Parse the compose file with Docker Compose itself, without interpolating."""
    return compose_config([compose_path], "--no-interpolate", "--no-path-resolution")


def split_image(image):
    """Split an image reference into (repository, tag), allowing a ${VAR:-default} tag."""
    variable = image.find("${")
    slash = image.rfind("/", 0, variable if variable != -1 else len(image))
    name, _, tag = image[slash + 1:].partition(":")
    return image[:slash + 1] + name, tag


def image_problems(name, image):
    repository, tag = split_image(image)
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


def as_text(command):
    """A command or healthcheck test as one string, whether written as a string or a list."""
    if isinstance(command, str):
        return command
    return " ".join(command or [])


def secret_problems(name, service):
    problems = []
    for key, value in (service.get("environment") or {}).items():
        if SECRET_NAME.search(key) and "PUBLIC" not in key and is_literal(value):
            problems.append(f"{name}: {key} holds a literal value; use a Coolify variable")
    commands = {
        "command": as_text(service.get("command")),
        "healthcheck": as_text((service.get("healthcheck") or {}).get("test")),
    }
    for field, text in commands.items():
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
    return report(check_compose(compose_path))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
