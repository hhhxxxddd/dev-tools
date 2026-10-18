from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import cmd_init, main, parser
from dev_tools.i18n import language_scope, message
from dev_tools.projects.cli import run
from dev_tools.projects.models import ProjectError
from tests import test_project_engine as engine_fixture


class CliTests(unittest.TestCase):
    def test_removed_command_forms_are_rejected(self):
        for arguments in (
            ["sync", "demo"],
            ["self", "install"],
            ["build", "demo", "--kind", "source"],
            ["init", "--dry-run"],
            ["prepare", "demo", "--dry-run"],
            ["check", "demo", "--dry-run"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                parser().parse_args(arguments)
            self.assertEqual(result.exception.code, 2)

    def test_execution_json_keeps_native_process_output_on_stderr(self):
        code = textwrap.dedent("""\
            import argparse, copy, json, os, sys, tempfile
            from pathlib import Path
            from unittest.mock import patch
            from dev_tools.projects.cli import run
            from dev_tools.projects.config import parse_project, render_toml
            from dev_tools.projects.execution import Executor
            from tests.test_project_engine import EngineContractTests
            with tempfile.TemporaryDirectory() as temporary:
                engine = EngineContractTests().fixture(Path(temporary), "windows")
                raw = copy.deepcopy(engine.spec.raw)
                raw.update(tasks={}, builds={}, services={"stack": {"driver": "compose"}})
                (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
                (engine.binding.source / "compose.yaml").write_text("services: {}")
                engine._set_spec(parse_project(raw, "windows"))
                engine.executor = Executor(engine.spec, engine.binding, engine.backend)
                def resolve(command, **kwargs):
                    content = "native build progress" if command.argv[-1:] == ("build",) else json.dumps([{"State": "running"}])
                    return [sys.executable, "-c", "print(" + repr(content) + ")"], engine.binding.source, os.environ.copy()
                args = argparse.Namespace(project_command=sys.argv[1], env="win", name="demo", service=None, json=True)
                with patch("dev_tools.projects.cli.engine_for", return_value=engine), patch.object(engine.executor, "resolve", side_effect=resolve), patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
                    raise SystemExit(run(args))
            """)
        for action in ("start", "prepare"):
            with self.subTest(action=action):
                result = subprocess.run(
                    [sys.executable, "-c", code, action],
                    cwd=Path(__file__).parents[1],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertTrue(payload["completed"])
                self.assertEqual(payload["action"], action)
                self.assertIn("native build progress", result.stderr)

    def test_init_does_not_write_a_partial_config_when_versions_are_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"engines":{"node":"<20"},"packageManager":"pnpm@10.0.0"}'
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), json=True))
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["action"], "unresolved")
            self.assertFalse((root / "mise.toml").exists())

    def test_version_matches_project_metadata(self) -> None:
        metadata = tomllib.loads(
            (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(metadata["project"]["version"], __version__)

    def test_version_flag_prints_version(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_context:
            parser().parse_args(["--version"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "dev-tools " + __version__)

    def test_init_creates_parseable_mise_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".nvmrc").write_text("22\n", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), json=True))

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["action"], "created")
            self.assertEqual((root / "mise.toml").read_text(encoding="utf-8"), payload["content"])

    def test_existing_config_is_preserved_even_when_legacy_files_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "apps/a").mkdir(parents=True)
            (root / "apps/b").mkdir(parents=True)
            config = root / "mise.toml"
            config.write_text('[tools]\nnode = "22"\n', encoding="utf-8")
            (root / "apps/a/.nvmrc").write_text("20\n", encoding="utf-8")
            (root / "apps/b/.nvmrc").write_text("22\n", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), json=True))

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["action"], "preserved")

    def test_native_commands_use_the_same_parser(self) -> None:
        for environment in ("win", "wsl"):
            for action in ("check", "prepare"):
                args = parser().parse_args(["--env", environment, action, "demo", "--json"])
                self.assertEqual(args.name, "demo")
                self.assertFalse(hasattr(args, "dry_run"))
                self.assertTrue(args.json)
                self.assertEqual(args.func.__module__, "dev_tools.projects.cli")

    def test_check_uses_full_plan_without_executing_or_writing(self):
        for environment in ("windows", "wsl"):
            for blocked in (False, True):
                with (
                    self.subTest(environment=environment, blocked=blocked),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    engine = engine_fixture.EngineContractTests().fixture(root, environment)
                    if blocked:
                        engine.backend.unresolved = (message("fixture prerequisite is missing"),)
                    before = {
                        path.relative_to(root): path.read_bytes()
                        for path in root.rglob("*")
                        if path.is_file()
                    }
                    output = io.StringIO()
                    with (
                        patch("dev_tools.projects.cli.engine_for", return_value=engine),
                        patch.object(engine, "plan", wraps=engine.plan) as plan,
                        patch.object(engine, "prepare") as prepare,
                        patch.object(engine.backend, "require_control") as require_control,
                        patch("subprocess.run") as execute,
                        contextlib.redirect_stdout(output),
                    ):
                        code = run(
                            parser().parse_args(
                                [
                                    "-e",
                                    "win" if environment == "windows" else "wsl",
                                    "check",
                                    "demo",
                                    "--json",
                                ]
                            )
                        )
                    plan.assert_called_once_with(force=True)
                    prepare.assert_not_called()
                    require_control.assert_not_called()
                    execute.assert_not_called()
                    self.assertEqual(code, 1 if blocked else 0)
                    payload = json.loads(output.getvalue())
                    self.assertEqual(payload["action"], "check")
                    self.assertEqual(payload["ready"], not blocked)
                    self.assertEqual([task["name"] for task in payload["tasks"]], ["install"])
                    self.assertFalse(engine.backend.events)
                    self.assertEqual(
                        before,
                        {
                            path.relative_to(root): path.read_bytes()
                            for path in root.rglob("*")
                            if path.is_file()
                        },
                    )
                    self.assertFalse(engine.binding.state.exists())
                    if environment == "wsl":
                        self.assertFalse(engine.binding.workspace.exists())

    def test_prepare_json_executes_the_same_full_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            output = io.StringIO()
            with (
                patch("dev_tools.projects.cli.engine_for", return_value=engine),
                patch.object(engine, "plan", wraps=engine.plan) as plan,
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    run(parser().parse_args(["-e", "win", "prepare", "demo", "--json"])),
                    0,
                )
            plan.assert_called_once_with(force=True)
            self.assertIn("task:install", engine.backend.events)
            self.assertTrue(engine.state()["prepared_revision"])
            self.assertEqual(json.loads(output.getvalue())["action"], "prepare")
            self.assertTrue(json.loads(output.getvalue())["completed"])

    def test_list_table_localizes_states_aligns_columns_and_preserves_json(self):
        environment = "windows" if os.name == "nt" else "wsl"
        target = "win" if environment == "windows" else environment
        environment_label = "Windows" if environment == "windows" else "WSL"
        cases = [
            ("a" * 63, ["running"], True, "healthy", None, "运行中", "Running"),
            ("paused", ["stopped"], False, "unknown", None, "已停止", "Stopped"),
            ("starting", ["starting"], False, "unknown", None, "启动中", "Starting"),
            ("failed", ["failed"], False, "unknown", None, "失败", "Failed"),
            (
                "mixed",
                ["running", "stopped"],
                False,
                "unknown",
                None,
                "部分运行",
                "Partially running",
            ),
            ("unhealthy", ["running"], False, "unhealthy", None, "异常", "Unhealthy"),
            ("stale", ["running"], False, "healthy", None, "未就绪", "Not ready"),
            (
                "recovering",
                ["stopped"],
                False,
                "unknown",
                {"operation": "prepare"},
                "待恢复",
                "Recovery pending",
            ),
        ]
        projects = [
            {
                "name": name,
                "environment": environment,
                "ready": ready,
                "recovery": recovery,
                "services": {
                    str(index): {"phase": phase, "health": health}
                    for index, phase in enumerate(phases)
                },
            }
            for name, phases, ready, health, recovery, _, _ in cases
        ]
        names = [project["name"] for project in projects] + ["broken"]

        def invoke(as_json=False):
            output = io.StringIO()
            engines = [SimpleNamespace(status=lambda value=value: value) for value in projects]
            engines.append(
                ProjectError(message("project is not registered: {name}", name="broken"))
            )
            with (
                patch("dev_tools.projects.cli.Registry.names", return_value=names),
                patch("dev_tools.projects.cli.project_columns", return_value={}),
                patch("dev_tools.projects.cli.engine_for", side_effect=engines),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    run(
                        parser().parse_args(
                            ["-e", target, "list", *(["--json"] if as_json else [])]
                        )
                    ),
                    0,
                )
            return output.getvalue()

        for language in ("zh", "en"):
            with self.subTest(language=language), language_scope(language):
                lines = invoke().splitlines()
                header = lines[1]
                if language == "zh":
                    self.assertIn("名称", header)
                    self.assertIn("状态", header)
                    environment_column = header.replace("名称", "NNNN").index("环境")
                else:
                    self.assertIn("Name", header)
                    self.assertIn("State", header)
                    environment_column = header.index("Environment")
                for row, case in zip(lines[3 : 3 + len(cases)], cases, strict=True):
                    self.assertEqual(row.index(environment_label), environment_column)
                    self.assertIn(case[-2] if language == "zh" else case[-1], row)
                    self.assertTrue(row.endswith(case[-2] if language == "zh" else case[-1]), row)
                self.assertIn("broken", lines[3 + len(cases)])
                self.assertIn(
                    "检查失败" if language == "zh" else "Check failed", lines[3 + len(cases)]
                )
                self.assertIn(
                    "项目尚未注册" if language == "zh" else "project is not registered", lines[-1]
                )
                payload = json.loads(invoke(as_json=True))
                self.assertEqual(payload["projects"][:-1], projects)
                self.assertEqual(payload["environment"], environment)
                self.assertFalse(payload["projects"][-1]["ready"])

    def test_empty_list_keeps_table_headers_and_empty_message(self):
        with (
            patch("dev_tools.projects.cli.Registry.names", return_value=()),
            contextlib.redirect_stdout(output := io.StringIO()),
            language_scope("en"),
        ):
            self.assertEqual(run(parser().parse_args(["list"])), 0)
        self.assertIn("Name", output.getvalue())
        self.assertIn("Environment", output.getvalue())
        self.assertIn("No registered projects", output.getvalue())

    def test_environment_defaults_to_native_and_removed_forms_are_rejected(self):
        args = parser().parse_args(["list"])
        self.assertEqual(args.env, "win" if os.name == "nt" else "wsl")
        for arguments in (
            ["cli", "status"],
            ["project", "list"],
            ["compile", "demo"],
            ["--en", "wsl", "list"],
            ["list", "--en", "wsl"],
            ["--env", "windows", "list"],
            ["--env", "local", "list"],
            ["doctor", "demo"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parser().parse_args(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_environment_flags_before_and_after_flat_command(self):
        for environment in ("win", "wsl"):
            for flags in (["-e", environment], ["--env=" + environment], ["-e" + environment]):
                for tokens in ([*flags, "status", "demo"], ["status", "demo", *flags]):
                    args = parser().parse_args(tokens)
                    self.assertEqual(args.env, environment)
                    self.assertEqual(args.name, "demo")

    def test_logs_project_precedes_optional_service_and_rename_destination_is_unambiguous(self):
        for tokens, name, service in (
            (["logs"], None, None),
            (["logs", "demo"], "demo", None),
            (["logs", "demo", "api"], "demo", "api"),
        ):
            args = parser().parse_args(tokens)
            self.assertEqual((args.name, args.service), (name, service))
        args = parser().parse_args(["rename", "new-name"])
        self.assertIsNone(args.name)
        self.assertEqual(args.new_name, "new-name")

    def test_help_lists_every_public_command_and_hides_worker(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["help"])
        self.assertEqual(stopped.exception.code, 0)
        help_text = output.getvalue()
        for command in (
            "init",
            "register",
            "list",
            "check",
            "prepare",
            "start",
            "stop",
            "restart",
            "status",
            "build",
            "logs",
            "unregister",
            "scan",
            "show",
            "rename",
            "report",
            "sysinfo",
            "help",
        ):
            self.assertRegex(help_text, rf"(?m)^  {command}\s+")
        self.assertNotIn("_worker", help_text)
        self.assertNotIn("project COMMAND", help_text)
        self.assertNotIn("--dry-run", help_text)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["help", "unregister"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--purge", output.getvalue())
        self.assertIn("[项目名]", output.getvalue())

    def test_machine_commands_accept_environment_in_the_shared_parser(self):
        for command in ("config", "sysinfo", "report"):
            for environment in ("win", "wsl"):
                for arguments in (["-e", environment, command], [command, "-e", environment]):
                    self.assertEqual(parser().parse_args(arguments).env, environment)


if __name__ == "__main__":
    unittest.main()
