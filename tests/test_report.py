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
    load_config,
    scrub,
)


class FakeRunner:
    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def run(self, command, **kwargs):
        self.calls.append(command)
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
            p = Path(temp) / "config.json"
            self.assertEqual(load_config(str(p))[1], "missing")
            for value in [
                "[]",
                '{"roots":[]}',
                '{"max_depth":99}',
                '{"roots":{"windows":[1],"wsl":[1]}}',
            ]:
                p.write_text(value)
                self.assertEqual(load_config(str(p))[1], "invalid")

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

    def test_utf8_output(self):
        self.assertEqual(decode_output("软件检查".encode()), "软件检查")

    def test_redaction(self):
        self.assertNotIn("mysecret", scrub("password=mysecret https://user:pass@example.com/repo"))
        self.assertNotIn("example.com", scrub("git@example.com:repo"))


if __name__ == "__main__":
    unittest.main()
