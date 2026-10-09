"""Presentation language and messages; persisted identifiers and tool output stay unchanged."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_language = ContextVar("dev_tools_language", default="zh")
_catalogs: dict[str, dict[str, str]] = {}


def language() -> str:
    return _language.get()


@contextmanager
def language_scope(value: str):
    token = _language.set(value)
    try:
        yield
    finally:
        _language.reset(token)


def t(source: str, **parameters) -> str:
    selected = language()
    if selected not in _catalogs:
        path = Path(__file__).with_name("locales") / (selected + ".json")
        _catalogs[selected] = json.loads(path.read_text(encoding="utf-8"))
    template = _catalogs[selected].get(source, source)
    return template.format(**parameters) if parameters else template


class Message(str):
    """Canonical text plus the template and parameters needed for later presentation."""

    def __new__(cls, source: str, **parameters):
        canonical = {key: _canonical(value) for key, value in parameters.items()}
        instance = super().__new__(cls, source.format(**canonical) if parameters else source)
        instance.source = source
        instance.parameters = {
            key: value.as_dict()
            if isinstance(value, (Message, JoinedMessages))
            else value
            if isinstance(value, dict)
            else str(value)
            for key, value in parameters.items()
        }
        return instance

    def as_dict(self) -> dict:
        return {"source": self.source, "parameters": self.parameters}


def message(source: str, **parameters) -> Message:
    return Message(source, **parameters)


class JoinedMessages(str):
    def __new__(cls, separator: str, values):
        values = tuple(values)
        instance = super().__new__(cls, separator.join(map(str, values)))
        instance.separator, instance.values = separator, values
        return instance

    def as_dict(self) -> dict:
        return {
            "separator": self.separator,
            "messages": [
                value.as_dict() if isinstance(value, Message) else str(value)
                for value in self.values
            ],
        }


def join_messages(separator: str, values) -> JoinedMessages:
    return JoinedMessages(separator, values)


def _canonical(value: object) -> str:
    if isinstance(value, dict) and "source" in value and "parameters" in value:
        return value["source"].format(
            **{key: _canonical(item) for key, item in value["parameters"].items()}
        )
    if isinstance(value, dict) and "separator" in value and "messages" in value:
        return value["separator"].join(_canonical(item) for item in value["messages"])
    return str(value)


def render(value: object) -> str:
    if isinstance(value, BaseException):
        return render(value.args[0]) if value.args else str(value)
    if isinstance(value, Message):
        return t(
            value.source,
            **{
                key: render(item) if isinstance(item, dict) else item
                for key, item in value.parameters.items()
            },
        )
    if isinstance(value, dict) and "source" in value and "parameters" in value:
        return t(
            value["source"],
            **{
                key: render(item) if isinstance(item, dict) else item
                for key, item in value["parameters"].items()
            },
        )
    if isinstance(value, dict) and "separator" in value and "messages" in value:
        return value["separator"].join(render(item) for item in value["messages"])
    return str(value)


def error_detail(error: BaseException) -> dict | None:
    value = error.args[0] if error.args else None
    return value.as_dict() if isinstance(value, Message) else None
