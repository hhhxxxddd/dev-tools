from __future__ import annotations

import copy
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.cli import _list_state
from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.execution import Executor
from dev_tools.projects.health import http_ready
from dev_tools.projects.models import ProjectError
from dev_tools.scanner import scan_project
from dev_tools.versions import normalize_version
from tests import test_project_engine as engine_fixture


class DesktopTests(unittest.TestCase):
    def test_rust_channel_whitespace_is_normalized_before_compatibility_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mise.toml").write_text('[tools]\nrust="nightly"\n')
            (root / "rust-toolchain.toml").write_text('[toolchain]\nchannel=" nightly "\n')
            self.assertEqual(normalize_version("rust", " nightly "), "nightly")
            self.assertFalse(scan_project(root).blocked)

    def test_native_source_and_workspace_identities_are_forwarded_without_business_defaults(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            executor = Executor(engine.spec, engine.binding, engine.backend)
            result = executor.user_environment(
                {
                    "DEV_TOOLS_SOURCE_ROOT": "spoofed",
                    "DEV_TOOLS_PROJECT_ROOT": "spoofed",
                    "APP_MODE": "shared",
                }
            )
            self.assertEqual(result["DEV_TOOLS_SOURCE_ROOT"], str(engine.binding.source))
            self.assertEqual(result["DEV_TOOLS_PROJECT_ROOT"], str(engine.binding.workspace))
            self.assertEqual(result["APP_MODE"], "shared")

    def test_cargo_workspace_toolchain_formats_and_invalid_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mise.toml").write_text('[tools]\nrust="1.99.1"\n')
            (root / "Cargo.toml").write_text(
                '[workspace]\nmembers=["deep/a/b/c/d/lib"]\n[workspace.package]\nrust-version="1.80"\n'
            )
            member = root / "deep/a/b/c/d/lib"
            member.mkdir(parents=True)
            (member / "Cargo.toml").write_text(
                '[package]\nname="lib"\nrust-version={workspace=true}\n'
            )
            self.assertFalse(scan_project(root).blocked)
            for filename, contents, blocked in (
                ("rust-toolchain", "1.99.1", False),
                ("rust-toolchain", '[toolchain]\nchannel="1.99.1"\n', False),
                ("rust-toolchain.toml", '[toolchain]\nchannel="1.99.1"\n', False),
                ("rust-toolchain.toml", "[toolchain]\nchannel=123\n", True),
                ("Cargo.toml", "[package]\nrust-version=123\n", True),
            ):
                with self.subTest(filename=filename, contents=contents):
                    path = root / filename
                    original = path.read_text() if path.exists() else None
                    path.write_text(contents)
                    self.assertEqual(scan_project(root).blocked, blocked)
                    if original is not None:
                        path.write_text(original)
                    else:
                        path.unlink()

    def fixture(self, environment):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        engine = engine_fixture.EngineContractTests().fixture(Path(temporary.name), environment)
        raw = copy.deepcopy(engine.spec.raw)
        raw["services"]["desktop"] = {
            "command": ["fixture"],
            "depends_on": ["web"],
            "required": False,
            "restart": "never",
        }
        self.configure(engine, raw)
        return engine

    def configure(self, engine, raw):
        (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
        engine._set_spec(parse_project(raw, engine.binding.environment))

    def test_close_reopen_browser_selection_idempotence_and_maintenance(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                engine.start()
                self.assertTrue(engine.status()["ready"])
                engine.backend.running.discard("desktop")
                self.assertTrue(engine.status()["ready"])
                self.assertEqual(_list_state(engine.status()), "running")
                self.assertFalse(engine.status()["services"]["desktop"]["required"])
                engine.backend.events.clear()
                engine.start(("web",))
                self.assertNotIn("start:desktop", engine.backend.events)
                raw = copy.deepcopy(engine.spec.raw)
                raw["services"]["api"]["env"] = {"MODE": "updated"}
                self.configure(engine, raw)
                engine.maintain()
                self.assertNotIn("desktop", engine.backend.running)
                self.assertTrue(engine.status()["ready"])
                engine.backend.events.clear()
                engine.start(("desktop",))
                engine.start(("desktop",))
                self.assertEqual(engine.backend.events.count("start:desktop"), 1)
                self.assertNotIn("start:web", engine.backend.events)
                self.assertNotIn("start:api", engine.backend.events)
                engine.stop()
                self.assertFalse(engine.backend.running)

    def test_optional_start_failure_keeps_shared_services_and_monitors(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            engine.backend.fail_start = "desktop"
            with self.assertRaisesRegex(ProjectError, "shared services remain running"):
                engine.start()
            self.assertTrue({"api", "web", "__watch"} <= engine.backend.running)
            self.assertTrue(engine.status()["ready"])
            self.assertFalse(engine.state().get("recovery"))
            engine.stop()
            self.assertFalse(engine.backend.running)

    def test_optional_dependency_of_a_required_service_is_still_required(self):
        engine = self.fixture("windows")
        raw = copy.deepcopy(engine.spec.raw)
        raw["services"]["desktop"]["depends_on"] = ["api"]
        raw["services"]["web"]["depends_on"] = ["desktop"]
        self.configure(engine, raw)
        engine.backend.fail_start = "desktop"
        with self.assertRaises(ProjectError):
            engine.start()
        self.assertFalse(engine.backend.running)

    def test_clean_client_exit_during_readiness_is_accepted(self):
        engine = self.fixture("windows")
        with patch.object(
            engine,
            "service_status",
            return_value={"phase": "stopped", "exit_code": 0, "ready": False},
        ):
            engine._wait_ready("desktop")

    def test_required_and_json_health_configuration_validation(self):
        invalid = (
            {"required": "false"},
            {"health": {"json": {"ready": True}}},
            {"health": {"http": "http://localhost/health", "json": {}}},
            {"health": {"http": "http://localhost/health", "json": {"number": float("inf")}}},
        )
        for fields in invalid:
            for platform in ("windows", "wsl"):
                with (
                    self.subTest(fields=fields, platform=platform),
                    self.assertRaises(ProjectError),
                ):
                    parse_project(
                        {
                            "schema": 1,
                            "name": "demo",
                            "services": {"app": {"command": ["fixture"], **fields}},
                        },
                        platform,
                    )

    def test_rust_tauri_metadata_and_msrv_are_scoped_without_executing_npm_scripts(self):
        for minimum, blocked in (("1.80", False), ("2.0", True)):
            with self.subTest(minimum=minimum), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "frontend/src-tauri").mkdir(parents=True)
                (root / ".local/review").mkdir(parents=True)
                (root / "dev-tools.toml").write_text(
                    render_toml(
                        {
                            "schema": 1,
                            "name": "demo",
                            "services": {
                                "desktop": {
                                    "command": ["npm", "run", "tauri:dev"],
                                    "workdir": "frontend",
                                    "tools": ["node", "rust"],
                                    "required": False,
                                }
                            },
                        }
                    )
                )
                (root / "mise.toml").write_text('[tools]\nnode="24"\nrust="1.99.1"\n')
                (root / "frontend/package.json").write_text('{"scripts":{"tauri:dev":"tauri dev"}}')
                (root / "frontend/src-tauri/Cargo.toml").write_text(
                    f'[package]\nname="fixture"\nrust-version="{minimum}"\n'
                )
                (root / ".local/review/rust-toolchain").write_text("1.10")
                with patch("subprocess.run", side_effect=AssertionError("project code executed")):
                    result = scan_project(root)
                self.assertEqual(result.blocked, blocked)
                self.assertEqual(result.tools["rust"].version, "1.99.1")
                self.assertIn("frontend/src-tauri/Cargo.toml", result.scope["files"])
                self.assertNotIn(".local/review/rust-toolchain", result.scope["files"])


class ReadinessTests(unittest.TestCase):
    def server(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            requests = 0

            def do_GET(self):
                type(self).requests += 1
                body = {"application": "fixture", "ready": True, "extra": "allowed"}
                if self.path == "/foreign":
                    body["application"] = "other"
                if self.path == "/wrong-type":
                    body["ready"] = 1
                self.send_response(302 if self.path == "/redirect" else 200)
                self.send_header("Location", "/health")
                self.end_headers()
                if self.path == "/slow":
                    for byte in json.dumps(body).encode():
                        try:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                        except OSError:
                            return
                        time.sleep(0.1)
                    return
                self.wfile.write(
                    b"invalid" if self.path == "/invalid" else json.dumps(body).encode()
                )

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        return server.server_address[1], Handler

    def test_direct_application_json_readiness_rejects_foreign_redirect_and_invalid_responses(self):
        port, _handler = self.server()
        for path, expected in (
            ("/health", True),
            ("/foreign", False),
            ("/wrong-type", False),
            ("/redirect", False),
            ("/invalid", False),
        ):
            with (
                self.subTest(path=path),
                patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}),
            ):
                self.assertEqual(
                    http_ready(
                        {
                            "http": f"http://127.0.0.1:{port}{path}",
                            "json": {"application": "fixture", "ready": True},
                        }
                    ),
                    expected,
                )

    def test_json_readiness_deadline_rejects_a_body_that_keeps_trickling(self):
        port, _handler = self.server()
        started = time.monotonic()
        self.assertFalse(
            http_ready({"http": f"http://127.0.0.1:{port}/slow", "json": {"ready": True}})
        )
        self.assertLess(time.monotonic() - started, 5)

    def test_existing_listener_is_never_adopted_or_terminated(self):
        port, handler = self.server()
        fixture = DesktopTests()
        engine = fixture.fixture("windows")
        self.addCleanup(fixture.doCleanups)
        raw = copy.deepcopy(engine.spec.raw)
        raw["services"]["api"]["health"] = {
            "tcp": port,
            "http": f"http://127.0.0.1:{port}/health",
            "json": {"application": "fixture", "ready": True},
        }
        (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
        engine._set_spec(parse_project(raw, "windows"))
        with self.assertRaisesRegex(ProjectError, "already listening"):
            engine.start()
        self.assertNotIn("start:api", engine.backend.events)
        self.assertEqual(handler.requests, 0)
        self.assertTrue(http_ready(raw["services"]["api"]["health"]))


if __name__ == "__main__":
    unittest.main()
