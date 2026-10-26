from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.cli_errors import emit_error, error_payload, request_scope
from dev_tools.i18n import language_scope
from dev_tools.projects.cli import emit
from dev_tools.projects.diagnostic_evidence import (
    failure_log,
    failure_messages,
    safe_message,
    safe_target,
)
from dev_tools.projects.diagnostics import describe, diagnostic, enrich
from dev_tools.projects.engine import _probe
from dev_tools.projects.execution import Executor
from dev_tools.projects.models import ProjectError
from dev_tools.projects.remote_evidence import remote_log_evidence
from dev_tools.runtimes.platforms.base import Backend
from tests import test_project_engine as engine_fixture


class EvidenceTests(unittest.TestCase):
    def project(self, phase="restarting"):
        return {
            "name": "fixture",
            "environment": "wsl",
            "ready": False,
            "healthy": False,
            "prepared": True,
            "services": {
                "api": {"phase": phase, "health": "unhealthy", "exit_code": 7, "required": True}
            },
        }

    def test_specific_access_failure_is_not_hidden_by_legacy_health_boolean(self):
        project = self.project("running")
        project["windows_access"] = {
            "state": "unreachable",
            "checks": [
                {
                    "service": "api",
                    "reachable": False,
                    "stage": "http",
                    "code": "http_status",
                    "http_status": 503,
                    "target": "http://user:PRIVATE@localhost:8021/ready?token=PRIVATE",
                }
            ],
        }
        value = enrich(project)
        self.assertEqual(value["diagnostics"][0]["code"], "access.unavailable")
        self.assertIn("503", value["summary"])
        self.assertIn("localhost:8021/ready", value["summary"])
        self.assertNotIn("PRIVATE", json.dumps(value["diagnostics"]))

    def test_reachable_access_does_not_claim_native_health_was_successful(self):
        project = self.project("running")
        project["windows_access"] = {
            "state": "reachable",
            "checks": [
                {
                    "service": "api",
                    "reachable": True,
                    "http_status": 400,
                    "target": "http://localhost:8021/",
                }
            ],
        }
        value = enrich(project)
        self.assertFalse(value["ready"])
        self.assertEqual(value["diagnostics"][0]["code"], "health.details_missing")
        self.assertEqual(value["diagnostics"][1]["code"], "access.observed")

    def test_exception_clues_are_redacted_bounded_and_select_deeper_messages(self):
        text = "Error: Outer failure\n at call()\nCaused by: example.CacheException: cache path is not writable. Repair it later.\n"
        values = failure_messages(text)
        self.assertIn("CacheException", values[0])
        self.assertNotIn("Repair", values[0])
        compiler = failure_messages("src/view.ts(12,4): error TS2307: Cannot find module 'library'")
        self.assertIn("src/view.ts(12,4)", compiler[0])
        self.assertIn("TS2307", compiler[0])
        for message in (
            'Error: password="SECRET WITH SPACES"',
            "Error: token=PRIVATE",
            "Error: invalid API key provided: PRIVATE",
            "Error: Bearer PRIVATE",
            "Error: cannot connect mysql://user:PRIVATE@host/db?key=PRIVATE",
            "Error: session_id: PRIVATE",
            "Error: token=FIRST password PRIVATE",
            "Error: Bearer FIRST; private key PRIVATE",
            "Error: already [redacted]; authorization PRIVATE",
        ):
            self.assertNotIn("PRIVATE", str(failure_messages(message)))
            self.assertNotIn("SECRET", str(failure_messages(message)))
        self.assertLessEqual(len(safe_message("Error: " + "x " * 1000)), 281)
        self.assertEqual(
            safe_target("https://u:PRIVATE@localhost:8081/ready?key=PRIVATE#fragment"),
            "https://localhost:8081/ready",
        )
        cleaned = safe_message("Error: token=FIRST password PRIVATE")
        self.assertEqual(safe_message(cleaned), cleaned)

    def test_unicode_log_limit_is_in_bytes_and_excludes_old_errors(self):
        text = "Error: OLD_OUTSIDE_LIMIT\n" + "中" * 5000 + "\nError: CURRENT_FAILURE\n"
        values = failure_messages(text)
        self.assertIn("CURRENT_FAILURE", str(values))
        self.assertNotIn("OLD_OUTSIDE_LIMIT", str(values))

    def test_native_reader_ignores_stale_logs_and_previous_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.log"
            path.write_text("Error: OLD_FAILURE\n", encoding="utf-8")
            offset = path.stat().st_size
            with path.open("a", encoding="utf-8") as stream:
                stream.write("ValueError: NEW_FAILURE\n")
            now = time.time()
            values = failure_log(
                path, {"phase": "failed", "updated_at": now}, now=now, start=offset
            )
            self.assertIn("NEW_FAILURE", str(values))
            self.assertNotIn("OLD_FAILURE", str(values))
            end = path.stat().st_size
            with path.open("a", encoding="utf-8") as stream:
                stream.write("ValueError: NEXT_ATTEMPT\n")
            values = failure_log(
                path, {"phase": "failed", "updated_at": now}, start=offset, end=end
            )
            self.assertIn("NEW_FAILURE", str(values))
            self.assertNotIn("NEXT_ATTEMPT", str(values))
            path.write_text("Error: ROTATED\n", encoding="utf-8")
            self.assertEqual(failure_log(path, {}, start=offset, end=end), [])
            os.utime(path, (now - 300, now - 300))
            self.assertEqual(failure_log(path, {"phase": "failed", "updated_at": now}, now=now), [])

    def test_running_health_failure_reads_current_attempt_instead_of_old_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = engine_fixture.EngineContractTests().fixture(Path(directory), "windows")
            path = engine.binding.state / "logs/api.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Error: OLD_EXIT\n", encoding="utf-8")
            offset = path.stat().st_size
            with path.open("a", encoding="utf-8") as stream:
                stream.write("Error: CURRENT_HEALTH\n")
            with patch("dev_tools.projects.engine._probe", return_value=("unhealthy", [])):
                value = engine.service_status(
                    "api",
                    worker={
                        "phase": "running",
                        "log_start": offset,
                        "last_failure": {"log_start": 0, "log_end": offset},
                    },
                )
            facts = value["log_diagnostics"]
            self.assertIn("CURRENT_HEALTH", str(facts))
            self.assertNotIn("OLD_EXIT", str(facts))
            self.assertEqual(facts[0]["code"], "log.tail")

    def test_native_reader_rejects_file_replaced_between_stat_and_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.log"
            other = Path(directory) / "replacement.log"
            path.write_text("Error: CURRENT_FAILURE\n", encoding="utf-8")
            other.write_text("Error: UNRELATED_FILE\n", encoding="utf-8")
            descriptor = os.open(other, os.O_RDONLY)
            with patch("dev_tools.projects.diagnostic_evidence.os.open", return_value=descriptor):
                self.assertEqual(failure_log(path, {}), [])

    def test_old_remote_log_fallback_is_read_only_limited_and_does_not_mutate_payload(self):
        projects = [dict(self.project(), name=f"fixture-{i}") for i in range(5)]
        original = json.dumps(projects)
        reply = subprocess.CompletedProcess(
            [], 0, "Error: library cache is not writable; token=PRIVATE\n", ""
        )
        with patch("dev_tools.runtimes.router.forward_remote", return_value=reply) as forward:
            values = remote_log_evidence(projects, "wsl", SimpleNamespace())
        self.assertEqual(forward.call_count, 3)
        self.assertTrue(all(call.args[1][0] == "logs" for call in forward.call_args_list))
        self.assertTrue(all(call.kwargs["timeout"] <= 2 for call in forward.call_args_list))
        self.assertEqual(json.dumps(projects), original)
        self.assertNotIn("PRIVATE", json.dumps(values))
        self.assertEqual(values[0]["services"]["api"]["log_diagnostics"][0]["code"], "log.tail")

    def test_legacy_native_target_uses_declarations_for_the_target_platform(self):
        project = self.project("running")
        raw = {
            "schema": 1,
            "name": "fixture",
            "services": {
                "api": {
                    "command": ["never-run"],
                    "health": {"tcp": 7001},
                    "platforms": {"wsl": {"health": {"tcp": 7002}}},
                }
            },
        }
        reply = subprocess.CompletedProcess([], 0, json.dumps({"project": raw}), "")
        with patch("dev_tools.runtimes.router.forward_remote", return_value=reply) as forward:
            value = remote_log_evidence([project], "wsl", SimpleNamespace())[0]
        self.assertEqual(forward.call_args.args[1], ["show", "fixture", "--json"])
        self.assertIn(":7002", value["services"]["api"]["diagnostics"][0]["evidence"]["target"])

    def test_recovery_starting_snapshot_still_collects_exception_clues(self):
        project = self.project("running")
        project["recovery"] = {"restore_workers": ["api"]}
        raw = {
            "schema": 1,
            "name": "fixture",
            "services": {"api": {"command": ["never-run"], "health": {"tcp": 7001}}},
        }
        replies = [
            subprocess.CompletedProcess([], 0, json.dumps({"project": raw}), ""),
            subprocess.CompletedProcess([], 0, "ImportError: native dependency is missing\n", ""),
        ]
        with patch("dev_tools.runtimes.router.forward_remote", side_effect=replies):
            value = remote_log_evidence([project], "wsl", SimpleNamespace())[0]
        self.assertIn(
            "native dependency",
            value["services"]["api"]["log_diagnostics"][0]["evidence"]["message"],
        )

    def test_healthy_services_never_trigger_supplementary_calls_and_timeouts_are_advisory(self):
        project = self.project("running")
        project["services"]["api"]["health"] = "healthy"
        with patch("dev_tools.runtimes.router.forward_remote") as forward:
            remote_log_evidence([project], "wsl", SimpleNamespace())
        forward.assert_not_called()
        with patch(
            "dev_tools.runtimes.router.forward_remote",
            side_effect=subprocess.TimeoutExpired("fixture", 1),
        ):
            value = remote_log_evidence([self.project()], "wsl", SimpleNamespace())[0]
        self.assertEqual(value["services"]["api"]["phase"], "restarting")

    def test_log_messages_are_visible_in_status_list_json_and_operation_failure(self):
        project = self.project()
        facts = [
            diagnostic(
                "log.tail",
                service="api",
                severity="info",
                evidence={"message": "PermissionError: cache directory is not writable"},
            )
        ]
        project["services"]["api"]["log_diagnostics"] = facts
        project = enrich(project)
        with language_scope("en"):
            for payload in (
                dict(project, action="status"),
                {"action": "list", "environment": "wsl", "projects": [project]},
            ):
                with contextlib.redirect_stdout(output := io.StringIO()):
                    emit(payload, False)
                self.assertIn("Reason summar", output.getvalue())
                self.assertIn("cache directory is not writable", output.getvalue())
        error = ProjectError("fixture failure")
        error.diagnostics = [diagnostic("process.exited", evidence={"exit_code": 7}), *facts]
        with (
            request_scope(["start", "fixture"]),
            contextlib.redirect_stderr(output := io.StringIO()),
        ):
            emit_error(error)
        self.assertIn("cache directory is not writable", output.getvalue())
        self.assertIn(
            "cache directory is not writable", json.dumps(error_payload(error, action="start"))
        )
        project["services"]["api"].update(phase="running", health="healthy")
        project.update(ready=True, recovery=None)
        self.assertIsNone(enrich(project)["summary"])

    def test_real_task_error_preserves_this_attempts_redacted_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = engine_fixture.EngineContractTests().fixture(Path(directory), "windows")
            from dataclasses import replace

            from dev_tools.projects.models import CommandSpec

            task = replace(
                engine.spec.tasks["install"],
                command=CommandSpec(
                    argv=(
                        sys.executable,
                        "-I",
                        "-c",
                        "print('ValueError: dependency version is invalid; password=PRIVATE'); raise SystemExit(7)",
                    )
                ),
            )
            executor = Executor(engine.spec, engine.binding, Backend(engine.binding))
            with self.assertRaises(ProjectError) as failed:
                executor.task(task)
            payload = error_payload(failed.exception, action="prepare")
            self.assertIn("dependency version", json.dumps(payload))
            self.assertNotIn("PRIVATE", json.dumps(payload))

    def test_compose_reports_container_state_instead_of_a_generic_unhealthy_label(self):
        result = subprocess.CompletedProcess(
            [], 0, json.dumps([{"Name": "fixture-db-1", "State": "exited", "Health": ""}]), ""
        )
        with patch("dev_tools.projects.engine.run_compose", return_value=result):
            healthy, values = _probe(SimpleNamespace(driver="compose", health={}), None)
        self.assertEqual(healthy, "unhealthy")
        self.assertIn("fixture-db-1", describe(values[0]))
        self.assertIn("exited", describe(values[0]))
