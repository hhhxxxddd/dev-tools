from __future__ import annotations

import contextlib
import copy
import http.server
import io
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dev_tools import __version__
from dev_tools.cli import main
from dev_tools.i18n import language_scope
from dev_tools.schemas import CLI_SCHEMA_VERSION
from dev_tools.settings import Settings, defaults
from dev_tools.toolbox import network, paths, ports
from dev_tools.toolbox.cli import display
from dev_tools.toolbox.platforms.wsl import parse_table


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(self.root / "config.toml", "missing", defaults(), {})
        self.native = "win" if os.name == "nt" else "wsl"
        self.target = "wsl" if os.name == "nt" else "win"

    def invoke(self, *arguments, remote=None):
        with (
            patch("dev_tools.cli.load_settings", return_value=self.settings),
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch("dev_tools.runtimes.router.forward_remote", return_value=remote) as transport,
            contextlib.redirect_stdout(out := io.StringIO()),
            contextlib.redirect_stderr(err := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        self.transport = transport
        return stopped.exception.code, out.getvalue(), err.getvalue()

    def test_group_help_is_available_locally_and_with_explicit_environment(self):
        for command in ("ssh", "hosts", "ports", "which", "net"):
            for arguments in ((command,), ("-e", self.native, command)):
                code, output, errors = self.invoke(*arguments)
                self.assertEqual((code, errors), (0, ""))
                self.assertIn("--json", output)
                self.transport.assert_not_called()
                self.assertEqual((code, output, errors), self.invoke(*arguments, "--help"))

    def test_bare_toolbox_commands_forward_help_to_the_selected_environment(self):
        for command in ("ssh", "hosts", "ports", "which", "net"):
            remote = subprocess.CompletedProcess([], 0, "target help\n", "")
            for arguments in (("-e", self.target, command), (command, "-e", self.target)):
                code, output, errors = self.invoke(*arguments, remote=remote)
                self.assertEqual((code, errors), (0, ""))
                self.assertIn("target help", output)
                self.assertEqual(self.transport.call_args.args, (self.target, [command, "--help"]))

    def test_toolbox_help_uses_the_configured_language(self):
        self.settings.data["language"] = "en"
        for arguments, expected in (
            (("ports", "show", "--help"), "Show the processes using a port"),
            (("which", "--help"), "Trace command candidates"),
            (("net", "check", "--help"), "Probe direct DNS"),
        ):
            code, output, errors = self.invoke(*arguments)
            self.assertEqual((code, errors), (0, ""))
            self.assertIn(expected, output)
            self.assertNotIn("端口", output)
            self.assertNotIn("目标", output)

    def test_invalid_inputs_have_structured_errors_before_any_probe(self):
        for arguments in (
            ("ports", "show", "0"),
            ("ports", "show", "65536"),
            ("which", "../python"),
            ("which", "python --version"),
            ("net", "check", "host"),
            ("net", "check", "http://host:0"),
            ("net", "check", "https://SECRET:password@host"),
            ("net", "check", "ftp://host"),
            ("net", "check", "http://host", "--timeout", "0"),
        ):
            code, output, errors = self.invoke(*arguments, "--json")
            self.assertEqual(code, 2, output + errors)
            self.assertEqual(json.loads(output)["error"]["code"], "invalid_arguments")
            self.transport.assert_not_called()

    def fixture(self, action):
        common = {
            "schema_version": CLI_SCHEMA_VERSION,
            "action": action,
            "environment": "windows" if self.target == "win" else "wsl",
            "complete": True,
            "warnings": [],
        }
        if action == "which":
            common.update(
                command="python",
                scope="path",
                found=True,
                candidates=[
                    {
                        "path": "/native/python",
                        "resolved": "/native/python",
                        "origin": "path",
                        "selected": True,
                    }
                ],
            )
        elif action == "ports":
            common.update(
                operation="show",
                port=3000,
                protocol="all",
                entries=[
                    {
                        "protocol": "tcp",
                        "family": "ipv4",
                        "address": "127.0.0.1",
                        "port": 3000,
                        "pid": 10,
                        "process": "node",
                        "executable": None,
                        "projects": [],
                    }
                ],
            )
        else:
            common.update(
                operation="check",
                target="127.0.0.1:3000",
                connection_mode="direct",
                timeout=5,
                healthy=True,
                proxies=[],
                stages=[
                    {"stage": stage, "status": "ok", "code": "ok", "duration_ms": 1}
                    for stage in ("dns", "tcp")
                ],
            )
        return common

    def test_cross_environment_queries_preserve_schema_and_target_identity(self):
        for action, arguments in (
            ("which", ("which", "python")),
            ("ports", ("ports", "show", "3000")),
            ("net", ("net", "check", "127.0.0.1:3000")),
        ):
            value = self.fixture(action)
            remote = subprocess.CompletedProcess([], 0, json.dumps(value), "")
            for invocation in (("-e", self.target, *arguments), (*arguments, "-e", self.target)):
                code, output, errors = self.invoke(*invocation, "--json", remote=remote)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(json.loads(output), value)
                self.assertEqual(self.transport.call_args.args[0], self.target)
                self.assertIn("--json", self.transport.call_args.args[1])
                self.assertNotIn("--config", self.transport.call_args.args[1])

    def test_remote_malformed_nested_fields_fail_without_tracebacks(self):
        for action, arguments, mutate in (
            (
                "which",
                ("which", "python"),
                lambda value: value["candidates"][0].update(selected="yes"),
            ),
            (
                "which",
                ("which", "python"),
                lambda value: value["candidates"][0].pop("resolved"),
            ),
            (
                "ports",
                ("ports", "show", "3000"),
                lambda value: value["entries"][0].update(protocol=[]),
            ),
            ("net", ("net", "check", "127.0.0.1:3000"), lambda value: value.update(proxies=[1])),
            (
                "net",
                ("net", "check", "127.0.0.1:3000"),
                lambda value: value["stages"][0].update(addresses=[{}]),
            ),
        ):
            value = self.fixture(action)
            mutate(value)
            result = subprocess.CompletedProcess([], 0, json.dumps(value), "")
            code, output, errors = self.invoke(
                *arguments, "-e", self.target, "--json", remote=result
            )
            self.assertEqual((code, errors), (1, ""))
            self.assertEqual(json.loads(output)["error"]["code"], "target_response_invalid")

    def test_old_target_reports_unsupported_without_bootstrap(self):
        for arguments in (("which", "python"), ("ports", "list"), ("net", "info")):
            result = subprocess.CompletedProcess([], 2, "", "old parser error")
            code, output, errors = self.invoke(
                *arguments, "-e", self.target, "--json", remote=result
            )
            self.assertEqual((code, errors), (1, ""))
            self.assertEqual(json.loads(output)["error"]["code"], "target_command_unsupported")

    def test_native_which_does_not_execute_candidates_or_write_preferences(self):
        binary = self.root / ("fixture.exe" if os.name == "nt" else "fixture")
        binary.write_text("raise RuntimeError('must not execute')")
        binary.chmod(0o755)
        with (
            patch.dict(os.environ, {"PATH": str(self.root), "PATHEXT": ".EXE"}),
            patch("subprocess.run", side_effect=AssertionError("no execution")),
        ):
            code, output, errors = self.invoke("which", "fixture", "--json")
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output)["candidates"][0]["path"], str(binary))
        self.assertFalse(self.settings.path.exists())

    def test_path_order_deduplication_extensions_and_no_implicit_cwd(self):
        roots = [self.root / name for name in ("one", "two")]
        for root in roots:
            root.mkdir()
            (root / "tool.cmd").write_text("")
            (root / "tool.exe").write_text("")
        value = paths.collect_paths(
            "tool",
            environment={
                "PATH": ";".join(map(str, [*roots, roots[0]])),
                "PATHEXT": ".EXE;.CMD",
                "MISE_DATA_DIR": str(roots[0]),
            },
            windows=True,
        )
        self.assertEqual(
            [row["path"] for row in value["candidates"]],
            [str(root / name) for root in roots for name in ("tool.exe", "tool.cmd")],
        )
        self.assertEqual(
            [row["selected"] for row in value["candidates"]], [True, False, False, False]
        )
        self.assertEqual(value["candidates"][0]["origin"], "mise")
        self.assertFalse(paths.collect_paths("tool", environment={"PATH": ""})["found"])

    @unittest.skipIf(os.name == "nt", "POSIX executable permission semantics")
    def test_posix_lookup_skips_non_executable_and_resolves_links(self):
        executable = self.root / "real"
        executable.write_text("not executed")
        executable.chmod(0o755)
        (self.root / "tool").symlink_to(executable)
        value = paths.collect_paths("tool", environment={"PATH": str(self.root)})
        self.assertEqual(value["candidates"][0]["resolved"], str(executable))
        executable.chmod(0o644)
        self.assertFalse(paths.collect_paths("tool", environment={"PATH": str(self.root)})["found"])

    def test_proc_tables_decode_ipv4_ipv6_and_connected_udp(self):
        header = "sl local_address rem_address st tx rx tr uid timeout inode\n"
        ipv4 = "0: 0100007F:0BB8 00000000:0000 0A 0:0 00:0 0 0 0 123"
        ipv6 = "0: 00000000000000000000000001000000:0035 0:0 07 0:0 00:0 0 0 0 456"
        self.assertEqual(parse_table(header + ipv4, "tcp", False)[0]["address"], "127.0.0.1")
        self.assertEqual(parse_table(header + ipv6, "udp", True)[0]["address"], "::1")
        self.assertEqual(parse_table(header + ipv4.replace("0A", "01"), "tcp", False), [])
        self.assertEqual(
            parse_table(header + ipv4.replace("0A", "01"), "udp", False)[0]["port"], 3000
        )

    def test_port_attribution_checks_process_birth_and_ancestry(self):
        binding = SimpleNamespace(state=self.root)
        workers = self.root / "workers"
        workers.mkdir()
        record = workers / "web.json"
        record.write_text(json.dumps({"phase": "running", "pid": 10, "updated_at": 101}))
        processes = {
            "10": {"started": 100, "parent_pid": 1},
            "11": {"started": 102, "parent_pid": 10},
        }
        registry = Mock()
        registry.names.return_value = ["demo"]
        registry.load.return_value = binding
        with patch("dev_tools.toolbox.ports.Registry", return_value=registry):
            roots, warnings = ports.project_processes(processes, "windows")
            self.assertEqual(warnings, [])
            self.assertEqual(
                ports.process_projects(11, processes, roots),
                [{"project": "demo", "service": "web"}],
            )
            processes["10"]["started"] = 200
            roots, _ = ports.project_processes(processes, "windows")
            self.assertEqual(roots, {})
            self.assertEqual(ports.process_projects(11, processes, {10: ["wrong"]}), [])

    def test_damaged_registration_does_not_hide_other_project_owners(self):
        workers = self.root / "workers"
        workers.mkdir()
        (workers / "web.json").write_text(
            json.dumps({"phase": "running", "pid": 10, "updated_at": 101})
        )
        registry = Mock()
        registry.names.return_value = ["damaged", "valid"]
        registry.load.side_effect = [ValueError("damaged"), SimpleNamespace(state=self.root)]
        with patch("dev_tools.toolbox.ports.Registry", return_value=registry):
            roots, warnings = ports.project_processes({"10": {"started": 100}}, "windows")
        self.assertEqual(roots[10], [{"project": "valid", "service": "web"}])
        self.assertEqual(warnings, ["project_details_unavailable"])

    def test_wsl_project_identity_uses_cgroups_despite_wall_clock_drift(self):
        workers = self.root / "workers"
        workers.mkdir()
        (workers / "web.json").write_text(
            json.dumps({"phase": "running", "pid": 10, "updated_at": 100})
        )
        registry = Mock()
        registry.names.return_value = ["demo"]
        registry.load.return_value = SimpleNamespace(state=self.root)
        processes = {
            "11": {"started": 200, "parent_pid": 1, "units": ["dev-tools-worker@demo:web.service"]},
            "12": {"started": 101, "parent_pid": 1, "units": ["unrelated.service"]},
        }
        with (
            patch("dev_tools.toolbox.ports.Registry", return_value=registry),
            patch.dict(os.environ, {"DEV_TOOLS_TEST_TRANSIENT": "0"}),
        ):
            roots, warnings = ports.project_processes(processes, "wsl")
        self.assertEqual(warnings, [])
        self.assertEqual(
            ports.process_projects(11, processes, roots), [{"project": "demo", "service": "web"}]
        )
        self.assertEqual(ports.process_projects(12, processes, roots), [])

    def test_missing_port_owner_is_partial_not_a_guessed_project(self):
        snapshot = {
            "entries": [{"protocol": "tcp", "address": "127.0.0.1", "port": 3000, "pid": None}],
            "processes": {},
            "warnings": [],
        }
        with (
            patch(
                "dev_tools.toolbox.ports.native",
                return_value=SimpleNamespace(port_snapshot=lambda **_: copy.deepcopy(snapshot)),
            ),
            patch("dev_tools.toolbox.ports.project_processes", return_value=({}, [])),
        ):
            value = ports.collect_ports("wsl", port=3000)
            self.assertFalse(value["complete"])
            self.assertIn("socket_owner_unavailable", value["warnings"])
            self.assertEqual(value["entries"][0]["projects"], [])
            self.assertTrue(ports.collect_ports("wsl", port=4000)["complete"])

    def test_human_output_escapes_control_characters_from_processes(self):
        value = self.fixture("ports")
        value["entries"][0]["process"] = "node\x1b[2J\nSECRET"
        with contextlib.redirect_stdout(out := io.StringIO()), language_scope("zh"):
            display(value)
        self.assertNotIn("\x1b[2J", out.getvalue())
        self.assertIn("\\x1b[2J\\x0aSECRET", out.getvalue())


