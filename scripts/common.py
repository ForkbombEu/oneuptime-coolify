"""Helpers shared by render.py, check.py and smoke.py."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "overrides.env"


def read_env(path):
    """Parse a KEY=value env file, skipping comments and blank lines."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def compose_config(compose_files, *flags, project_directory=None, env_files=(), env=None):
    """Parse compose files with Docker Compose itself and return the model as a dict."""
    project_directory = project_directory or Path(compose_files[0]).parent
    result = subprocess.run(
        [
            "docker", "compose",
            "--project-directory", str(project_directory),
            *[arg for path in compose_files for arg in ("-f", str(path))],
            *[arg for path in env_files for arg in ("--env-file", str(path))],
            "config", *flags, "--format", "json",
        ],
        capture_output=True, text=True, env=env, check=True,
    )
    return json.loads(result.stdout)


def report(problems, stream=sys.stdout):
    """Print one line per problem; return the process exit code."""
    for problem in problems:
        print(problem, file=stream)
    return 1 if problems else 0
