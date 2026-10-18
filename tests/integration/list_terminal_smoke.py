"""Real WSL PTY keys, save/cancel and terminal restoration in an isolated home."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HARNESS = """
import sys
from pathlib import Path
from unittest.mock import patch
from dev_tools.cli import main
from dev_tools.settings import Settings, defaults
root=Path(sys.argv[1])
settings=Settings(root/'config.toml','missing',defaults(),{})
settings.data['language']='en'
with patch('dev_tools.cli.load_settings',return_value=settings), patch('dev_tools.ssh.native_identity',return_value=(root/'home','fixture-user',root/'system-config')), patch('dev_tools.ssh_list.default_path',return_value=root/'preferences/config.toml'):
    main(['ssh','list','--edit'])
"""


def smoke():
    if os.name == "nt":
        raise RuntimeError(
            "run in native WSL; ssh_smoke.py covers Windows and bidirectional transport"
        )
    import fcntl
    import pty
    import select
    import struct
    import termios

    with tempfile.TemporaryDirectory(prefix="dev-tools-list-terminal-") as directory:
        root = Path(directory)
        config = root / "home/.ssh/config"
        config.parent.mkdir(parents=True)
        config.write_bytes(b"# keep\r\nHost fixture\r\n User old\r\n")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        environment.pop("DEV_TOOLS_TERMINAL", None)
        for keys, expected, code in (
            (b"\x1b[C\x1b[Ci\x15pty-user\rsy", b"User pty-user", 0),
            (b"lli\x15discarded\x1bq", b"User pty-user", 0),
            (b"\x03", b"User pty-user", 130),
        ):
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            original = termios.tcgetattr(slave)
            process = subprocess.Popen(
                [sys.executable, "-c", HARNESS, str(root)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
            )
            output, sent, deadline = b"", False, time.monotonic() + 20
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(output.decode(errors="replace"))
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                    if not sent and b"s/:w" in output:
                        os.write(master, keys)
                        sent = True
                while select.select([master], [], [], 0)[0]:
                    output += os.read(master, 65536)
                assert process.returncode == code, output.decode(errors="replace")
                assert termios.tcgetattr(slave) == original, "terminal mode was not restored"
                assert b"\x1b[?1049l" in output, "alternate screen was not restored"
                assert expected in config.read_bytes(), config.read_bytes()
                assert b"discarded" not in config.read_bytes()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
                os.close(slave)
    return {
        "native_pty": True,
        "arrow_keys_save": True,
        "escape_cancel": True,
        "terminal_restored": True,
        "isolated_home": True,
    }


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
