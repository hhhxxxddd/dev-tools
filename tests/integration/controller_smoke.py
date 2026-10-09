"""Exercise Windows -> WSL deployment in isolated native directories, without new runtimes."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from dev_tools.release import REPOSITORY, build


def wsl(script: str) -> str:
    payload = base64.b64encode(script.encode()).decode()
    result = subprocess.run(
        [
            "wsl.exe",
            "-d",
            os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu"),
            "-u",
            "root",
            "--",
            "bash",
            "-c",
            f"printf %s {payload} | base64 -d | bash -e",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Run this transport check on Windows with WSL available")
    native = wsl("mktemp -d /tmp/dev-tools-controller-XXXXXXXX")
    if not re.fullmatch(r"/tmp/dev-tools-controller-[A-Za-z0-9]+", native):
        raise ValueError("Unexpected isolated native directory")
    try:
        # Reuse the installed native controller Python; fake package-manager/systemd
        # adapters record/check installer calls rather than changing real services.
        wsl(f"""set -eu
mkdir -p {native}/fake {native}/config/projects.d {native}/state
cat > {native}/fake/mise <<'EOF'
#!/bin/sh
test "$1" = --no-config || exit 1
shift
case "$1" in
  --yes) test "$2" = install && test "$3" = python@3.14.8 ;;
  where) dirname "$(dirname "$(cat /opt/dev-tools/.host-python)")" ;;
  *) exit 1 ;;
esac
EOF
cat > {native}/fake/systemctl <<'EOF'
#!/bin/sh
case "$1" in daemon-reload|list-units) exit 0 ;; *) exit 1 ;; esac
EOF
chmod 0755 {native}/fake/*
printf 'export PATH={native}/fake:$PATH\n' > {native}/shell-env
printf keep-registration > {native}/config/projects.d/existing.json
printf keep-state > {native}/state/existing
""")
        with tempfile.TemporaryDirectory(prefix="dev-tools-controller-") as temporary:
            output = Path(temporary)
            built = build(REPOSITORY, output / "dist")
            package = output / "package with spaces"
            with zipfile.ZipFile(built["archive"]) as archive:
                archive.extractall(package)
            environment = os.environ.copy()
            variables = {
                "DEV_TOOLS_INSTALL_ROOT": native + "/deployment",
                "DEV_TOOLS_COMMAND_ROOT": native + "/bin",
                "DEV_TOOLS_UNIT_DIR": native + "/units",
                "DEV_TOOLS_CONFIG_ROOT": native + "/config",
                "DEV_TOOLS_STATE_ROOT": native + "/state",
                "BASH_ENV": native + "/shell-env",
            }
            environment.update(variables)
            environment["WSLENV"] = ":".join(
                [
                    part
                    for part in environment.get("WSLENV", "").split(":")
                    if part.split("/")[0] not in variables
                ]
                + [name + "/u" for name in variables]
            ).strip(":")
            for action in ("install", "update"):
                result = subprocess.run(
                    [
                        "pwsh",
                        "-NoProfile",
                        "-NonInteractive",
                        "-File",
                        str(REPOSITORY / "scripts/dev-tools.ps1"),
                        "self",
                        action,
                        "-e",
                        "wsl",
                        "--source",
                        str(package),
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=90,
                )
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
            result = wsl(f"""set -eu
test "$(cat {native}/config/projects.d/existing.json)" = keep-registration
test "$(cat {native}/state/existing)" = keep-state
test -f {native}/deployment/docs/installation.md
test -f {native}/deployment/docs/installation.en.md
{native}/bin/dev-tools --version
{native}/bin/dev-tools self status --json
""")
            print(result)
            print("Isolated package install/update preserved registrations and state")
    finally:
        # Check the resolved native target before removing only this owned fixture.
        wsl(f"""set -eu
test "$(readlink -f {native})" = {native}
test "$(dirname {native})" = /tmp
rm -rf -- {native}
""")


if __name__ == "__main__":
    main()
