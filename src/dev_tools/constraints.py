"""Conservative containment checks; unknown syntax never establishes compatibility."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .versions import normalize_version


@dataclass(frozen=True)
class Interval:
    lower: tuple[int, int, int] = (0, 0, 0)
    upper: tuple[int, int, int] | None = None
    upper_inclusive: bool = False
    lower_inclusive: bool = True

    def contains(self, other: Interval) -> bool:
        if other.lower < self.lower or (
            other.lower == self.lower and other.lower_inclusive and not self.lower_inclusive
        ):
            return False
        return self.upper is None or (
            other.upper is not None
            and (
                other.upper < self.upper
                or (
                    other.upper == self.upper
                    and (self.upper_inclusive or not other.upper_inclusive)
                )
            )
        )

    def disjoint(self, other: Interval) -> bool:
        return any(
            left.upper is not None
            and (
                left.upper < right.lower
                or (
                    left.upper == right.lower
                    and not (left.upper_inclusive and right.lower_inclusive)
                )
            )
            for left, right in ((self, other), (other, self))
        )


def _number(raw: str) -> tuple[int, int, int]:
    parts = tuple(map(int, raw.split(".")))
    return parts + (0,) * (3 - len(parts))


def _prefix(raw: str) -> Interval:
    parts = raw.split(".")
    lower = _number(raw)
    if len(parts) == 3:
        return Interval(lower, lower, True)
    upper = list(lower)
    upper[len(parts) - 1] += 1
    return Interval(lower, tuple(upper))


def _parse(raw: str, tool: str) -> Interval | None:
    value = raw.strip().strip("\"'").removeprefix("v")
    if value in {"*", "x", "X"}:
        return Interval()
    value = re.sub(r"(?<=\d)\+[A-Za-z0-9.-]+$", "", value)
    if re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        return _prefix(value)
    wildcard = re.fullmatch(r"(?:==)?(\d+(?:\.\d+)?)\.[xX*]", value)
    if wildcard:
        return _prefix(wildcard.group(1))
    compatible = re.fullmatch(r"(\^|~=|~)(\d+(?:\.\d+){0,2})", value)
    if compatible:
        parts = compatible.group(2).split(".")
        lower = _number(compatible.group(2))
        if compatible.group(1) == "^":
            index = next(
                (i for i, number in enumerate(lower[: len(parts)]) if number), len(parts) - 1
            )
        else:
            index = max(0, len(parts) - (2 if compatible.group(1) == "~=" else 1))
            if compatible.group(1) == "~":
                index = min(index, 1)
        upper = tuple(
            number if i < index else number + 1 if i == index else 0
            for i, number in enumerate(lower)
        )
        return Interval(lower, upper)
    tokens = re.findall(r"(>=|<=|==|>|<|=)\s*(\d+(?:\.\d+){0,2})", value)
    if not tokens or re.sub(r"(>=|<=|==|>|<|=)\s*\d+(?:\.\d+){0,2}", "", value).strip(" ,"):
        return None
    lower, upper, lower_inclusive, upper_inclusive = (0, 0, 0), None, True, False
    for operator, number in tokens:
        # Partial <=, > and equality differ across SemVer and PEP 440.
        if operator in {"<=", ">", "=", "=="} and len(number.split(".")) != 3:
            return None
        parsed = _number(number)
        if operator in {">=", ">", "=", "=="}:
            inclusive = operator != ">"
            if parsed > lower or (parsed == lower and not inclusive):
                lower, lower_inclusive = parsed, inclusive
        if operator in {"<=", "<", "=", "=="}:
            inclusive = operator != "<"
            if upper is None or parsed < upper or (parsed == upper and not inclusive):
                upper, upper_inclusive = parsed, inclusive
    if upper is not None and (
        lower > upper or (lower == upper and not (lower_inclusive and upper_inclusive))
    ):
        return None
    return Interval(lower, upper, upper_inclusive, lower_inclusive)


def compatible(tool: str, selected: str, requirement: str, *, minimum: bool = False) -> bool | None:
    """True proves containment, False proves disjointness, None needs confirmation."""
    selected, requirement = selected.strip().strip("\"'"), requirement.strip().strip("\"'")
    if selected == requirement and not minimum:
        return True
    if tool == "java":
        vendor = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*)-(?=\d)", requirement)
        selected_vendor = re.match(
            r"^([a-zA-Z][a-zA-Z0-9_-]*)-(?=\d)", normalize_version(tool, selected) or selected
        )
        if (
            not minimum
            and vendor
            and selected_vendor
            and vendor.group(1) != selected_vendor.group(1)
        ):
            return False
        selected = normalize_version(tool, selected) or selected
        requirement = normalize_version(tool, requirement) or requirement
        selected = re.sub(r"^[a-zA-Z][a-zA-Z0-9_-]*-(?=\d)", "", selected)
        requirement = re.sub(r"^[a-zA-Z][a-zA-Z0-9_-]*-(?=\d)", "", requirement)
    if minimum and re.fullmatch(r"\d+(?:\.\d+){0,2}", requirement):
        requirement = ">=" + requirement
    candidate = _parse(selected, tool)
    ranges = [_parse(branch.strip(), tool) for branch in requirement.split("||")]
    if candidate is None or any(item is None for item in ranges):
        return None
    if any(item.contains(candidate) for item in ranges):
        return True
    return False if all(item.disjoint(candidate) for item in ranges) else None
