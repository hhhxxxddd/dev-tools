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
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
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
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("repository") != UPSTREAM
            or not isinstance(value.get("files"), dict)
        ):
            raise ValueError(message("unverified controller package: {path}", path=root))
        seen = set()
        for name, expected in value["files"].items():
            if (
                not isinstance(name, str)
                or "\\" in name
                or ":" in name
                or name.lower() in seen
                or PurePosixPath(name).as_posix() != name
                or any(part in {".", ".."} for part in PurePosixPath(name).parts)
                or not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
            ):
                raise ValueError(message("unverified controller package: {path}", path=root))
            seen.add(name.lower())
            target = root / name
            if (
                target.resolve().is_relative_to(root.resolve())
                and not any((root / part).is_symlink() for part in PurePosixPath(name).parents)
                and not target.is_symlink()
                and target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == expected
            ):
                continue
            raise ValueError(message("controller package checksum failed: {path}", path=name))
        for required in (
            "scripts/install.sh",
            "scripts/install-wsl.ps1",
            "scripts/install.ps1",
            "src/dev_tools/cli.py",
            "src/dev_tools/__init__.py",
            "pyproject.toml",
        ):
            if required not in value["files"]:
                raise ValueError(message("unverified controller package: {path}", path=root))
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata.get("project")
        version = re.search(
            r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
            (root / "src/dev_tools/__init__.py").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if (
            version is None
            or value.get("version") != version.group(1)
            or not isinstance(project, dict)
            or project.get("version") != version.group(1)
        ):
            raise ValueError(message("unverified controller package: {path}", path=root))
        for directory in ("src/dev_tools", "scripts", "config", "docs", "examples", "systemd"):
            for target in (root / directory).rglob("*"):
                if "__pycache__" in target.parts or target.suffix == ".pyc":
                    continue
                if target.is_symlink() or (
                    target.is_file() and target.relative_to(root).as_posix() not in value["files"]
                ):
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
        timeout=30,
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
        seen = set()
        for member in package.infolist():
            if (
                not (root / member.filename).resolve().is_relative_to(root.resolve())
                or member.filename.lower() in seen
                or "\\" in member.filename
                or ":" in member.filename
            ):
                raise ValueError(
                    message("unsafe controller archive member: {path}", path=member.filename)
                )
            seen.add(member.filename.lower())
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(
                    message("unsafe controller archive member: {path}", path=member.filename)
                )
        package.extractall(root)
    verify_source(root)
    receipt = json.loads((root / ".release.json").read_text(encoding="utf-8"))
    if receipt["version"] != tag[1:]:
        raise ValueError(message("unverified controller package: {path}", path=root))
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


def _refresh_checkout(source: Path) -> None:
    """Fetch only the verified origin, and never merge into local work."""
    verify_source(source)
    prefix = ["git", "-c", f"safe.directory={source.resolve()}", "-C", str(source)]

    def git(*arguments: str) -> str:
        result = subprocess.run(
            [*prefix, *arguments], capture_output=True, text=True, timeout=120, check=False
        )
        if result.returncode:
            raise ValueError(
                message("Source update failed; check Git origin, branch and network, then retry")
            )
        return result.stdout.strip()

    if git("status", "--porcelain"):
        raise ValueError(
            message("Source checkout has local changes; commit or stash them before self upgrade")
        )
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if git("config", f"branch.{branch}.remote") != "origin":
        raise ValueError(
            message("Source branch must track the verified origin before self upgrade")
        )
    reference = git("config", f"branch.{branch}.merge")
    if not reference.startswith("refs/heads/"):
        raise ValueError(
            message("Source branch must track the verified origin before self upgrade")
        )
    git("fetch", "--no-tags", "origin", reference)
    git("merge", "--ff-only", "FETCH_HEAD")
    verify_source(source)


def _scoop_installation() -> bool:
    root = REPOSITORY.resolve()
    return (
        WINDOWS
        and (root / "install.json").is_file()
        and root.parent.name == "dev-tools"
        and root.parent.parent.name == "apps"
    )


def _require_stopped_windows() -> None:
    from .projects.registry import Registry
    from .runtimes.platforms.windows import WindowsBackend

    registry = Registry("windows")
    for name in registry.names():
        backend = WindowsBackend(registry.load(name))
        if any(backend.active(worker) for worker in backend.known_workers()):
            raise ValueError(
                message(
                    "Stop Windows projects before upgrading the controller: dev-tools -e win stop {project}",
                    project=name,
                )
            )


def _upgrade(args: argparse.Namespace) -> int:
    if args.env == "win" and not WINDOWS:
        raise ValueError(message("Windows controller upgrades must run on Windows"))
    if args.env == "wsl" and not WINDOWS and os.geteuid() != 0:
        raise ValueError(message("controller deployment requires root; rerun with sudo"))
    if WINDOWS and args.env == "win":
        _require_stopped_windows()
    if args.env == "win" and not args.source and _scoop_installation():
        return subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; & scoop update dev-tools; exit $LASTEXITCODE",
            ],
            check=False,
        ).returncode
    source = Path(args.source).expanduser().resolve() if args.source else REPOSITORY
    if not args.source and not (source / ".git").exists():
        binding = source / ".source"
        if binding.is_file():
            source = Path(binding.read_text(encoding="utf-8").strip())
    if not (source / ".git").exists():
        if args.source or args.env == "win":
            raise ValueError(
                message("Source upgrade requires a verified Git checkout; use --source")
            )
        with tempfile.TemporaryDirectory(prefix="dev-tools-release-") as temporary:
            return _install_wsl(download_release(Path(temporary)), args.settings.distro)
    if WINDOWS and args.env == "wsl" and source.resolve() == REPOSITORY.resolve():
        # Fetching the shared checkout also replaces code used by Windows workers.
        _require_stopped_windows()
    _refresh_checkout(source)
    if args.env == "wsl":
        return _install_wsl(source, args.settings.distro)
    verify_source(source)
    environment = os.environ.copy()
    binding = REPOSITORY / ".host-python.win"
    if binding.is_file():
        environment["DEV_TOOLS_PYTHON"] = binding.read_text(encoding="utf-8").strip()
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(source / "scripts/install.ps1")],
        check=False,
        env=environment,
    ).returncode


