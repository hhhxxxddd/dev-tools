from __future__ import annotations

import contextlib
import copy
import http.server
import io
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.cli_errors import TargetError
from dev_tools.projects.access import access_targets, probe, windows_access
from dev_tools.projects.cli import _list_state, emit, remote_wsl_status


class ProjectAccessTests(unittest.TestCase):
    def test_optional_client_access_does_not_block_shared_services(self):
        project = self.project()
        project["services"]["desktop"] = {
            "phase": "running",
            "health": "unhealthy",
            "required": False,
        }
        project["access_targets"].append(
            {"service": "desktop", "protocol": "tcp", "port": 9222, "path": "/"}
        )
        with patch(
            "dev_tools.projects.access.probe",
            side_effect=lambda target: {"reachable": target[1] != 9222, "target": str(target)},
        ):
            result = windows_access([project])[0]
        self.assertTrue(result["ready"])
        self.assertEqual(result["windows_access"]["state"], "reachable")
        self.assertFalse(result["windows_access"]["checks"][1]["reachable"])

    def project(self):
        return {
            "schema_version": 3,
            "action": "status",
            "environment": "wsl",
            "name": "demo",
            "prepared": True,
            "ready": True,
            "healthy": True,
            "services": {"web": {"phase": "running", "health": "healthy"}},
            "health_scope": "wsl-local",
            "access_targets": [{"service": "web", "protocol": "http", "port": 5175, "path": "/"}],
        }

    def service(self, argv, health, *, driver="process", workdir="."):
        return SimpleNamespace(
            command=SimpleNamespace(argv=tuple(argv), script=""),
            health=health,
            driver=driver,
            workdir=workdir,
        )

    def test_static_web_detection_reads_scripts_without_executing_project_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"scripts":{"dev":"vite --host 0.0.0.0"}}')
            spec = SimpleNamespace(
                services={
                    "web": self.service(["pnpm", "run", "dev"], {"tcp": 5175}),
                    "api": self.service(["{maven}", "spring-boot:run"], {"tcp": 48090}),
                    "custom": self.service(["custom-server"], {"tcp": 4321}),
                    "remote": self.service(["fixture"], {"http": "https://example.test/health"}),
                    "secure": self.service(
                        ["fixture"],
                        {"http": "https://user:password@localhost/health?token=secret#x"},
                    ),
                }
            )
            with patch("subprocess.run", side_effect=AssertionError("project code execution")):
                targets = access_targets(spec, root)
        self.assertEqual(
            [target["protocol"] for target in targets], ["http", "http", "tcp", "https"]
        )
        self.assertEqual(
            [target["service"] for target in targets], ["web", "api", "custom", "secure"]
        )
        self.assertEqual(targets[-1]["path"], "/health")
        self.assertNotIn("secret", json.dumps(targets))
        self.assertNotIn("password", json.dumps(targets))

    def test_corrupt_or_oversized_package_metadata_keeps_tcp_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = SimpleNamespace(
                services={"web": self.service(["npm", "run", "dev"], {"tcp": 1234})}
            )
            for content in ("null", "{broken", " " * (1024 * 1024 + 1)):
                (root / "package.json").write_text(content)
                self.assertEqual(access_targets(spec, root)[0]["protocol"], "tcp")

    def test_unprivileged_query_uses_source_metadata_when_native_cache_is_inaccessible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, workspace = root / "source", root / "native"
            source.mkdir()
            workspace.mkdir()
            (workspace / "package.json").write_text("{}")
            (source / "package.json").write_text('{"scripts":{"dev":"vite --host 0.0.0.0"}}')
            spec = SimpleNamespace(
                services={"web": self.service(["pnpm", "run", "dev"], {"tcp": 5175})}
            )
            original_open = os.open

            def guarded_open(path, *args, **kwargs):
                if path == workspace / "package.json":
                    raise PermissionError("root-owned cache")
                return original_open(path, *args, **kwargs)

            with patch("os.open", guarded_open):
                self.assertEqual(
                    access_targets(spec, workspace, source_root=source)[0]["protocol"], "http"
                )
            # Readable native metadata remains authoritative during source edits.
            (workspace / "package.json").write_text('{"scripts":{"dev":"custom-server"}}')
            self.assertEqual(
                access_targets(spec, workspace, source_root=source)[0]["protocol"], "tcp"
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX filesystem metadata")
    def test_package_symlinks_and_fifos_are_not_opened_by_status_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "package.json"
            outside = root / "outside.json"
            outside.write_text('{"scripts":{"dev":"vite"}}')
            spec = SimpleNamespace(services={"web": self.service(["pnpm", "dev"], {"tcp": 1234})})
            target.symlink_to(outside)
            with patch("os.open", side_effect=AssertionError("opened a metadata symlink")):
                self.assertEqual(access_targets(spec, root)[0]["protocol"], "tcp")
            target.unlink()
            os.mkfifo(target)
            with patch("os.open", side_effect=AssertionError("opened a metadata FIFO")):
                self.assertEqual(access_targets(spec, root)[0]["protocol"], "tcp")

    def test_windows_timeout_overrides_aggregate_health_but_preserves_runtime_facts(self):
        original = self.project()
        before = copy.deepcopy(original)
        with patch(
            "dev_tools.projects.access.probe",
            return_value={
                "target": "http://localhost:5175/",
                "reachable": False,
                "stage": "http",
                "code": "timeout",
            },
        ) as check:
            first, second = windows_access([original, {**original, "name": "other"}])
        check.assert_called_once()
        self.assertFalse(first["ready"])
        self.assertFalse(first["healthy"])
        self.assertTrue(first["runtime_ready"])
        self.assertTrue(first["runtime_healthy"])
        self.assertEqual(first["services"]["web"]["health"], "healthy")
        self.assertEqual(_list_state(first), "access-unavailable")
        self.assertEqual(second["windows_access"]["state"], "unreachable")
        self.assertEqual(original, before)

    def test_old_or_invalid_target_metadata_is_unchecked_without_network_calls(self):
        for targets in (
            None,
            [{}],
            [{"service": "web", "protocol": [], "port": 80, "path": "/"}],
            [{"service": "web", "protocol": "http", "port": 80, "path": "//elsewhere"}],
        ):
            value = self.project()
            value["access_targets"] = targets
            with patch("dev_tools.projects.access.probe") as check:
                result = windows_access([value])[0]
            check.assert_not_called()
            self.assertEqual(result["windows_access"]["state"], "unknown")
            self.assertIsNone(result["healthy"])
            self.assertEqual(_list_state(result), "access-unknown")

    def test_stopped_services_are_not_probed(self):
        value = self.project()
        value["services"]["web"]["phase"] = "stopped"
        with patch("dev_tools.projects.access.probe") as check:
            result = windows_access([value])[0]
        check.assert_not_called()
        self.assertEqual(result["windows_access"]["state"], "not-applicable")

    def serve(self, handler):
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        server = Server(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_real_accepting_but_silent_http_relay_is_not_healthy(self):
        release = threading.Event()

        class Silent(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(8192)
                release.wait(4)

        port = self.serve(Silent)
        self.addCleanup(release.set)
        started = time.monotonic()
        value = probe(("http", port, "/"))
        self.assertFalse(value["reachable"])
        self.assertEqual((value["stage"], value["code"]), ("http", "timeout"))
        self.assertLess(time.monotonic() - started, 3.8)

    def test_http_auth_response_proves_access_without_reading_a_body(self):
        class Auth(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401 if self.headers["Host"].startswith("localhost:") else 503)
                self.end_headers()

            def log_message(self, *_):
                pass

        port = self.serve(Auth)
        with patch("dev_tools.toolbox.network.resolve", side_effect=AssertionError("DNS child")):
            value = probe(("http", port, "/"))
        self.assertTrue(value["reachable"])
        self.assertEqual(value["http_status"], 401)

    def test_get_only_health_preserves_readiness_and_still_rejects_server_errors(self):
        for status in (200, 503):
            with self.subTest(status=status):
                requests = []

                class GetOnly(http.server.BaseHTTPRequestHandler):
                    response_status = status
                    received_requests = requests

                    def do_GET(self):
                        self.received_requests.append(
                            (self.command, self.path, self.headers["Host"])
                        )
                        self.send_response(self.response_status)
                        # A healthy response needs no body download, even when
                        # the advertised body is large and never arrives.
                        self.send_header("Content-Length", str(1024**3))
                        self.end_headers()

                    def log_message(self, *_):
                        pass

                port = self.serve(GetOnly)
                project = self.project()
                project["access_targets"][0].update(port=port, path="/health")
                before = copy.deepcopy(project)
                with (
                    patch("dev_tools.toolbox.network.resolve", side_effect=AssertionError("DNS")),
                    patch("subprocess.run", side_effect=AssertionError("project execution")),
                ):
                    value = windows_access([project])[0]
                self.assertEqual(value["ready"], status == 200)
                self.assertEqual(value["healthy"], status == 200)
                self.assertTrue(value["runtime_ready"])
                self.assertTrue(value["runtime_healthy"])
                check = value["windows_access"]["checks"][0]
                self.assertEqual(check["http_status"], status)
                self.assertEqual(check["reachable"], status == 200)
                self.assertEqual(requests, [("GET", "/health", f"localhost:{port}")])
                self.assertEqual(project, before)

    def test_remote_status_is_captured_and_rendered_with_windows_access(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(self.project()), "")
        with (
            patch("dev_tools.runtimes.router.forward_remote", return_value=result) as forward,
            patch(
                "dev_tools.projects.access.probe",
                return_value={
                    "target": "http://localhost:5175/",
                    "reachable": False,
                    "stage": "http",
                    "code": "timeout",
                },
            ),
            contextlib.redirect_stdout(output := io.StringIO()),
        ):
            self.assertEqual(remote_wsl_status(["status", "demo"], SimpleNamespace(), True), 0)
        self.assertEqual(forward.call_args.args, ("wsl", ["status", "demo", "--json"]))
        value = json.loads(output.getvalue())
        self.assertFalse(value["healthy"])
        with contextlib.redirect_stdout(human := io.StringIO()):
            emit(value, False)
        self.assertIn("localhost:5175", human.getvalue())
        self.assertIn("http/timeout", human.getvalue())

    def test_remote_status_rejects_wrong_environment_or_unsuccessful_status_payload(self):
        for environment, returncode in (("windows", 0), ("wsl", 1)):
            value = {**self.project(), "environment": environment}
            result = subprocess.CompletedProcess([], returncode, json.dumps(value), "")
            with (
                patch("dev_tools.runtimes.router.forward_remote", return_value=result),
                patch("dev_tools.projects.access.probe") as check,
                self.assertRaises(TargetError),
            ):
                remote_wsl_status(["status", "demo"], SimpleNamespace(), True)
            check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
