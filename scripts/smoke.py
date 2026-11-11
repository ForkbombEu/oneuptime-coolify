#!/usr/bin/env python3
"""Smoke check a running OneUptime through its public URL.

Usage: scripts/smoke.py URL [--expect-version VERSION]

Run after every deploy and upgrade, e.g. scripts/smoke.py https://uptime.example.com.
The expected version defaults to APP_TAG in overrides.env. Exits non-zero and
prints one line per problem.
"""

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request

from common import OVERRIDES, read_env, report


class Unreachable(Exception):
    """The host couldn't be reached at all; no point checking further paths."""


def fetch(base_url, path):
    """GET a path and return (status, parsed JSON or None)."""
    try:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=15) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise Unreachable(f"{base_url}: TLS certificate is invalid ({error.reason.verify_message})")
        raise Unreachable(f"{base_url}: unreachable ({error.reason})")
    except (TimeoutError, ConnectionError) as error:
        raise Unreachable(f"{base_url}: unreachable ({error})")
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def smoke(base_url, expected_version):
    base_url = base_url.rstrip("/")
    problems = []
    try:
        status, body = fetch(base_url, "/version")
        if status != 200:
            problems.append(f"/version answered {status}")
        elif not isinstance(body, dict):
            problems.append("/version didn't return JSON; is the request reaching OneUptime's ingress?")
        elif body.get("version") != expected_version:
            problems.append(f"/version reports {body.get('version')}, expected {expected_version}")
        status, _ = fetch(base_url, "/status/ready")
        if status != 200:
            problems.append(f"/status/ready answered {status}; the app isn't ready")
    except Unreachable as error:
        problems.append(str(error))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--expect-version")
    args = parser.parse_args()
    return report(smoke(args.url, args.expect_version or read_env(OVERRIDES)["APP_TAG"]))


if __name__ == "__main__":
    sys.exit(main())