def run(args: argparse.Namespace) -> int:
    if args.json and args.self_action != "status":
        raise ValueError(message("self --json requires status"))
    if args.source and args.self_action == "status":
        raise ValueError(message("self --source requires update or upgrade"))
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
    if args.self_action == "upgrade":
        return _upgrade(args)
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
    binding = REPOSITORY / ".source"
    if binding.is_file():
        return _install_wsl(Path(binding.read_text(encoding="utf-8").strip()), args.settings.distro)
    raise ValueError(
        message("Redeployment source is unavailable; use self update --source or self upgrade")
    )


def add_commands(catalog) -> None:
    upgrade = catalog.add_parser(
        "upgrade",
        help=t("获取新版并升级 dev-tools 自身"),
        group=t("辅助命令"),
        project=False,
    )
    add_environment(upgrade)
    upgrade.add_argument("--source", metavar=t("目录"), help=t("用于升级的可信 Git 仓库目录"))
    upgrade.set_defaults(func=run, self_action="upgrade", json=False)
    command = catalog.add_parser(
        "self",
        help=t("查看工具部署；self update 部署当前代码到 WSL"),
        group=t("辅助命令"),
        project=False,
    )
    add_environment(command)
    command.add_argument(
        "self_action",
        choices=("update", "upgrade", "status"),
        metavar=t("操作"),
        help=t("upgrade 获取新版并部署；update 部署当前代码到 WSL；status 查看部署状态"),
    )
    command.add_argument(
        "--source",
        metavar=t("目录"),
        help=t("操作源码目录；upgrade 要求可信 Git 仓库，update 也接受发行包"),
    )
    command.add_argument(
        "--json", action="store_true", help=t("以 JSON 输出部署状态，仅用于 status")
    )
    command.set_defaults(func=run)
