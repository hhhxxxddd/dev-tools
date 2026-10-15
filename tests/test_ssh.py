from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.i18n import language_scope
from dev_tools.ssh import print_hosts
from dev_tools.ssh_config import collect_hosts


class SshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        (self.home / ".ssh").mkdir(parents=True)
        self.config = self.home / ".ssh/config"
        self.system = self.root / "etc/ssh/ssh_config"
        self.native = "win" if os.name == "nt" else "wsl"
        self.target = "wsl" if os.name == "nt" else "win"
        self.settings = SimpleNamespace(language="zh", errors=(), distro="Fixture")

    def write(self, content, path=None):
        path = path or self.config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def collect(self, **kwargs):
        return collect_hosts(self.home, "local-user", self.system, **kwargs)

    def invoke(self, *args, remote=None):
        with (
            patch("dev_tools.cli.load_settings", return_value=self.settings),
            patch(
                "dev_tools.ssh.native_identity", return_value=(self.home, "local-user", self.system)
            ),
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch("dev_tools.runtimes.router.forward_remote", return_value=remote) as transport,
            contextlib.redirect_stdout(out := io.StringIO()),
            contextlib.redirect_stderr(err := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(args))
        self.transport = transport
        return stopped.exception.code, out.getvalue(), err.getvalue()

    def test_missing_configs_are_empty_and_do_not_create_files(self):
        value = self.collect()
        self.assertTrue(value["complete"])
        self.assertEqual(value["hosts"], [])
        self.assertFalse(self.config.exists())
        code, output, errors = self.invoke("ssh", "list")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("Host", output)
        self.transport.assert_not_called()

    def test_unconditional_match_keyword_is_case_insensitive(self):
        self.write("Match ALL\n User deploy\nHost demo\n")
        value = self.collect()
        self.assertTrue(value["complete"])
        self.assertEqual(value["hosts"][0]["user"], "deploy")
        self.assertEqual(value["hosts"][0]["unresolved_fields"], [])

    def test_proxy_precedence_includes_disabled_jump_and_case_insensitive_none(self):
        directives = (
            "ProxyCommand NONE",
            "ProxyCommand echo fixture",
            "ProxyJump NONE",
            "ProxyJump jump",
        )
        for first in range(4):
            for second in range(4):
                with self.subTest(first=directives[first], second=directives[second]):
                    self.write(f"Host demo\n {directives[first]}\n {directives[second]}\n")
                    host = self.collect()["hosts"][0]
                    self.assertEqual(host["proxy_jump"], "jump" if first == 3 else None)
                    self.assertEqual(
                        host["proxy_command"], first == 1 or (first == 2 and second == 1)
                    )

    def test_aliases_defaults_negation_and_first_value_precedence(self):
        self.write(
            "Host Alpha beta\n HostName %h.example.test\n User deploy\nHost * !beta\n Port 2200\nHost beta\n Port 2222\nHost *\n User ignored\n Port 22\nHost *.example.test !excluded\n User wildcard\n"
        )
        self.write("Host Alpha system-only\n User system\n Port 9999\n", self.system)
        value = self.collect()
        self.assertTrue(value["complete"], value)
        self.assertEqual(
            [host["name"] for host in value["hosts"]], ["Alpha", "beta", "system-only"]
        )
        first, second, third = value["hosts"]
        self.assertEqual(
            (first["hostname"], first["user"], first["port"]),
            ("Alpha.example.test", "deploy", 2200),
        )
        self.assertEqual(second["port"], 2222)
        self.assertEqual(
            (third["hostname"], third["user"], third["port"]), ("system-only", "ignored", 2200)
        )

    def test_includes_glob_order_root_relative_paths_and_context_restore(self):
        self.write('Include "parts/*.conf"\nHost after\n HostName after.test\n')
        self.write(
            "Host included\n Include options.conf\n Port 2200\n", self.home / ".ssh/parts/10.conf"
        )
        self.write("Host included\n User too-late\n", self.home / ".ssh/parts/20.conf")
        self.write("User first\nHost other\n Port 2300\n", self.home / ".ssh/options.conf")
        value = self.collect()
        hosts = {host["name"]: host for host in value["hosts"]}
        self.assertEqual(
            set(hosts), {"included", "after"}
        )  # other is behind a nonmatching Include.
        self.assertEqual((hosts["included"]["user"], hosts["included"]["port"]), ("first", 2200))
        self.assertEqual(hosts["after"]["port"], 22)
        self.assertEqual(Path(hosts["included"]["source"]).name, "10.conf")

    def test_include_options_only_apply_to_matching_host(self):
        self.write("Host selected\n Include selected.conf\nHost remaining\n")
        self.write("Port 2222\n", self.home / ".ssh/selected.conf")
        hosts = {host["name"]: host for host in self.collect()["hosts"]}
        self.assertEqual(hosts["selected"]["port"], 2222)
        self.assertEqual(hosts["remaining"]["port"], 22)

    def test_quoted_equals_bom_comments_and_native_include_path(self):
        include = self.home / ".ssh/含 空格.conf"
        self.write(
            'Host "演示"\n HostName="demo.example.test" # comment\n User=deploy\n ProxyJump jump\n',
            include,
        )
        self.write('\ufeffInclude "' + str(include) + '"\n')
        value = self.collect()
        self.assertTrue(value["complete"], value)
        self.assertEqual(value["hosts"][0]["name"], "演示")
        self.assertEqual(value["hosts"][0]["proxy_jump"], "jump")

    def test_home_environment_include_and_missing_glob(self):
        self.write("Include ${HOME}/.ssh/parts/*.conf missing/*.conf\n")
        self.write("Host from-home\n", self.home / ".ssh/parts/one.conf")
        self.assertEqual([host["name"] for host in self.collect()["hosts"]], ["from-home"])

    def test_include_globs_stop_consuming_at_the_file_limit(self):
        from dev_tools.ssh_config import MAX_FILES

        self.write("Include parts/*.conf\nHost stable\n")
        consumed = []

        def filenames(_):
            for index in range(MAX_FILES + 2):
                consumed.append(index)
                if index > MAX_FILES:
                    self.fail("Include glob consumed beyond the read limit")
                yield str(self.home / f".ssh/parts/{index}.conf")

        with patch("dev_tools.ssh_config.glob.iglob", side_effect=filenames):
            value = self.collect()
        self.assertEqual(len(consumed), MAX_FILES + 1)
        self.assertFalse(value["complete"])
        self.assertIn("scan_limit", [item["code"] for item in value["warnings"]])

    def test_match_exec_and_proxy_commands_are_never_executed_or_echoed(self):
        marker = self.root / "executed"
        self.write(
            f'Host safe\n User known\nMatch exec "echo SECRET > {marker}"\n User conditional\n Port 2222\nHost proxied\n ProxyCommand echo SECRET\n'
        )
        with patch("subprocess.run", side_effect=AssertionError("unexpected execution")):
            value = self.collect()
        hosts = {host["name"]: host for host in value["hosts"]}
        self.assertEqual(hosts["safe"]["user"], "known")
        self.assertIsNone(hosts["safe"]["port"])
        self.assertIn("port", hosts["safe"]["unresolved_fields"])
        self.assertTrue(hosts["proxied"]["proxy_command"])
        self.assertFalse(marker.exists())
        self.assertNotIn("SECRET", json.dumps(value))

    def test_conditional_include_aliases_are_marked_and_known_values_not_guessed(self):
        self.write('Match exec "anything"\n Include maybe.conf\nHost stable\n')
        self.write("Host maybe\n HostName maybe.test\n", self.home / ".ssh/maybe.conf")
        hosts = {host["name"]: host for host in self.collect()["hosts"]}
        self.assertTrue(hosts["maybe"]["conditional"])
        self.assertIsNone(hosts["maybe"]["hostname"])
        self.assertFalse(hosts["stable"]["conditional"])

    def test_none_proxy_and_first_proxy_option_win(self):
        self.write(
            "Host direct\n ProxyCommand none\n ProxyJump ignored\nHost jump\n ProxyJump bastion\n ProxyCommand ignored\nHost none\n ProxyJump none\n"
        )
        hosts = {host["name"]: host for host in self.collect()["hosts"]}
        self.assertFalse(hosts["direct"]["proxy_command"])
        self.assertIsNone(hosts["direct"]["proxy_jump"])
        self.assertEqual(hosts["jump"]["proxy_jump"], "bastion")
        self.assertFalse(hosts["jump"]["proxy_command"])
        self.assertIsNone(hosts["none"]["proxy_jump"])

    def test_cycle_unreadable_and_invalid_config_preserve_partial_results(self):
        self.write(
            "Host valid\n HostName valid.test\nInclude config\nHost damaged\n Port invalid\n"
        )
        value = self.collect()
        self.assertFalse(value["complete"])
        self.assertEqual(
            {item["code"] for item in value["warnings"]}, {"include_cycle", "invalid_config"}
        )
        self.assertEqual(len(value["hosts"]), 2)
        self.assertEqual(
            next(host for host in value["hosts"] if host["name"] == "valid")["hostname"],
            "valid.test",
        )
        self.config.write_bytes(b"\xff\xfeinvalid")
        self.assertEqual(self.collect()["warnings"][0]["code"], "unreadable")

    def test_invalid_quotes_and_dynamic_includes_are_diagnostics_without_raw_content(self):
        self.write('Host visible\n User "SECRET\n Include %h/config\n')
        value = self.collect()
        self.assertEqual(
            {item["code"] for item in value["warnings"]}, {"invalid_config", "dynamic_include"}
        )
        self.assertNotIn("SECRET", json.dumps(value))

    def test_filter_and_json_do_not_change_read_only_behavior(self):
        self.write("Host alpha\n HostName alpha.test\n User deploy\nHost beta\n User different\n")
        before = self.config.read_bytes()
        code, output, errors = self.invoke("ssh", "list", "DEPLOY", "--json")
        self.assertEqual((code, errors), (0, ""))
        value = json.loads(output)
        self.assertEqual((value["action"], value["operation"]), ("ssh", "list"))
        self.assertEqual([host["name"] for host in value["hosts"]], ["alpha"])
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(self.collect(query="alpha.test")["hosts"][0]["name"], "alpha")

    def test_human_table_is_localized_and_terminal_controls_are_escaped(self):
        self.write("Host demo\n")
        value = {"environment": "windows", **self.collect()}
        value["hosts"][0]["hostname"] = "unsafe\x1b[31m"
        for language, header in (("zh", "别名"), ("en", "Alias")):
            with language_scope(language), contextlib.redirect_stdout(out := io.StringIO()):
                print_hosts(value)
            self.assertIn(header, out.getvalue())
            self.assertNotIn("\x1b", out.getvalue())
            self.assertIn("\\x1b", out.getvalue())

    def test_environment_and_format_flags_work_before_and_after_subcommands(self):
        for args in (
            ["-e", "win", "ssh", "list", "--json"],
            ["ssh", "-e", "win", "--json", "list"],
            ["ssh", "list", "-e", "win", "--json"],
        ):
            parsed = parser().parse_args(args)
            self.assertEqual(parsed.env, "win")
            self.assertTrue(parsed.json)
            self.assertFalse(hasattr(parsed, "project_command"))

    def test_remote_result_is_rendered_locally_and_only_query_is_forwarded(self):
        self.write("Host demo\n")
        value = {
            "schema_version": 3,
            "action": "ssh",
            "operation": "list",
            "environment": "wsl" if self.target == "wsl" else "windows",
            **self.collect(),
        }
        remote = subprocess.CompletedProcess([], 0, json.dumps(value), "")
        for flags in ([], ["--json"]):
            code, output, errors = self.invoke(
                "--config",
                "caller.toml",
                "ssh",
                "list",
                "demo",
                "-e",
                self.target,
                *flags,
                remote=remote,
            )
            self.assertEqual((code, errors), (0, ""))
            if flags:
                self.assertEqual(json.loads(output), value)
            else:
                self.assertIn("别名", output)
            self.assertEqual(
                self.transport.call_args.args,
                (self.target, ["ssh", "list", "--json", "--", "demo"]),
            )

    def test_old_target_gets_explicit_unsupported_diagnostic_even_at_same_version(self):
        remote = subprocess.CompletedProcess(
            [], 2, '{"schema_version":3,"action":null,"error":{"code":"invalid_arguments"}}', ""
        )
        code, output, errors = self.invoke(
            "ssh", "list", "-e", self.target, "--json", remote=remote
        )
        self.assertEqual((code, errors), (1, ""))
        self.assertEqual(json.loads(output)["error"]["code"], "target_command_unsupported")
        self.assertEqual(json.loads(output)["action"], "ssh")

    def test_malformed_remote_rows_are_rejected_without_traceback(self):
        for hosts in ([{}], [None]):
            value = {
                "schema_version": 3,
                "action": "ssh",
                "operation": "list",
                "environment": "wsl" if self.target == "wsl" else "windows",
                "hosts": hosts,
                "warnings": [],
                "files": [],
                "complete": True,
            }
            remote = subprocess.CompletedProcess([], 0, json.dumps(value), "")
            code, output, errors = self.invoke(
                "ssh", "list", "-e", self.target, "--json", remote=remote
            )
            self.assertEqual((code, errors), (1, ""))
            self.assertEqual(json.loads(output)["error"]["code"], "target_response_invalid")


if __name__ == "__main__":
    unittest.main()
