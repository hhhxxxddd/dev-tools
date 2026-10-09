from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Evidence:
    tool: str
    version: str | None
    source: str
    priority: int
    confidence: str
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Conflict:
    tool: str
    versions: dict[str, list[str]]


@dataclass(frozen=True)
class Wrapper:
    version: str
    source: str


@dataclass(frozen=True)
class UnresolvedRequirement:
    tool: str
    source: str
    raw: str
    reason: str


@dataclass(frozen=True)
class Diagnostic:
    source: str
    code: str
    message: str


@dataclass
class ScanResult:
    root: Path
    existing_config: Path | None
    tools: dict[str, Evidence]
    evidence: list[Evidence]
    conflicts: list[Conflict]
    warnings: list[str]
    wrappers: dict[str, Wrapper]
    requirements: dict[str, list[str]]
    unresolved: list[UnresolvedRequirement]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    root_tools: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts or self.unresolved or self.diagnostics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "root": str(self.root),
            "existing_config": str(self.existing_config) if self.existing_config else None,
            "tools": {
                name: {
                    "version": item.version,
                    "source": item.source,
                    "confidence": item.confidence,
                }
                for name, item in self.tools.items()
            },
            "evidence": [item.as_dict() for item in self.evidence],
            "conflicts": [asdict(item) for item in self.conflicts],
            "warnings": self.warnings,
            "wrappers": {name: asdict(item) for name, item in self.wrappers.items()},
            "requirements": self.requirements,
            "unresolved": [asdict(item) for item in self.unresolved],
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }
