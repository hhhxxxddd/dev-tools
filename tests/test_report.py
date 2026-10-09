import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.report import (
    Runner,
    collect_report,
    decode_output,
    discover,
    git_report,
    render_report,
    scrub,
)
from dev_tools.report_updates import mise_updates, query_updates, rustup_updates, store_packages
from dev_tools.settings import SettingsError, load_settings, render_settings


class FakeRunner:
    def __init__(self, overrides=None):
        self.calls = []
        self.options = []
        self.overrides = overrides or {}

    def run(self, command, **kwargs):
        self.calls.append(command)
        self.options.append(kwargs)
        text = " ".join(command)
        for match, result in self.overrides.items():
            if match in text:
                return dict(result)
        output = "{}"
        if "rev-parse" in text:
            output = "origin/main"
        if "rev-list" in text:
            output = "0\t0\n"
        if "porcelain" in text:
            output = ""
        return {"status": "ok", "output": output, "exit_code": 0}


class ReportTests(unittest.TestCase):
    def test_configuration_can_disable_all_collectors(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("[report]\ncollectors = []\n", encoding="utf-8")
            runner = FakeRunner()
            value = collect_report(path, runner=runner)
            self.assertEqual(runner.calls, [])
            self.assertEqual(value["collectors"], {})
            self.assertEqual(value["repositories"], [])

    def test_roots_refresh_timeout_and_language_share_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "repository/.git").mkdir(parents=True)
            path = root / "config.toml"
            path.write_text(
                render_settings(
                    {
                        "language": "en",
                        "report": {
                            "roots": ["."],
                            "collectors": ["git"],
                            "refresh": False,
                            "timeout": 7,
                        },
                    }
                ),
                encoding="utf-8",
            )
            runner = FakeRunner()
            with patch("dev_tools.report.Runner", return_value=runner) as factory:
                value = collect_report(path)
            factory.assert_called_once_with(7)
            self.assertEqual(len(value["repositories"]), 1)
            self.assertFalse(value["refresh_requested"])
            self.assertFalse(any("fetch" in command for command in runner.calls))
            self.assertNotRegex(render_report(value), r"[\u4e00-\u9fff]")
            with patch("dev_tools.report.Runner", return_value=runner) as factory:
                value = collect_report(path, refresh=True, timeout=11)
            factory.assert_called_once_with(11)
            self.assertTrue(value["refresh_requested"])
            self.assertTrue(any("fetch" in command for command in runner.calls))

    def test_windows_report_uses_shared_wsl_distribution_and_skips_disabled_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text(
                '[wsl]\ndistro = "TestDistro"\n[report]\ncollectors = ["mise", "wsl"]\nrefresh = false\n',
                encoding="utf-8",
            )
            settings = load_settings(path)
            runner = FakeRunner()
            with (
                patch("dev_tools.report.os.name", "nt"),
                patch.dict("os.environ", {"DEV_TOOLS_DISTRO": ""}),
            ):
                value = collect_report(settings=settings, runner=runner)
            self.assertTrue(
                any(command[:3] == ["wsl.exe", "-d", "TestDistro"] for command in runner.calls)
            )
            self.assertNotIn("apt_upgradable", value["collectors"])
            self.assertNotIn("npm_inventory", value["collectors"])
            self.assertNotIn("scoop_status", value["collectors"])

    def test_git_zero_and_failed_fetch_cache(self):
        runner = FakeRunner({"fetch": {"status": "timeout", "output": ""}})
        result = git_report(Path("."), runner, True)
        self.assertEqual((result["ahead"], result["behind"]), (0, 0))
        self.assertEqual(result["tracking"], "even")
        self.assertEqual(result["freshness"], "cached")
        self.assertEqual(result["worktree"], "clean")

    def test_git_divergence_and_no_upstream(self):
        r = FakeRunner({"rev-list": {"status": "ok", "output": "2 3"}})
        self.assertEqual(git_report(Path("."), r, False)["tracking"], "diverged")
        r = FakeRunner({"rev-parse": {"status": "failed", "output": ""}})
        result = git_report(Path("."), r, False)
        self.assertEqual(result["tracking"], "no-upstream")
        self.assertIsNone(result["ahead"])
        self.assertFalse(any("fetch" in c for c in r.calls))

    def test_discovery_bounded_and_skips_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one/.git").mkdir(parents=True)
            (root / "node_modules/hidden/.git").mkdir(parents=True)
            (root / "deep/deeper/repo/.git").mkdir(parents=True)
            repos, issues = discover([temp, str(root / "missing")], 2)
            self.assertEqual([p.name for p in repos], ["one"])
            self.assertEqual(issues[0]["status"], "invalid-path")

    def test_bad_config_and_missing_are_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            self.assertEqual(load_settings(path).state, "missing")
            for value in (
                "[report]\nroots = 1",
                "[report]\nmax_depth = 99",
                "[report]\nroots = [1]",
                "[report]\nrefresh = 1",
            ):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(SettingsError):
                    collect_report(path, runner=FakeRunner())

    def test_missing_tool_and_real_timeout(self):
        with patch("dev_tools.report.shutil.which", return_value=None):
            self.assertEqual(Runner().run(["unavailable"])["status"], "missing")
        import sys

        result = Runner(1).run([sys.executable, "-c", "import time; time.sleep(10)"])
        self.assertEqual(result["status"], "timeout")

    def test_multiple_tools_fail_isolated_and_no_refresh(self):
        runner = FakeRunner(
            {
                "mise ls": {"status": "missing", "output": ""},
                "apt list": {"status": "failed", "output": "permission denied"},
            }
        )
        result = collect_report("/nonexistent/report.json", refresh=False, runner=runner)
        self.assertEqual(result["config_state"], "missing")
        self.assertEqual(result["collectors"]["apt_upgradable"]["status"], "failed")
        self.assertEqual(result["collectors"]["npm_outdated"]["status"], "skipped")
        self.assertFalse(any("fetch" in c or "update" in c or "upgrade" in c for c in runner.calls))

    def test_apt_permission_failure_marks_cached(self):
        runner = FakeRunner(
            {"apt-get update": {"status": "failed", "output": "sudo: a password is required"}}
        )
        result = collect_report("/nonexistent/report.json", runner=runner)
        self.assertEqual(result["collectors"]["apt_upgradable"]["freshness"], "cached")
        self.assertEqual(result["collectors"]["apt_refresh"]["status"], "failed")
        self.assertFalse(
            any("--accept-source-agreements" in c or "pull" in c for c in runner.calls)
        )

    def test_only_extra_npm_packages_queried(self):
        runner = FakeRunner(
            {
                "mise ls": {"status": "ok", "output": '{"npm:managed":[]}'},
                "npm list": {
                    "status": "ok",
                    "output": '{"dependencies":{"managed":{},"extra":{},"npm":{}}}',
                },
            }
        )
        collect_report("/nonexistent/report.json", runner=runner)
        command = next(c for c in runner.calls if "outdated" in c and "npm" in c)
        self.assertEqual(command[-1], "extra")
        self.assertNotIn("managed", command)

    def test_report_does_not_invoke_removed_cli_management(self):
        runner = FakeRunner()
        value = collect_report("/nonexistent/report.json", refresh=False, runner=runner)
        self.assertNotIn("cli_outdated", value["collectors"])
        self.assertNotIn("dev_tools_status", value["collectors"])
        self.assertIn(["mise", "--version"], runner.calls)
        self.assertFalse(
            any(
                "status" in command and any("dev-tools" in part for part in command)
                for command in runner.calls
            )
        )
        self.assertFalse(
            any("cli" in command and "outdated" in command for command in runner.calls)
        )

    def test_apt_partial_index_refresh_and_versions(self):
        runner = FakeRunner(
            {
                "apt-get update": {
                    "status": "ok",
                    "output": "W: Some index files failed to download",
                },
                "apt list": {
                    "status": "ok",
                    "output": "mise/stable 2026.9.18 amd64 [upgradable from: 2026.9.17]",
                },
            }
        )
        value = collect_report("/nonexistent/report.json", runner=runner)
        apt = value["collectors"]["apt_upgradable"]
        self.assertEqual(apt["freshness"], "cached")
        self.assertEqual(apt["updates"][0]["available"], "2026.9.18")
        self.assertEqual(apt["updates"][0]["installed"], "2026.9.17")

    def test_inventory_removes_runtime_paths(self):
        runner = FakeRunner(
            {
                "mise ls": {
                    "status": "ok",
                    "output": '{"python":[{"version":"3.11","install_path":"private-runtime"}]}',
                }
            }
        )
        value = collect_report("/nonexistent/report.json", refresh=False, runner=runner)
        mise = value["collectors"]["mise_installed"]
        self.assertNotIn("private-runtime", str(mise))
        self.assertEqual(mise["versions"][0]["version"], "3.11")

    def test_mise_queries_configured_versions_without_bumping_or_historical_installs(self):
        runner = FakeRunner(
            {
                "mise outdated": {
                    "status": "ok",
                    "output": '{"npm:@openai/codex":{"requested":"latest",'
                    '"current":"0.160.0","latest":"0.161.0","source":{"path":"private"}}}',
                }
            }
        )
        value = collect_report("/nonexistent/report.json", runner=runner)
        mise = value["collectors"]["mise_outdated"]
        self.assertEqual(mise["status"], "updates")
        self.assertEqual(mise["updates"][0]["installed"], "0.160.0")
        self.assertEqual(mise["updates"][0]["available"], "0.161.0")
        self.assertNotIn("source", mise["updates"][0])
        self.assertIn(["mise", "outdated", "--json"], runner.calls)
        self.assertFalse(any("--bump" in c or "--inactive" in c for c in runner.calls))

    def test_new_queries_skip_network_when_refresh_is_disabled(self):
        runner = FakeRunner()
        value = collect_report("/nonexistent/report.json", refresh=False, runner=runner)
        for name in ("mise_outdated", "snap_updates", "rustup_updates"):
            self.assertEqual(value["collectors"][name]["status"], "skipped")
        self.assertFalse(
            any("check" in c or "--list" in c or "outdated" in c for c in runner.calls)
        )

    def test_store_declines_installation_and_keeps_target_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[report]\ncollectors = ["store"]\n', encoding="utf-8")
            settings = load_settings(path)
            runner = FakeRunner(
                {
                    "store updates": {
                        "status": "ok",
                        "output": "Updates available (1 found)\n"
                        "│ Name │ Publisher │ Version │ Date │\n"
                        "├──────┼───────────┼─────────┼──────┤\n"
                        "│ Windows Web Experience │ Microsoft │ 1.2.3 │ today │\n"
                        "│ Pack │ │ │ │\n",
                    }
                }
            )
            with patch("dev_tools.report.os.name", "nt"):
                value = collect_report(settings=settings, runner=runner)
            self.assertEqual(runner.calls, [["store", "updates"]])
            self.assertEqual(runner.options, [{"input": "n\n"}])
            result = value["collectors"]["store_updates"]
            self.assertEqual(result["status"], "updates")
            self.assertEqual(result["updates"][0]["name"], "Windows Web Experience Pack")
            self.assertEqual(result["updates"][0]["installed"], "1.2.3")
            self.assertIsNone(result["updates"][0]["available"])

    def test_store_missing_does_not_claim_all_apps_are_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[report]\ncollectors = ["store"]\n', encoding="utf-8")
            settings = load_settings(path)
            runner = FakeRunner({"store updates": {"status": "missing", "output": "missing"}})
            with patch("dev_tools.report.os.name", "nt"):
                value = collect_report(settings=settings, runner=runner)
            self.assertEqual(value["collectors"]["store_updates"]["status"], "missing")
            self.assertNotIn("updates", value["collectors"]["store_updates"])

    def test_store_incomplete_table_retains_partial_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[report]\ncollectors = ["store"]\n', encoding="utf-8")
            settings = load_settings(path)
            runner = FakeRunner(
                {
                    "store updates": {
                        "status": "ok",
                        "output": "Updates available (2 found)\n│ Name │ Publisher │ Version │\n"
                        "│ Example │ Microsoft │ 1.2.3 │\n",
                    }
                }
            )
            with patch("dev_tools.report.os.name", "nt"):
                value = collect_report(settings=settings, runner=runner)
            result = value["collectors"]["store_updates"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["updates"][0]["installed"], "1.2.3")

    def test_store_confirmation_failure_does_not_hide_valid_query_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[report]\ncollectors = ["store"]\n', encoding="utf-8")
            settings = load_settings(path)
            runner = FakeRunner(
                {
                    "store updates": {
                        "status": "ok",
                        "exit_code": 0,
                        "output": "Updates available (1 found)\n│ Name │ Publisher │ Version │\n"
                        "│ Example │ Microsoft │ 1.2.3 │\n"
                        "Failed to read input in non-interactive mode.",
                    }
                }
            )
            with patch("dev_tools.report.os.name", "nt"):
                value = collect_report(settings=settings, runner=runner)
            result = value["collectors"]["store_updates"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["updates"]), 1)

    def test_missing_snap_inside_wsl_is_reported_as_missing(self):
        runner = FakeRunner({"snap": {"status": "failed", "exit_code": 127, "output": "not found"}})
        value = collect_report("/nonexistent/report.json", runner=runner)
        for name in ("snap_inventory", "snap_updates"):
            self.assertEqual(value["collectors"][name]["status"], "missing")
            self.assertNotIn("updates", value["collectors"][name])

    def test_snap_revision_updates_preserve_installed_and_available_versions(self):
        runner = FakeRunner(
            {
                "snap list": {
                    "status": "ok",
                    "output": "Name Version Rev Tracking\ncore22 20260824 2900 latest/stable\n",
                },
                "snap refresh --list": {
                    "status": "ok",
                    "output": "Name Version Rev Size\ncore22 20260824 2955 70MB\n",
                },
            }
        )
        value = collect_report("/nonexistent/report.json", runner=runner)
        snaps = value["collectors"]["snap_updates"]
        self.assertEqual(snaps["status"], "updates")
        item = snaps["updates"][0]
        self.assertEqual(item["installed"], item["available"])
        self.assertEqual((item["installed_revision"], item["available_revision"]), ("2900", "2955"))
        self.assertTrue(any(c[-3:] == ["snap", "refresh", "--list"] for c in runner.calls))

    def test_snap_inventory_failure_retains_partial_update_result(self):
        runner = FakeRunner(
            {
                "snap list": {"status": "timeout", "output": ""},
                "snap refresh --list": {
                    "status": "ok",
                    "output": "Name Version Rev Size\ncore22 20260824 2955 70MB\n",
                },
            }
        )
        value = collect_report("/nonexistent/report.json", runner=runner)
        snaps = value["collectors"]["snap_updates"]
        self.assertEqual(snaps["status"], "partial")
        self.assertIsNone(snaps["updates"][0]["installed"])
        self.assertEqual(snaps["updates"][0]["available"], "20260824")

    def test_rustup_update_exit_code_and_mixed_failed_results(self):
        output = (
            "stable-x86_64-unknown-linux-gnu - update available: "
            "1.98.1 (old 2026-09-01) -> 1.99.0 (new 2026-09-28)\n"
            "rustup - up to date : 1.29.1\n"
        )
        result = {"status": "failed", "exit_code": 100, "output": output}
        query_updates(result, rustup_updates, success_codes=(0, 100))
        self.assertEqual(result["status"], "updates")
        self.assertEqual(result["updates"][0]["available"], "1.99.0")
        result = {"status": "failed", "exit_code": 1, "output": output + "error: unavailable"}
        query_updates(result, rustup_updates, success_codes=(0, 100))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["updates"]), 1)

    def test_windows_and_wsl_update_queries_are_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text(
                '[wsl]\ndistro = "TestDistro"\n[report]\n'
                'collectors = ["mise", "snap", "rustup", "wsl"]\n',
                encoding="utf-8",
            )
            settings = load_settings(path)
            runner = FakeRunner()
            with (
                patch("dev_tools.report.os.name", "nt"),
                patch.dict("os.environ", {"DEV_TOOLS_DISTRO": ""}),
            ):
                value = collect_report(settings=settings, runner=runner)
            self.assertIn(
                ["wsl.exe", "-d", "TestDistro", "--exec", "mise", "outdated", "--json"],
                runner.calls,
            )
            self.assertIn("wsl_rustup_updates", value["collectors"])
            snap = next(c for c in runner.calls if "snap" in c and "--list" in c)
            self.assertEqual(snap[:4], ["wsl.exe", "-d", "TestDistro", "--exec"])
            self.assertNotIn("store_updates", value["collectors"])

    def test_unknown_update_responses_are_not_reported_as_current(self):
        for parser, output in (
            (mise_updates, "[]"),
            (mise_updates, '{"node":{"current":"1"}}'),
            (store_packages, "Service unavailable"),
            (rustup_updates, "Unexpected response"),
        ):
            result = {"status": "ok", "exit_code": 0, "output": output}
            query_updates(result, parser)
            self.assertEqual(result["status"], "parse-error")
            self.assertNotIn("updates", result)

    def test_runner_only_supplies_explicit_noninteractive_input(self):
        import sys

        result = Runner(5).run(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], input="n\n"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"].strip(), "'n\\n'")

    def test_utf8_output(self):
        self.assertEqual(decode_output("软件检查".encode()), "软件检查")

    def test_redaction(self):
        self.assertNotIn("mysecret", scrub("password=mysecret https://user:pass@example.com/repo"))
        self.assertNotIn("example.com", scrub("git@example.com:repo"))


if __name__ == "__main__":
    unittest.main()
