"""Static framework conventions; filesystem and scan policy belong to Inspection."""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass


@dataclass(frozen=True)
class RelatedScope:
    directory: str
    metadata: tuple[str, ...]
    reason: str


def related_scopes(metadata: Container[str]) -> tuple[RelatedScope, ...]:
    """Suggest related scopes from metadata names without reading or executing files."""
    if "package.json" in metadata:
        return (RelatedScope("src-tauri", ("Cargo.toml",), "tauri"),)
    return ()
