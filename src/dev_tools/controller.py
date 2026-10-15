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
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError

from . import __version__
from .command_help import add_environment
from .i18n import language, message, render, t
from .presentation import warning

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
        result = subprocess.run(
            command, capture_output=True, timeout=5, check=False, stdin=subprocess.DEVNULL
        )
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
        if status.get("version") and status["version"] != __version__:
            from .runtimes.diagnostics import version_notice

            return version_notice("wsl", status["version"])
        if not WINDOWS and shutil.which("pwsh.exe"):
            from .runtimes.diagnostics import target_status, version_notice

            windows = target_status("win", settings)
            if windows.get("version") and windows["version"] != __version__:
                return version_notice("win", windows["version"])
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


def _install_wsl(source: Path, distro: str, *, yes: bool = False) -> int:
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
        if yes:
            command.append("-Yes")
    else:
        if os.geteuid() != 0:
            raise ValueError(message("controller deployment requires root; rerun with sudo"))
        command = [sys.executable, "-P", "-m", "dev_tools.controller_update", str(source)]
        if yes:
            command.append("--yes")
    environment = os.environ.copy()
    environment["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
    if not WINDOWS:
        environment["PYTHONPATH"] = str(source / "src")
    return subprocess.run(command, check=False, env=environment).returncode


def clear_controller_bytecode(source: Path) -> None:
    boundary = (source / "src/dev_tools").resolve()
    if not boundary.is_relative_to(source.resolve()):
        raise ValueError(message("Invalid controller installation target"))
    for cache in boundary.rglob("__pycache__"):
        if cache.is_dir() and not cache.is_symlink() and cache.resolve().is_relative_to(boundary):
            shutil.rmtree(cache)


def _refresh_checkout(source: Path, *, before_merge=None) -> None:
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
    git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    if before_merge is not None:
        before_merge()
    git("merge", "--ff-only", "FETCH_HEAD")
    clear_controller_bytecode(source)
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


def _run_windows_installer(source: Path) -> int:
    environment = os.environ.copy()
    environment["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
    binding = REPOSITORY / ".host-python.win"
    if binding.is_file():
        environment["DEV_TOOLS_PYTHON"] = binding.read_text(encoding="utf-8").strip()
    command = [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(source / "scripts/install.ps1"),
    ]
    if _scoop_installation():
        command.append("-NoProfile")
    return subprocess.run(command, check=False, env=environment).returncode


def _update_windows(args) -> int:
    from .controller_update import UpdateSession, update_with_recovery

    source = Path(args.source).expanduser().resolve() if args.source else REPOSITORY
    verify_source(source)
    if _scoop_installation() and source.resolve() != REPOSITORY.resolve():
        raise ValueError(
            message(
                "Scoop installations redeploy their installed package; use upgrade to change versions"
            )
        )
    session = UpdateSession(
        yes=getattr(args, "yes", False), entry=REPOSITORY / "scripts/dev-tools.ps1"
    )

    def deploy():
        clear_controller_bytecode(source)
        code = _run_windows_installer(source)
        if code == 0:
            session.entry = source / "scripts/dev-tools.ps1"
        return code

    return update_with_recovery(session, deploy)


def _show_status(args, deployment=None) -> int:
    # This command has its own versioned shape; controller_version is always the
    # selected controller, not the forwarding caller's version.
    deployment = deployment or {
        "state": "installed",
        "version": __version__,
        "path": str(REPOSITORY),
    }
    if args.env == "wsl":
        deployment = {**deployment, "distro": args.settings.distro}
    value = {
        "schema_version": 1,
        "environment": "windows" if args.env == "win" else "wsl",
        "controller_version": deployment.get("version"),
        "deployment": deployment,
    }
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(t("Controller target: {environment}", environment=args.env))
        print(t("Controller version: {version}", version=value["controller_version"] or "-"))
        state = t("Controller deployment: {state}", state=t(deployment["state"]))
        print(warning(state) if deployment["state"] != "installed" else state)
        if deployment.get("diagnostic"):
            print(warning(render(deployment["diagnostic"])))
        if deployment.get("path"):
            print(t("Controller directory: {path}", path=deployment["path"]))
    return 0


def _upgrade(args: argparse.Namespace) -> int:
    from .controller_update import UpdateSession, update_with_recovery

    if args.env == "wsl" and not WINDOWS and os.geteuid() != 0:
        raise ValueError(message("controller deployment requires root; rerun with sudo"))
    yes = getattr(args, "yes", False)
    if args.env == "win" and _scoop_installation():
        if args.source:
            raise ValueError(
                message(
                    "Scoop upgrades do not accept --source; run dev-tools upgrade without --source"
                )
            )
        session = UpdateSession(yes=yes, scoop=True)
        return update_with_recovery(
            session,
            lambda: (
                subprocess.run(
                    [
                        "pwsh",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "$ErrorActionPreference='Stop'; & scoop update dev-tools; exit $LASTEXITCODE",
                    ],
                    check=False,
                ).returncode
            ),
        )
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
            return _install_wsl(download_release(Path(temporary)), args.settings.distro, yes=yes)
    verify_source(source)

    if not WINDOWS and re.match(r"^/mnt/[a-zA-Z]/", source.as_posix()):
        # Windows coordinates both sides before changing a shared checkout.
        from .runtimes.router import _windows_path, forward_remote

        arguments = [
            "-Distro",
            args.settings.distro,
            "upgrade",
            "-e",
            "wsl",
            "--source",
            _windows_path(source),
        ]
        if yes:
            arguments.append("--yes")
        return forward_remote("win", arguments, settings=args.settings, timeout=None)

    session = None
    if WINDOWS and (args.env == "win" or source.resolve() == REPOSITORY.resolve()):
        session = UpdateSession(yes=yes, entry=REPOSITORY / "scripts/dev-tools.ps1")

    def deploy():
        _refresh_checkout(source, before_merge=session.stop if session is not None else None)
        if args.env == "wsl":
            return _install_wsl(source, args.settings.distro, yes=yes)
        code = _run_windows_installer(source)
        if code == 0 and session is not None:
            session.entry = source / "scripts/dev-tools.ps1"
        return code

    if session is not None:
        return update_with_recovery(session, deploy, stop=False)
    return deploy()


def run(args: argparse.Namespace) -> int:
    if args.json and args.self_action != "status":
        raise ValueError(message("self --json requires status"))
    if args.source and args.self_action in {"status", "recover"}:
        raise ValueError(message("self --source requires update or upgrade"))
    native = "win" if WINDOWS else "wsl"
    if args.env != native and (
        args.self_action == "status"
        or args.env == "win"
        or args.source
        and args.source.startswith("/")
    ):
        from .runtimes.diagnostics import forward_command, target_status

        if args.self_action == "status":
            status = target_status(args.env, args.settings)
            if status["state"] != "installed":
                return _show_status(args, status)
        arguments = ["upgrade"] if args.command == "upgrade" else ["self", args.self_action]
        if args.source:
            arguments.extend(["--source", args.source])
        if getattr(args, "yes", False):
            arguments.append("--yes")
        if args.json:
            arguments.append("--json")
        return forward_command(args.env, arguments, args.settings, as_json=args.json)
    if args.self_action == "recover":
        from .controller_update import recover_controller, recovery_path
        from .runtimes.platforms.locking import operation_lock

        if args.env == "wsl" and WINDOWS:
            return subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(REPOSITORY / "scripts/install-wsl.ps1"),
                    "-Distro",
                    args.settings.distro,
                    "-Language",
                    language(),
                    "-Recover",
                ],
                check=False,
            ).returncode
        if not WINDOWS and os.geteuid() != 0:
            raise ValueError(message("controller deployment requires root; rerun with sudo"))
        with operation_lock(recovery_path().parent, timeout=15):
            return recover_controller()
    if args.self_action == "status":
        return _show_status(args)
    if args.self_action == "upgrade":
        return _upgrade(args)
    if args.env == "win":
        return _update_windows(args)
    if not WINDOWS and os.geteuid() != 0:
        raise ValueError(message("controller deployment requires root; rerun with sudo"))
    if args.source:
        return _install_wsl(
            Path(args.source).expanduser().resolve(),
            args.settings.distro,
            yes=getattr(args, "yes", False),
        )
    if WINDOWS or (REPOSITORY / ".git").exists():
        return _install_wsl(REPOSITORY, args.settings.distro, yes=getattr(args, "yes", False))
    binding = REPOSITORY / ".source"
    if binding.is_file():
        return _install_wsl(
            Path(binding.read_text(encoding="utf-8").strip()),
            args.settings.distro,
            yes=getattr(args, "yes", False),
        )
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
    upgrade.add_argument(
        "-y", "--yes", action="store_true", help=t("确认停止受影响项目，更新后恢复运行")
    )
    upgrade.set_defaults(func=run, self_action="upgrade", json=False)
    command = catalog.add_parser(
        "self",
        help=t("查看、重新部署、升级或恢复目标环境的工具"),
        group=t("辅助命令"),
        project=False,
    )
    add_environment(command)
    command.add_argument(
        "self_action",
        choices=("update", "upgrade", "status", "recover"),
        metavar=t("操作"),
        help=t(
            "upgrade 获取新版并部署；update 重新部署当前代码；status 查看目标部署；recover 重试恢复运行"
        ),
    )
    command.add_argument(
        "--source",
        metavar=t("目录"),
        help=t("操作源码目录；upgrade 要求可信 Git 仓库，update 也接受发行包"),
    )
    command.add_argument(
        "--json", action="store_true", help=t("以 JSON 输出部署状态，仅用于 status")
    )
    command.add_argument(
        "-y", "--yes", action="store_true", help=t("确认停止受影响项目，更新后恢复运行")
    )
    command.set_defaults(func=run)
