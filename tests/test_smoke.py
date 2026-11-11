import json
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from smoke import smoke
from tests.helpers import ProblemAssertions


class FakeOneUptime:
    """Answers like OneUptime's ingress: /version and /status/ready."""

    def __init__(self, version="14.0.107", ready=True, version_status=200, version_raw=None):
        self.version, self.ready = version, ready
        self.version_status, self.version_raw = version_status, version_raw
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/version" and fake.version_raw is not None:
                    self.reply(200, fake.version_raw)
                elif self.path == "/version":
                    self.reply(fake.version_status, {"version": fake.version, "commit": "abc123"})
                elif self.path == "/status/ready":
                    self.reply(200 if fake.ready else 503, {"status": "ok" if fake.ready else "down"})
                else:
                    self.reply(404, {})

            def reply(self, status, body):
                payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class SmokeTest(ProblemAssertions, unittest.TestCase):
    def test_healthy_deploy_on_the_pinned_version_passes(self):
        with FakeOneUptime() as fake:
            self.assertEqual(smoke(fake.url, "14.0.107"), [])

    def test_wrong_version_is_a_problem(self):
        with FakeOneUptime(version="14.0.105") as fake:
            self.assertProblem(smoke(fake.url, "14.0.107"), "14.0.105", "14.0.107")

    def test_not_ready_is_a_problem(self):
        with FakeOneUptime(ready=False) as fake:
            self.assertProblem(smoke(fake.url, "14.0.107"), "/status/ready", "503")

    def test_version_endpoint_error_status_is_a_problem(self):
        with FakeOneUptime(version_status=404) as fake:
            self.assertProblem(smoke(fake.url, "14.0.107"), "/version", "404")

    def test_non_json_version_is_a_problem(self):
        with FakeOneUptime(version_raw="<html>Coolify: no available server</html>") as fake:
            self.assertProblem(smoke(fake.url, "14.0.107"), "/version", "JSON")

    def test_unreachable_host_is_a_problem(self):
        with FakeOneUptime() as fake:
            url = fake.url
        self.assertProblem(smoke(url, "14.0.107"), "unreachable")

    def test_invalid_certificate_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp, FakeOneUptime() as fake:
            cert = Path(tmp) / "cert.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=127.0.0.1", "-keyout", str(cert), "-out", str(cert)],
                check=True, capture_output=True,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert)
            fake.server.socket = context.wrap_socket(fake.server.socket, server_side=True)
            url = fake.url.replace("http://", "https://")
            self.assertProblem(smoke(url, "14.0.107"), "certificate")


if __name__ == "__main__":
    unittest.main()
