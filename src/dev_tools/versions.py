from __future__ import annotations

import re

SDKMAN_JAVA_VENDORS = {
    "amzn": "corretto",
    "librca": "liberica",
    "ms": "microsoft",
    "oracle": "oracle",
    "sem": "semeru",
    "tem": "temurin",
    "zulu": "zulu",
}


def _normalize_java(raw: str) -> str | None:
    value = raw.strip().strip("\"'")
    if not value or value.startswith("${"):
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9_-]*-\d", value):
        return value
    sdkman = re.fullmatch(r"(\d+(?:\.\d+){0,2})-([A-Za-z0-9]+)", value)
    if sdkman:
        vendor = SDKMAN_JAVA_VENDORS.get(sdkman.group(2).lower())
        return f"{vendor}-{sdkman.group(1)}" if vendor else None
    numeric = _normalize_numeric("java", value)
    if numeric is None:
        return value if value in {"latest", "lts"} else None
    if numeric.startswith("1."):
        numeric = numeric.split(".", 1)[1]
    return f"temurin-{numeric}"


def _normalize_numeric(tool: str, value: str) -> str | None:
    """Choose only selectors wholly inside a supported version constraint."""
    value = value.removeprefix("v")
    if not value:
        return None
    if value in {"latest", "lts", "system"} or value.startswith(("lts/", "ref:", "path:")):
        return value
    exact = re.fullmatch(r"(\d+(?:\.\d+){0,2})(?:\+.*)?", value)
    if exact:
        return exact.group(1)
    wildcard = re.fullmatch(r"(?:==)?(\d+(?:\.\d+)*)\.[xX*]", value)
    if wildcard:
        return wildcard.group(1)
    compatible = re.fullmatch(r"([~^])(\d+(?:\.\d+){0,2})", value)
    if compatible:
        parts = compatible.group(2).split(".")
        if len(parts) == 3 and parts[-1] != "0":
            return compatible.group(2)
        width = 2 if tool in {"python", "uv"} or parts[0] == "0" else 1
        if compatible.group(1) == "~" and len(parts) >= 2:
            width = 2
        if compatible.group(1) == "^" and len(parts) == 3 and parts[:2] == ["0", "0"]:
            width = 3
        while any(int(part) for part in parts[width:]):
            width += 1
        return ".".join(parts[:width])
    equal = re.fullmatch(r"(?:==|=)(\d+(?:\.\d+){0,2})", value)
    if equal:
        # A partial equality has different semantics in SemVer and PEP 440.
        return equal.group(1) if len(equal.group(1).split(".")) == 3 else None
    bounds = re.findall(r"(>=|<=|<)\s*(\d+(?:\.\d+){0,2})", value)
    remainder = re.sub(r"(>=|<=|<)\s*\d+(?:\.\d+){0,2}", "", value)
    if not bounds or remainder.strip(" ,"):
        return None

    def version(raw: str) -> tuple[int, ...]:
        parts = tuple(int(part) for part in raw.split("."))
        return parts + (0,) * (3 - len(parts))

    lowers = [raw for op, raw in bounds if op == ">="]
    if not lowers:
        return None
    lower = max(lowers, key=version)
    minimum = version(lower)
    for op, raw in bounds:
        if (op == "<" and minimum >= version(raw)) or (op == "<=" and minimum > version(raw)):
            return None
    width = min(len(lower.split(".")), 2 if tool in {"python", "uv"} else 1)
    parts = minimum[:width]
    next_prefix = parts[:-1] + (parts[-1] + 1,) + (0,) * (3 - width)
    prefix_safe = all(part == 0 for part in minimum[width:]) and all(
        op == ">=" or next_prefix <= version(raw) for op, raw in bounds
    )
    return ".".join(str(part) for part in parts) if prefix_safe else ".".join(map(str, minimum))


def normalize_version(tool: str, raw: str) -> str | None:
    if tool == "java":
        return _normalize_java(raw)
    return _normalize_numeric(tool, raw.strip().strip("\"'"))
