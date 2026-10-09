"""One validated, user-local configuration for the controller's own preferences."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .i18n import language_scope, message, render, t

SECTIONS = ("system", "directories", "tools")
COLLECTORS = ("git", "mise", "winget", "store", "scoop", "apt", "snap", "rustup", "npm", "wsl")
COMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
TEMPLATE = Path(__file__).resolve().parents[2] / "config/settings.example.toml"


class SettingsError(ValueError):
    def __init__(self, value, *, language: str = "zh"):
        super().__init__(value)
        self.language = language


@dataclass(frozen=True)
class Settings:
    path: Path
    state: str
    data: dict
    origins: dict[str, str]
    errors: tuple[object, ...] = ()

    @property
    def language(self) -> str:
        return self.data["language"]

    @property
    def distro(self) -> str:
        return os.environ.get("DEV_TOOLS_DISTRO") or self.data["wsl"]["distro"]

    def as_dict(self) -> dict:
        data = copy.deepcopy(self.data)
        editor, origin = resolve_editor(self)
        data["editor"] = editor
        data["wsl"]["distro"] = self.distro
        origins = {**self.origins, "editor": origin}
        if os.environ.get("DEV_TOOLS_DISTRO"):
            origins["wsl.distro"] = "DEV_TOOLS_DISTRO"
        return {
            "path": str(self.path),
            "state": self.state,
            "settings": data,
            "origins": origins,
            "errors": [str(item) for item in self.errors],
        }


def defaults() -> dict:
    tools = ["git", "mise", "node", "python", "uv", "pnpm"]
    tools.extend(["winget", "scoop", "wsl"] if os.name == "nt" else ["python3"])
    return {
        "language": "zh",
        "editor": [],
        "wsl": {"distro": "Ubuntu"},
        "sysinfo": {
            "sections": list(SECTIONS),
            "show_missing": True,
            "tools": [{"command": command, "description": ""} for command in tools],
            "directories": [],
        },
        "report": {
            "roots": [],
            "max_depth": 2,
            "timeout": 90,
            "refresh": True,
            "collectors": list(COLLECTORS),
        },
    }


def default_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / (
            "dev-tools/config.toml"
        )
    from .runtimes.platforms.preferences import user_config_directory

    return user_config_directory() / "dev-tools/config.toml"


def settings_path(explicit: str | Path | None = None) -> Path:
    return (
        Path(explicit or os.environ.get("DEV_TOOLS_CONFIG") or default_path())
        .expanduser()
        .resolve()
    )


def native_path(value: str, base: Path) -> Path:
    if os.name != "nt" and (value == "~" or value.startswith("~/")):
        from .runtimes.platforms.preferences import invoking_user

        path = Path(invoking_user().pw_dir) / value.removeprefix("~").lstrip("/")
    else:
        path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def split_config(arguments: list[str]) -> tuple[str | None, list[str]]:
    """Resolve the file before building localized help; transport never shares this path."""
    explicit = None
    forwarded = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            forwarded.extend(arguments[index:])
            break
        if token == "--config":
            index += 1
            if index == len(arguments) or arguments[index].startswith("-"):
                raise SettingsError(message("--config requires a file path"))
            explicit = arguments[index]
        elif token.startswith("--config="):
            explicit = token.split("=", 1)[1]
            if not explicit:
                raise SettingsError(message("--config requires a file path"))
        else:
            forwarded.append(token)
        index += 1
    return explicit, forwarded


def _invalid(field: str):
    raise SettingsError(message("invalid setting: {field}", field=field))


def _table(value: object, allowed: set[str], field: str) -> dict:
    if not isinstance(value, dict):
        _invalid(field)
    if set(value) - allowed:
        _invalid(field + "." + min(set(value) - allowed))
    return value


def _strings(value: object, field: str, choices: tuple[str, ...] | None = None) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or not item.isprintable() for item in value
    ):
        _invalid(field)
    if choices is not None and (
        len(set(value)) != len(value) or any(v not in choices for v in value)
    ):
        _invalid(field)
    return value


def _description(value: object, field: str) -> str | dict:
    if isinstance(value, str):
        return "".join(char for char in value if char.isprintable())[:200]
    table = _table(value, {"zh", "en"}, field)
    if any(not isinstance(v, str) for v in table.values()):
        _invalid(field)
    return {key: _description(v, field) for key, v in table.items()}


def _validate(raw: dict, data: dict, origins: dict, source: str) -> None:
    _table(raw, set(data), "config")
    if "language" in raw and raw["language"] not in ("zh", "en"):
        _invalid("language")
    if "editor" in raw:
        _strings(raw["editor"], "editor")
    for key in ("language", "editor"):
        if key in raw:
            data[key] = raw[key]
            origins[key] = source
    for section in ("wsl", "sysinfo", "report"):
        value = _table(raw.get(section, {}), set(data[section]), section)
        for key, item in value.items():
            field = section + "." + key
            if key in {"show_missing", "refresh"}:
                if not isinstance(item, bool):
                    _invalid(field)
            elif key in {"max_depth", "timeout"}:
                high = 5 if key == "max_depth" else 600
                low = 0 if key == "max_depth" else 1
                if type(item) is not int or not low <= item <= high:
                    _invalid(field)
            elif key == "distro":
                if not isinstance(item, str) or not COMMAND.fullmatch(item):
                    _invalid(field)
            elif key in {"roots", "sections", "collectors"}:
                _strings(item, field, {"sections": SECTIONS, "collectors": COLLECTORS}.get(key))
            elif key in {"tools", "directories"}:
                if not isinstance(item, list):
                    _invalid(field)
                entries = []
                for index, entry in enumerate(item):
                    location = f"{field}[{index}]"
                    allowed = {"command", "description"} | (
                        {"path"} if key == "directories" else set()
                    )
                    _table(entry, allowed, location)
                    command = entry.get("command", "")
                    if key == "tools" and (
                        not isinstance(command, str) or not COMMAND.fullmatch(command)
                    ):
                        _invalid(location + ".command")
                    if not isinstance(command, str) or (command and not COMMAND.fullmatch(command)):
                        _invalid(location + ".command")
                    normalized = {
                        "command": command,
                        "description": _description(
                            entry.get("description", ""), location + ".description"
                        ),
                    }
                    if key == "directories":
                        directory = entry.get("path")
                        if (
                            not isinstance(directory, str)
                            or not directory
                            or not directory.isprintable()
                        ):
                            _invalid(location + ".path")
                        normalized["path"] = directory
                    entries.append(normalized)
                if key == "tools" and len({entry["command"] for entry in entries}) != len(entries):
                    _invalid(field)
                item = entries
            data[section][key] = item
            origins[field] = source


def load_settings(path: str | Path | None = None, *, tolerate_invalid: bool = False) -> Settings:
    path = settings_path(path)
    data = defaults()
    origins = {"language": "default", "editor": "default"}
    origins.update(
        {
            f"{section}.{key}": "default"
            for section in ("wsl", "sysinfo", "report")
            for key in data[section]
        }
    )
    raw = {}
    try:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return Settings(path, "missing", data, origins)
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(
                message(
                    "invalid TOML configuration: {path} (line {line}, column {column})",
                    path=path,
                    line=exc.lineno,
                    column=exc.colno,
                )
            ) from None
        _validate(raw, data, origins, str(path))
    except (OSError, UnicodeError) as exc:
        error = SettingsError(message("cannot read configuration: {path}", path=path))
        if not tolerate_invalid:
            raise error from exc
        return Settings(path, "invalid", defaults(), origins, (error.args[0],))
    except SettingsError as exc:
        selected = raw.get("language", "zh")
        exc.language = selected if selected in ("zh", "en") else "zh"
        if not tolerate_invalid:
            raise
        fallback = defaults()
        fallback["language"] = exc.language
        return Settings(
            path, "invalid", fallback, {key: "default" for key in origins}, (exc.args[0],)
        )
    return Settings(path, "loaded", data, origins)


def resolve_editor(settings: Settings) -> tuple[list[str], str]:
    if settings.data["editor"]:
        return settings.data["editor"], str(settings.path)
    for name in ("VISUAL", "EDITOR"):
        if os.environ.get(name):
            try:
                tokens = shlex.split(os.environ[name], posix=os.name != "nt")
            except ValueError:
                raise SettingsError(
                    message("invalid editor setting: {field}", field=name),
                    language=settings.language,
                ) from None
            if os.name == "nt":
                tokens = [
                    token[1:-1] if token[:1] == token[-1:] == '"' else token for token in tokens
                ]
            if tokens:
                return tokens, name
    return (["notepad.exe"] if os.name == "nt" else ["vi"]), "default"


def render_settings(data: dict) -> str:
    def encode(value):
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return "[" + ", ".join(encode(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{ " + ", ".join(f"{key} = {encode(item)}" for key, item in value.items()) + " }"
        return str(value)

    lines = []
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {encode(value)}")
    for section, fields in data.items():
        if isinstance(fields, dict):
            lines.extend(["", f"[{section}]"])
            lines.extend(f"{key} = {encode(value)}" for key, value in fields.items())
    return "\n".join(lines) + "\n"


def edit_settings(settings: Settings) -> int:
    path = settings.path
    if not path.exists():
        missing = []
        directory = path.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        created = []
        for directory in reversed(missing):
            try:
                directory.mkdir()
                created.append(directory)
            except FileExistsError:
                pass
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(TEMPLATE.read_text(encoding="utf-8"))
            if os.name != "nt":
                from .runtimes.platforms.preferences import own_user_file

                own_user_file(path, created)
        except FileExistsError:
            pass
    argv, _ = resolve_editor(settings)
    executable = shutil.which(argv[0])
    if executable is None:
        raise SettingsError(
            message("editor not found: {editor}", editor=argv[0]), language=settings.language
        )
    command = [executable, *argv[1:], str(path)]
    if os.name != "nt":
        from .runtimes.platforms.preferences import editor_identity

        command = editor_identity(command)
    elif executable.lower().endswith((".cmd", ".bat", ".ps1")):
        quoted = " ".join("'" + token.replace("'", "''") + "'" for token in command)
        command = [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"& {quoted}; exit $LASTEXITCODE",
        ]
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise SettingsError(
            message("editor exited with code {code}", code=result.returncode),
            language=settings.language,
        )
    updated = load_settings(path)
    with language_scope(updated.language):
        print(t("配置已校验：{path}", path=path))
    return 0


def config_command(args) -> int:
    settings = getattr(args, "settings", None) or load_settings(
        getattr(args, "config", None), tolerate_invalid=True
    )
    if args.config_action == "edit":
        return edit_settings(settings)
    payload = settings.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            t(
                "配置文件：{path}（{state}）",
                path=settings.path,
                state=t(
                    {"loaded": "已加载", "missing": "未配置", "invalid": "无效"}[settings.state]
                ),
            )
        )
        for error in settings.errors:
            print(render(error))
        if args.config_action == "check":
            if not settings.errors:
                print(
                    t("配置有效。")
                    if settings.state == "loaded"
                    else t("配置文件不存在，将使用默认值。")
                )
        else:
            print(render_settings(payload["settings"]), end="")
            print(t("配置来源："))
            for field, source in payload["origins"].items():
                print(f"  {field}: {t('默认值') if source == 'default' else source}")
    return 1 if settings.errors else 0
