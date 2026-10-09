"""WSL invoking-user preferences, including sudo; imported only on the WSL side."""

from __future__ import annotations

import os
import pwd
from pathlib import Path


def invoking_user():
    sudo_user = os.environ.get("SUDO_USER") if os.geteuid() == 0 else None
    return pwd.getpwnam(sudo_user) if sudo_user else pwd.getpwuid(os.getuid())


def user_config_directory() -> Path:
    user = invoking_user()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg and Path(xdg).is_absolute() else Path(user.pw_dir) / ".config"


def own_user_file(path: Path, created_directories: list[Path]) -> None:
    if os.geteuid() == 0:
        user = invoking_user()
        os.chown(path, user.pw_uid, user.pw_gid)
        for directory in created_directories:
            os.chown(directory, user.pw_uid, user.pw_gid)


def editor_identity(command: list[str]) -> list[str]:
    user = invoking_user()
    return (
        ["sudo", "-u", user.pw_name, "--", *command]
        if os.geteuid() == 0 and user.pw_uid
        else command
    )
