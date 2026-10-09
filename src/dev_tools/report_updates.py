"""Parse native package-manager queries without inferring unavailable versions."""

from __future__ import annotations

import json
import re


def mise_updates(output: str) -> list[dict]:
    value = json.loads(output)
    if not isinstance(value, dict):
        raise TypeError("invalid mise outdated response")
    updates = []
    for name, item in value.items():
        if not isinstance(item, dict) or not isinstance(item.get("latest"), str):
            raise TypeError("invalid mise outdated entry")
        current = item.get("current")
        if current is not None and not isinstance(current, str):
            raise ValueError("invalid installed version")
        updates.append(
            {
                "name": name,
                "installed": current,
                "available": item["latest"],
                "requested": item.get("requested"),
            }
        )
    return updates


def store_packages(output: str) -> list[dict]:
    """Store CLI shows installed versions, including in its updates table."""
    packages = []
    columns = None
    for line in output.splitlines():
        if "│" not in line:
            continue
        cells = [cell.strip() for cell in line.split("│")[1:-1]]
        lowered = [cell.lower() for cell in cells]
        if "name" in lowered and "version" in lowered:
            columns = {name: lowered.index(name) for name in ("name", "version", "publisher")}
            continue
        if columns is None or len(cells) <= max(columns.values()):
            continue
        name, version, publisher = (cells[columns[key]] for key in ("name", "version", "publisher"))
        if version:
            packages.append({"name": name, "installed": version, "publisher": publisher})
        elif name and packages:
            # Spectre.Console wraps names and publishers onto continuation rows.
            packages[-1]["name"] += " " + name
            if publisher:
                packages[-1]["publisher"] += " " + publisher
    if not packages and not re.search(
        r"no (?:updates?|apps?|applications?)|all .*up.to.date|everything .*up.to.date",
        output,
        re.IGNORECASE,
    ):
        raise ValueError("unrecognized Store response")
    return packages


def snap_packages(output: str) -> list[dict]:
    packages = []
    in_table = False
    for line in output.splitlines():
        cells = line.split()
        if cells[:3] == ["Name", "Version", "Rev"]:
            in_table = True
        elif in_table and len(cells) >= 3 and cells[2].isdigit():
            packages.append({"name": cells[0], "version": cells[1], "revision": cells[2]})
    if not in_table and not re.search(
        r"all snaps up to date|no snaps (?:are )?installed", output, re.IGNORECASE
    ):
        raise ValueError("unrecognized Snap response")
    return packages


def rustup_updates(output: str) -> list[dict]:
    updates = []
    recognized = False
    for line in output.splitlines():
        match = re.match(r"^(\S+)\s+-\s+update available\s*:\s*(\S+).*?\s+->\s+(\S+)", line)
        if match:
            recognized = True
            updates.append({"name": match[1], "installed": match[2], "available": match[3]})
        elif re.match(r"^\S+\s+-\s+up to date\s*:\s*\S+", line):
            recognized = True
    if not recognized:
        raise ValueError("unrecognized rustup response")
    return updates


def query_updates(result: dict, parser, *, success_codes: tuple[int, ...] = (0,)) -> None:
    if result["status"] in {"missing", "timeout", "skipped"}:
        return
    try:
        updates = parser(result.get("output", ""))
    except ValueError, TypeError, KeyError:
        if result["status"] == "ok":
            result["status"] = "parse-error"
        return
    result["updates"] = updates
    if result.get("exit_code", 0) in success_codes:
        result["status"] = "updates" if updates else "ok"
    elif updates:
        result["status"] = "partial"
