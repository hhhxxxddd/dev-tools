"""Native storage and identity selection; declarations never contain these values."""

from __future__ import annotations

import os
from pathlib import Path

from ...i18n import message
from ...projects.models import ProjectError


def storage(environment: str) -> tuple[Path, Path]:
    if environment == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "dev-tools"
        registry, state = base / "projects.d", base / "state"
    elif environment == "wsl":
        registry, state = Path("/etc/dev-tools/projects.d"), Path("/var/lib/dev-tools")
    else:
        raise ProjectError(message("environment must be windows or wsl"))
    return (
        Path(os.environ.get("DEV_TOOLS_REGISTRY_ROOT", registry)).resolve(),
        Path(os.environ.get("DEV_TOOLS_STATE_ROOT", state)).resolve(),
    )


def workspace(
    environment: str, source: Path, name: str, user: str
) -> tuple[Path, str, Path | None]:
    if environment == "windows":
        if user:
            raise ProjectError(message("--user is only available in WSL"))
        return source, "", None
    import pwd

    user = user or os.environ.get("SUDO_USER") or pwd.getpwuid(os.geteuid()).pw_name
    try:
        home = Path(pwd.getpwnam(user).pw_dir)
    except KeyError as exc:
        raise ProjectError(message("unknown native user: {user}", user=user)) from exc
    cache = Path(os.environ.get("DEV_TOOLS_CACHE_ROOT", home / ".cache/dev-tools/build")).resolve()
    if cache == Path(cache.anchor):
        raise ProjectError(message("native cache root cannot be the filesystem root"))
    if len(cache.parts) > 2 and cache.parts[1] == "mnt" and len(cache.parts[2]) == 1:
        raise ProjectError(message("WSL workspace must be on the native Linux filesystem"))
    path = cache / name
    if path == source or source in path.parents or path in source.parents:
        raise ProjectError(message("source and WSL workspace must not overlap"))
    return path, user, cache


def require_registration_control(environment: str) -> None:
    if environment == "wsl" and os.geteuid() != 0:
        raise ProjectError(message("WSL registration requires root; run this command with sudo"))