class NetworkTests(unittest.TestCase):
    def server(self, status=204):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                requests.append((self.command, self.path))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, requests

    def test_real_http_probe_uses_head_redacts_query_and_does_not_follow_redirect(self):
        for status in (204, 302, 503):
            port, requests = self.server(status)
            value = network.check_target(
                network.parse_target(f"http://127.0.0.1:{port}/health?token=SECRET"), 2
            )
            self.assertEqual(value["healthy"], status < 400)
            self.assertEqual(value["stages"][-1]["http_status"], status)
            self.assertEqual(requests, [("HEAD", "/health?token=SECRET")])
            self.assertNotIn("SECRET", json.dumps(value))
            self.assertEqual([row["stage"] for row in value["stages"]], ["dns", "tcp", "http"])

    def test_dns_timeout_is_bounded_and_skips_later_stages(self):
        with (
            patch(
                "dev_tools.toolbox.network.subprocess.run",
                side_effect=subprocess.TimeoutExpired([], 1),
            ) as child,
            patch("dev_tools.toolbox.network.connect") as connect,
        ):
            value = network.check_target(network.parse_target("example.test:443"), 1)
        self.assertFalse(value["healthy"])
        self.assertEqual(value["stages"][0]["code"], "timeout")
        self.assertEqual(child.call_args.args[0][1:3], ["-I", "-S"])
        self.assertEqual(child.call_args.kwargs["timeout"], 1)
        connect.assert_not_called()

    def test_tcp_refusal_and_invalid_tls_certificate_identify_the_failed_stage(self):
        addresses = [(socket.AF_INET, ("127.0.0.1", 1234))]
        with (
            patch("dev_tools.toolbox.network.resolve", return_value=addresses),
            patch("dev_tools.toolbox.network.connect", side_effect=ConnectionRefusedError),
        ):
            value = network.check_target(network.parse_target("localhost:1234"), 1)
        self.assertEqual(value["stages"][-1]["code"], "connection_refused")
        stream = Mock()
        stream.getpeername.return_value = ("127.0.0.1", 1234)
        context = Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError()
        with (
            patch("dev_tools.toolbox.network.resolve", return_value=addresses),
            patch("dev_tools.toolbox.network.connect", return_value=stream),
            patch("dev_tools.toolbox.network.ssl.create_default_context", return_value=context),
        ):
            value = network.check_target(network.parse_target("https://localhost:1234"), 1)
        self.assertEqual(value["stages"][-1]["stage"], "tls")
        self.assertEqual(value["stages"][-1]["code"], "certificate_invalid")
        stream.close.assert_called_once()

    def test_proxy_credentials_never_appear_and_invalid_values_are_safe(self):
        value = network.proxy_info(
            {
                "HTTPS_PROXY": "http://user:SECRET@localhost:8080/path?token=SECRET",
                "http_proxy": "http://[invalid",
            }
        )
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertEqual(value[0], {"variable": "http_proxy", "valid": False})
        self.assertTrue(value[1]["credentials"])
        self.assertEqual(value[1]["host"], "localhost")

    def test_http_header_trickle_cannot_extend_the_total_stage_deadline(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)

        def trickle():
            writer.recv(8192)
            try:
                writer.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                for _ in range(10):
                    time.sleep(0.2)
                    writer.sendall(b"x")
            except OSError:
                pass

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("localhost")
        connection.sock = reader
        self.addCleanup(connection.close)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            network.request_http(connection, reader, "/", 1)
        self.assertLess(time.monotonic() - started, 1.8)


if __name__ == "__main__":
    unittest.main()
