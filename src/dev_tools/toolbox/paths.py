"""Static PATH lookup. Never execute candidates, shims, profiles or project metadata."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def origin(path: Path, environment: dict) -> str:
    home = Path.home()
    roots = (
        ("mise", environment.get("MISE_DATA_DIR", str(home / ".local/share/mise"))),
        ("scoop", environment.get("SCOOP", str(home / "scoop"))),
        ("scoop", environment.get("SCOOP_GLOBAL", "")),
        ("venv", environment.get("VIRTUAL_ENV", "")),
    )
    for name, root in roots:
        if root and path.is_relative_to(Path(root).absolute()):
            return name + ("-shim" if path.parent.name == "shims" else "")
    return "path"


def collect_paths(
    command: str, *, environment: dict | None = None, windows: bool | None = None
) -> dict:
    environment = os.environ if environment is None else environment
    windows = os.name == "nt" if windows is None else windows
    extensions = [
        item.lower()
        for item in environment.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        if item
    ]
    names = [command]
    if windows and Path(command).suffix.lower() not in extensions:
        names = [command + extension for extension in extensions]
    candidates, warnings, seen = [], [], set()
    for folder in environment.get("PATH", "").split(";" if windows else ":"):
        # An explicit empty PATH component denotes cwd; never add cwd implicitly.
        if not environment.get("PATH"):
            break
        root = Path(folder.strip('"') or ".").absolute()
        for name in names:
            path = root / name
            key = str(path).casefold() if windows else str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                metadata = path.lstat()
                app_alias = windows and getattr(metadata, "st_reparse_tag", 0) == 0x8000001B
                if not app_alias and (
                    not stat.S_ISREG(path.stat().st_mode)
                    or not windows
                    and not os.access(path, os.X_OK)
                ):
                    continue
                resolved = None if app_alias else str(path.resolve(strict=True))
                source = "windows-app-alias" if app_alias else origin(path, environment)
                candidates.append(
                    {
                        "path": str(path),
                        "resolved": resolved,
                        "origin": source,
                        "selected": not candidates,
                    }
                )
            except FileNotFoundError:
                continue
            except OSError, ValueError:
                warnings.append("path_unreadable")
    return {
        "command": command,
        "candidates": candidates,
        "found": bool(candidates),
        "scope": "path",
        "warnings": sorted(set(warnings)),
        "complete": not warnings,
    }
