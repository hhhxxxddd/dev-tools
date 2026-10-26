from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli_errors import error_payload
from dev_tools.i18n import language_scope, message
from dev_tools.presentation import display_width, label, wrap_text
from dev_tools.projects.cli import _list_state, emit
from dev_tools.projects.config import parse_project
from dev_tools.projects.diagnostics import (
    MESSAGES,
    diagnostic,
    enrich,
    normalize,
    recovery_progress,
)
from dev_tools.projects.health import http_probe
from dev_tools.projects.logfiles import failure_codes
from dev_tools.projects.models import ProjectError
from tests import test_project_engine as engine_fixture


class DiagnosticTests(unittest.TestCase):
    def test_diagnostic_catalogs_and_recovery_stages_cover_both_languages(self):
        from dev_tools import i18n

        for language, stage in (("zh", "健康检查"), ("en", "Health check")):
            with self.subTest(language=language), language_scope(language):
                for source in MESSAGES.values():
                    i18n.t(source)
                    self.assertIn(source, i18n._catalogs[language])
                self.assertEqual(label("health"), stage)
                project = self.project()
                project["recovery"].update(stage="health", attempt=2, current_service="api")
                with contextlib.redirect_stdout(output := io.StringIO()):
                    emit(dict(project, action="status", healthy=False), False)
                self.assertIn(stage, output.getvalue())
                self.assertNotIn("health;", output.getvalue())

    def test_log_clues_are_bounded_to_this_attempt_and_never_retain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.log"
            path.write_bytes(b"ERR_OLD_ATTEMPT\n")
            start = path.stat().st_size
            with path.open("ab") as stream:
                stream.write(b"PermissionError: token=SECRET\nERR_NATIVE_LOAD\n")
            self.assertEqual(failure_codes(path, start), ["PermissionError", "ERR_NATIVE_LOAD"])
            path.unlink()
            self.assertEqual(failure_codes(path, 0), [])

    def project(self):
        return {
            "name": "example",
            "environment": "wsl",
            "ready": False,
            "prepared": True,
            "desired_running": True,
            "services": {
                "api": {
                    "phase": "running",
                    "health": "unhealthy",
                    "required": True,
                    "diagnostics": [
                        diagnostic("health.http_status", evidence={"http_status": 503})
                    ],
                }
            },
            "recovery": {
                "operation": "prepare",
                "error": "timeout",
                "error_detail": {
                    "source": "service {name} did not become ready before timeout",
                    "parameters": {"name": "api"},
                },
            },
        }

    def test_native_health_failure_deduplicates_timeout_and_caller_access(self):
        project = self.project()
        project["windows_access"] = {
            "checks": [
                {"service": "api", "reachable": False, "stage": "http", "code": "http_status"}
            ]
        }
        with language_scope("en"):
            result = enrich(project)
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["health.http_status"])
        self.assertIn("503", result["summary"])
        self.assertNotIn("tunnel", result["summary"])

    def test_optional_failure_does_not_become_blocking_and_stop_suppresses_stale_error(self):
        project = self.project()
        project.update(recovery=None, ready=True)
        project["services"]["api"].update(required=False, phase="failed", exit_code=1)
        self.assertTrue(enrich(project)["ready"])
        self.assertTrue(all(d["severity"] == "warning" for d in enrich(project)["diagnostics"]))
        project.update(desired_running=False, recovery={"error": "old failure"})
        project["services"]["api"].update(phase="stopped", diagnostics=[])
        self.assertIsNone(enrich(project)["summary"])
        self.assertEqual(_list_state(project), "stopped")

    def test_old_exit_log_cannot_override_a_new_health_failure(self):
        project = self.project()
        project["services"]["api"]["last_failure"] = {
            "exit_code": 7,
            "at": 1,
            "log_codes": ["ERR_OLD_FAILURE"],
        }
        facts = enrich(project)["diagnostics"]
        self.assertEqual([fact["code"] for fact in facts], ["health.http_status"])

    def test_new_attempt_keeps_last_failure_and_success_clears_it(self):
        for environment in ("windows", "wsl"):
            with tempfile.TemporaryDirectory() as directory:
                engine = engine_fixture.EngineContractTests().fixture(Path(directory), environment)
                fact = diagnostic(
                    "health.http_status", service="api", evidence={"http_status": 503}
                )
                engine.save(
                    recovery={"operation": "prepare", "error": "unready", "diagnostics": [fact]}
                )
                engine.save(recovery={"operation": "prepare", "completed_tasks": []})
                self.assertEqual(engine.state()["recovery"]["last_failure"], [fact])
                self.assertEqual(engine.status()["diagnostics"][0]["code"], "health.http_status")
                engine.save(recovery=None)
                self.assertNotIn(
                    "health.http_status", [d["code"] for d in engine.status()["diagnostics"]]
                )

    def test_remote_unknown_fields_and_secret_evidence_are_not_rendered(self):
        result = normalize(
            [
                {"code": []},
                {"code": "invented"},
                {
                    "code": "health.http_status",
                    "stage": [],
                    "severity": [],
                    "evidence": {"http_status": 503, "token": "SECRET", "code": "password=SECRET"},
                },
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["evidence"], {"http_status": 503})
        self.assertNotIn("SECRET", json.dumps(result))

    def test_list_status_and_json_share_the_same_summary(self):
        for language in ("zh", "en"):
            with language_scope(language):
                project = enrich(self.project())
                with contextlib.redirect_stdout(output := io.StringIO()):
                    emit({"action": "list", "environment": "wsl", "projects": [project]}, False)
                self.assertIn(project["summary"], output.getvalue())
                project.update(action="status", healthy=False)
                with contextlib.redirect_stdout(output := io.StringIO()):
                    emit(project, False)
                self.assertIn(project["summary"], output.getvalue())
                with contextlib.redirect_stdout(output := io.StringIO()):
                    emit(project, True)
                self.assertEqual(json.loads(output.getvalue())["summary"], project["summary"])

    def test_list_summary_is_labeled_and_disambiguates_environments(self):
        for language, title, cause, waiting in (
            ("zh", "原因摘要：", "原因：", "30 秒后重试"),
            ("en", "Reason summaries:", "Cause:", "Retry in 30s"),
        ):
            project = self.project()
            project["recovery"]["next_retry_at"] = 130
            payload = {
                "action": "list",
                "environment": "all",
                "projects": [project, {**project, "environment": "windows"}],
            }
            with (
                self.subTest(language=language),
                language_scope(language),
                patch("dev_tools.projects.diagnostics.time.time", return_value=100),
                patch(
                    "dev_tools.projects.cli.shutil.get_terminal_size",
                    return_value=os.terminal_size((70, 24)),
                ),
                contextlib.redirect_stdout(output := io.StringIO()),
            ):
                emit(payload, False)
            text = output.getvalue()
            self.assertEqual(text.count(title), 1)
            self.assertIn("\n\n" + title + "\n", text)
            block = text.split(title + "\n", 1)[1]
            self.assertIn("  example [WSL / ", block)
            self.assertIn("  example [Windows / ", block)
            self.assertEqual(block.count("    " + cause), 2)
            self.assertIn("\n    " + waiting, block)
            self.assertNotIn("example: api:", text)
            self.assertTrue(all(display_width(line) <= 70 for line in block.splitlines()))
            with contextlib.redirect_stdout(output := io.StringIO()):
                emit(payload, True)
            self.assertEqual(json.loads(output.getvalue()), payload)

    def test_summary_wrapping_preserves_chinese_and_long_tokens(self):
        text = "原因：frontend: " + "连接失败，需要检查服务。" * 8 + " ERR_" + "A" * 80
        wrapped = wrap_text(text, 42, initial_indent="    ", subsequent_indent="      ")
        lines = wrapped.splitlines()
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(display_width(line) <= 42 for line in lines))
        self.assertTrue(all(line.startswith("      ") for line in lines[1:]))
        self.assertEqual("".join(wrapped.split()), "".join(text.split()))

    def test_healthy_list_has_no_empty_summary_section(self):
        project = self.project()
        project.update(ready=True, recovery=None)
        project["services"]["api"].update(health="healthy", diagnostics=[])
        with language_scope("en"), contextlib.redirect_stdout(output := io.StringIO()):
            emit({"action": "list", "environment": "wsl", "projects": [project]}, False)
        self.assertNotIn("Reason summaries:", output.getvalue())

    def test_operation_error_payload_retains_safe_specific_facts(self):
        error = ProjectError(
            message("service {name} did not become ready before timeout", name="api")
        )
        error.diagnostics = [
            diagnostic(
                "health.http_status",
                service="api",
                evidence={"http_status": 503, "secret": "SECRET"},
            )
        ]
        for action in ("start", "prepare", "build", "restart"):
            payload = error_payload(error, action=action)
            self.assertIn("503", payload["summary"])
            self.assertEqual(payload["diagnostics"][0]["code"], "health.http_status")
            self.assertNotIn("SECRET", json.dumps(payload))

    def test_read_only_status_never_updates_state_or_runs_tasks(self):
        for environment in ("windows", "wsl"):
            with tempfile.TemporaryDirectory() as directory:
                engine = engine_fixture.EngineContractTests().fixture(Path(directory), environment)
                engine.save(recovery={"operation": "prepare", "next_retry_at": 130, "attempt": 3})
                before = engine.state_path.read_bytes()
                with patch.object(engine.executor, "task") as task:
                    engine.status()
                self.assertEqual(before, engine.state_path.read_bytes())
                task.assert_not_called()
                self.assertEqual(engine.backend.events, [])
        progress = recovery_progress(
            {"operation": "prepare", "next_retry_at": 130, "attempt": 3}, now=100
        )
        self.assertEqual(progress["state"], "waiting")
        self.assertEqual(progress["retry_in_seconds"], 30)

    def server(self, status, body):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/ready"

    def test_http_error_is_preserved_without_leaking_body_and_details_are_opt_in(self):
        url = self.server(503, b'{"checks":{"store":false,"queue":"down"},"token":"SECRET"}')
        healthy, values = http_probe({"http": url})
        self.assertFalse(healthy)
        self.assertEqual(len(values), 1)
        healthy, values = http_probe(
            {"http": url, "details": ["/checks/store", "/checks/queue", "/token"]}
        )
        self.assertFalse(healthy)
        self.assertEqual(len(values), 3)
        self.assertNotIn("SECRET", json.dumps(values))

    def test_detail_parse_failure_does_not_change_successful_http_readiness(self):
        url = self.server(200, b"not json SECRET")
        healthy, values = http_probe({"http": url, "details": ["/checks/store"]})
        self.assertTrue(healthy)
        self.assertEqual(values[0]["severity"], "warning")
        self.assertFalse(http_probe({"http": url, "json": {"ready": True}})[0])

    def test_json_mismatch_reports_field_paths_and_target_without_response_values(self):
        url = self.server(200, b'{"status":{"ready":false},"secret":"PRIVATE"}')
        healthy, values = http_probe(
            {"http": url + "?token=PRIVATE", "json": {"status": {"ready": True}, "missing": None}}
        )
        self.assertFalse(healthy)
        self.assertEqual(
            {value["evidence"]["check"] for value in values}, {"/status/ready", "/missing"}
        )
        self.assertTrue(all(value["evidence"]["target"] == url for value in values))
        self.assertNotIn("PRIVATE", json.dumps(values))

    def test_invalid_detail_configuration_is_rejected(self):
        for paths in ("/x", ["bad"], ["/"], ["/x?token=secret"], ["/x"] * 17):
            with self.subTest(paths=paths), self.assertRaises(ProjectError):
                parse_project(
                    {
                        "schema": 1,
                        "name": "example",
                        "services": {
                            "api": {
                                "command": ["fixture"],
                                "health": {"http": "http://localhost/", "details": paths},
                            }
                        },
                    },
                    "windows",
                )
