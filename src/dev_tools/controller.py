"""Installation of the controller, independent of project preparation and transport."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.error import HTTPError

from . import __version__
from .command_help import add_environment
from .i18n import language, message, t

REPOSITORY = Path(__file__).resolve().parents[2]
UPSTREAM = "hhhxxxddd/dev-tools"
WINDOWS = os.name == "nt"
WSL_PROBE = r"""set -eu
entry=/usr/local/bin/dev-tools
root=/opt/dev-tools
if [ -x "$entry" ] && [ "$(readlink -f "$entry")" = "$root/scripts/dev-tools" ] && [ -f "$root/src/dev_tools/cli.py" ] && [ -f /etc/systemd/system/dev-tools-worker@.service ] && [ -r "$root/.host-python" ] && [ -x "$(cat "$root/.host-python")" ]; then
  tr -d '\r' < "$root/src/dev_tools/__init__.py" | sed -n 's/^__version__ = "\([^"]*\)"$/\1/p'
else
  exit 3
fi
"""


def deployment_status(distro: str) -> dict:
    """Read installation files, without executing the installed controller or profiles."""
    command = ["bash", "-c", WSL_PROBE]
    if WINDOWS:
        if not shutil.which("wsl.exe"):
            return {"state": "unavailable", "version": None, "distro": distro}
        payload = base64.b64encode(WSL_PROBE.encode()).decode()
        command = [
            "wsl.exe",
            "-d",
            distro,
            "--",
            "bash",
            "-c",
            f"printf %s {payload} | base64 -d | bash",
        ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=False)
    except OSError, subprocess.TimeoutExpired:
        return {"state": "unavailable", "version": None, "distro": distro}
    version = result.stdout.decode("utf-8", errors="replace").strip()
    state = "installed" if result.returncode == 0 and version else "missing"
    if result.returncode not in (0, 3):
        state = "unavailable"
    return {"state": state, "version": version if state == "installed" else None, "distro": distro}


def deployment_notice(settings) -> str:
    status = deployment_status(settings.distro)
    if status["state"] == "installed":
        return ""
    instruction = "dev-tools self update -e wsl"
    if not WINDOWS:
        instruction = "sudo dev-tools self update -e wsl"
    if status["state"] == "missing":
        return t(
            "WSL deployment is missing or incomplete ({distro}). Deploy it with: {command}",
            distro=settings.distro,
            command=instruction,
        )
    return t(
        "Cannot check WSL deployment ({distro}); check WSL and the configured distro, then run: {command}",
        distro=settings.distro,
        command=instruction,
    )


def verify_source(root: Path) -> None:
    """A checkout must have the reviewed origin; Scoop/release payloads carry file hashes."""
    receipt = root / ".release.json"
    if receipt.is_file() and not (root / ".git").exists():
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if value.get("repository") != UPSTREAM or not isinstance(value.get("files"), dict):
            raise ValueError(message("unverified controller package: {path}", path=root))
        for name, expected in value["files"].items():
            target = root / name
            if (
                target.resolve().is_relative_to(root.resolve())
                and target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == expected
            ):
                continue
            raise ValueError(message("controller package checksum failed: {path}", path=name))
        for required in (
            "scripts/install.sh",
            "scripts/install-wsl.ps1",
            "src/dev_tools/__init__.py",
        ):
            if required not in value["files"]:
                raise ValueError(message("unverified controller package: {path}", path=root))
        return
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.resolve()}",
            "-C",
            str(root),
            "remote",
            "get-url",
            "origin",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not re.fullmatch(
        r"(?:git@github\.com:|https://github\.com/)hhhxxxddd/dev-tools(?:\.git)?",
        result.stdout.strip(),
    ):
        raise ValueError(message("unverified controller checkout: {path}", path=root))


def _read_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dev-tools/" + __version__})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def download_release(destination: Path) -> Path:
    try:
        release = json.loads(_read_url(f"https://api.github.com/repos/{UPSTREAM}/releases/latest"))
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise ValueError(
                message(
                    "No published controller release is available; deploy from Windows or use --source"
                )
            ) from None
        raise
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(message("unsupported release tag: {tag}", tag=tag))
    name = f"dev-tools-{tag[1:]}.zip"
    base = f"https://github.com/{UPSTREAM}/releases/download/{tag}/"
    checksums = _read_url(base + "SHA256SUMS").decode("ascii")
    expected = next(
        (line.split()[0] for line in checksums.splitlines() if line.split()[1:] == [name]),
        None,
    )
    archive = _read_url(base + name)
    if expected is None or hashlib.sha256(archive).hexdigest() != expected:
        raise ValueError(message("controller package checksum failed: {path}", path=name))
    archive_path = destination / name
    archive_path.write_bytes(archive)
    root = destination / "package"
    with zipfile.ZipFile(archive_path) as package:
        for member in package.infolist():
            if not (root / member.filename).resolve().is_relative_to(root.resolve()):
                raise ValueError(
                    message("unsafe controller archive member: {path}", path=member.filename)
                )
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(
                    message("unsafe controller archive member: {path}", path=member.filename)
                )
        package.extractall(root)
    verify_source(root)
    return root


def _install_wsl(source: Path, distro: str) -> int:
    verify_source(source)
    if WINDOWS:
        command = [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(source / "scripts/install-wsl.ps1"),
            "-Distro",
            distro,
            "-Language",
            language(),
        ]
    else:
        if os.geteuid() != 0:
            raise ValueError(message("controller deployment requires root; rerun with sudo"))
        command = ["bash", str(source / "scripts/install.sh")]
    environment = os.environ.copy()
    environment["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
    return subprocess.run(command, check=False, env=environment).returncode


def run(args: argparse.Namespace) -> int:
    if args.json and args.self_action != "status":
        raise ValueError(message("self --json requires status"))
    if args.source and args.self_action == "status":
        raise ValueError(message("self --source requires update"))
    if args.self_action == "status":
        value = {"controller_version": __version__, "wsl": deployment_status(args.settings.distro)}
        if args.json:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            print(t("Controller version: {version}", version=__version__))
            print(
                t(
                    "WSL deployment: {state}; version: {version}",
                    state=t(value["wsl"]["state"]),
                    version=value["wsl"]["version"] or "-",
                )
            )
        return 0
    if args.env == "win":
        raise ValueError(
            message("Use Scoop for Windows: scoop install dev-tools / scoop update dev-tools")
        )
    if not WINDOWS and os.geteuid() != 0:
        raise ValueError(message("controller deployment requires root; rerun with sudo"))
    if args.source:
        return _install_wsl(Path(args.source).expanduser().resolve(), args.settings.distro)
    if WINDOWS or (REPOSITORY / ".git").exists():
        return _install_wsl(REPOSITORY, args.settings.distro)
    with tempfile.TemporaryDirectory(prefix="dev-tools-release-") as temporary:
        return _install_wsl(download_release(Path(temporary)), args.settings.distro)


def add_commands(catalog) -> None:
    command = catalog.add_parser(
        "self",
        help=t("Install, update or inspect the controller deployment"),
        group=t("辅助命令"),
        project=False,
    )
    add_environment(command)
    command.add_argument(
        "self_action",
        choices=("update", "status"),
        metavar=t("操作"),
        help=t("update 安装或更新 WSL 部署；status 查看部署状态"),
    )
    command.add_argument(
        "--source",
        metavar=t("目录"),
        help=t("Verified checkout or release package to deploy into WSL"),
    )
    command.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    command.set_defaults(func=run)
