"""Hosts text, managed profiles and native user backups; no system writes here."""

from __future__ import annotations

import codecs
import hashlib
import ipaddress
import json
import re
import stat
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .i18n import message

MAX_BYTES = 10 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
BEGIN = "# BEGIN dev-tools hosts: "
END = "# END dev-tools hosts"


def validate_profile(name: str) -> str:
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not NAME.fullmatch(name) or name.upper() in reserved:
        raise ValueError(
            message("Hosts profile names must contain 1-64 letters, digits, underscores or hyphens")
        )
    return name


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(message("Hosts data must be a regular file"))
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(message("Hosts file exceeds the 10 MiB limit"))
    return data


@dataclass
class Document:
    text: str
    encoding: str = "utf-8"
    bom: bytes = b""

    @classmethod
    def decode(cls, data: bytes) -> Document:
        for bom, encoding in (
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
        ):
            if data.startswith(bom):
                return cls(data[len(bom) :].decode(encoding), encoding, bom)
        try:
            return cls(data.decode("utf-8"))
        except UnicodeDecodeError:
            import os

            if os.name != "nt":
                raise ValueError(message("Hosts file encoding is unsupported")) from None
            return cls(data.decode("mbcs"), "mbcs")

    def encode(self, text: str) -> bytes:
        try:
            return self.bom + text.encode(self.encoding)
        except UnicodeError:
            raise ValueError(
                message("New hosts content cannot be represented in the original encoding")
            ) from None

    @property
    def newline(self) -> str:
        return "\r\n" if "\r\n" in self.text else "\n"


def managed_block(text: str) -> tuple[int, int, str] | None:
    start = None
    found = None
    offset = 0
    for line in text.splitlines(keepends=True):
        value = line.rstrip("\r\n")
        if value.startswith("# BEGIN dev-tools hosts"):
            if start is not None or found is not None or not value.startswith(BEGIN):
                raise ValueError(
                    message(
                        "Hosts managed markers are damaged or duplicated; repair them before switching"
                    )
                )
            name = validate_profile(value[len(BEGIN) :])
            start = (offset, name)
        elif value.startswith(END):
            if value != END or start is None or found is not None:
                raise ValueError(
                    message(
                        "Hosts managed markers are damaged or duplicated; repair them before switching"
                    )
                )
            found = (start[0], offset + len(line), start[1])
            start = None
        offset += len(line)
    if start is not None:
        raise ValueError(
            message("Hosts managed markers are damaged or duplicated; repair them before switching")
        )
    return found


def _domain(value: str) -> str:
    try:
        normalized = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError from None
    if len(normalized) > 253 or not all(
        re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
        for label in normalized.split(".")
    ):
        raise ValueError
    return normalized


def parse_entries(text: str, *, strict: bool = False) -> tuple[list[dict], list[int]]:
    entries, invalid = [], []
    for number, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        # Some edited Windows files have a BOM before an otherwise ordinary comment.
        if raw.lstrip("\ufeff").startswith("#"):
            raw = raw.lstrip("\ufeff")
        if not raw:
            continue
        if any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in line):
            invalid.append(number)
            continue
        enabled = not raw.startswith("#")
        content = raw if enabled else raw[1:].lstrip()
        body, _, comment = content.partition("#")
        tokens = body.split()
        try:
            if len(tokens) < 2:
                raise ValueError
            address = ipaddress.ip_address(tokens[0])
            for domain in tokens[1:]:
                _domain(domain)
        except ValueError:
            if enabled:
                invalid.append(number)
            continue
        entries.append(
            {
                "address": str(address),
                "names": tokens[1:],
                "enabled": enabled,
                "comment": comment.strip(),
                "line": number,
            }
        )
    if strict and invalid:
        raise ValueError(
            message("Invalid hosts entries on lines: {lines}", lines=", ".join(map(str, invalid)))
        )
    return entries, invalid


def entry_conflicts(entries: list[dict]) -> list[dict]:
    addresses = {}
    for entry in entries:
        if not entry["enabled"]:
            continue
        version = ipaddress.ip_address(entry["address"]).version
        for domain in entry["names"]:
            key = (_domain(domain), version)
            group = addresses.setdefault(key, {"addresses": set(), "lines": []})
            group["addresses"].add(entry["address"])
            group["lines"].append(entry["line"])
    return [
        {
            "domain": domain,
            "version": version,
            "addresses": sorted(value["addresses"]),
            "lines": value["lines"],
        }
        for (domain, version), value in addresses.items()
        if len(value["addresses"]) > 1
    ]


def _conflict(item: dict):
    raise ValueError(
        message(
            "Hosts domain {domain} maps to conflicting IPv{version} addresses",
            domain=item["domain"],
            version=item["version"],
        )
    )


def validate_content(text: str) -> list[dict]:
    managed_block(text)
    entries, _ = parse_entries(text, strict=True)
    conflicts = entry_conflicts(entries)
    if conflicts:
        _conflict(conflicts[0])
    return entries


def validate_edit(before: str, after: str) -> None:
    managed_block(after)
    before_entries, before_invalid = parse_entries(before)
    after_entries, after_invalid = parse_entries(after)
    lines = before.splitlines()
    preserved = Counter(lines[number - 1] for number in before_invalid)
    lines = after.splitlines()
    for number in after_invalid:
        if preserved[lines[number - 1]] <= 0:
            raise ValueError(message("Invalid hosts entries on lines: {lines}", lines=str(number)))
        preserved[lines[number - 1]] -= 1
    previous = {
        (item["domain"], item["version"]): set(item["addresses"])
        for item in entry_conflicts(before_entries)
    }
    for item in entry_conflicts(after_entries):
        if not set(item["addresses"]).issubset(
            previous.get((item["domain"], item["version"]), set())
        ):
            _conflict(item)


def switch_profile(document: Document, name: str | None, profile: str = "") -> str:
    block = managed_block(document.text)
    start, end = (block[0], block[1]) if block else (len(document.text), len(document.text))
    if name is None:
        return document.text[:start] + document.text[end:]
    validate_profile(name)
    if any(line.startswith(("# BEGIN dev-tools hosts", END)) for line in profile.splitlines()):
        raise ValueError(message("Hosts profiles must not contain managed markers"))
    profile_entries = validate_content(profile)
    base_entries = parse_entries(document.text[:start] + document.text[end:])[0]
    profile_domains = {
        (_domain(domain), ipaddress.ip_address(item["address"]).version)
        for item in profile_entries
        if item["enabled"]
        for domain in item["names"]
    }
    for item in entry_conflicts(base_entries + profile_entries):
        if (item["domain"], item["version"]) in profile_domains:
            _conflict(item)
    newline = document.newline
    body = newline.join(profile.splitlines())
    replacement = BEGIN + name + newline + body.rstrip("\r\n") + newline + END + newline
    prefix = document.text[:start]
    if prefix and not prefix.endswith(("\r", "\n")):
        prefix += newline
    result = prefix + replacement + document.text[end:]
    return result


class HostsStore:
    def __init__(self, root: Path):
        self.root = root

    def profile(self, name: str) -> Path:
        return self.root / "profiles" / (validate_profile(name) + ".hosts")

    def load_profile(self, name: str) -> str:
        path = self.profile(name)
        if not path.is_file():
            raise ValueError(
                message(
                    "Hosts profile does not exist: {name}; create it with dev-tools hosts edit {name}",
                    name=name,
                )
            )
        content = Document.decode(read_bytes(path)).text
        if managed_block(content):
            raise ValueError(message("Hosts profiles must not contain managed markers"))
        validate_content(content)
        return content

    def backup(self, before: bytes, after: bytes, operation: str) -> str:
        identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        folder = self.root / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / (identifier + ".hosts")).open("xb") as stream:
            stream.write(before)
        # Metadata describes a transaction, never user preferences or a custom target.
        content = {"before": digest(before), "after": digest(after), "operation": operation}
        with (folder / (identifier + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(content, stream)
        return identifier

    def backups(self) -> list[dict]:
        result = []
        for path in sorted((self.root / "backups").glob("*.json"), reverse=True):
            if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}", path.stem):
                continue
            try:
                value = json.loads(read_bytes(path))
                if not isinstance(value, dict) or not all(
                    isinstance(value.get(key), str) for key in ("before", "after", "operation")
                ):
                    raise ValueError
                if not all(
                    re.fullmatch(r"[0-9a-f]{64}", value[key]) for key in ("before", "after")
                ):
                    raise ValueError
            except OSError, ValueError:
                raise ValueError(
                    message("Hosts backup metadata is damaged: {name}", name=path.stem)
                ) from None
            result.append(
                {
                    "id": path.stem,
                    **{key: value[key] for key in ("before", "after", "operation")},
                }
            )
        return result

    def restore(self, current: bytes, identifier: str | None = None) -> bytes:
        records = self.backups()
        record = (
            next((item for item in records if item["id"] == identifier), None)
            if identifier
            else next((item for item in records if item["after"] == digest(current)), None)
        )
        if record is None:
            raise ValueError(
                message(
                    "No matching hosts backup; inspect dev-tools hosts backups and merge manually if the file changed"
                )
            )
        if record["after"] != digest(current):
            raise ValueError(
                message("Hosts changed after this backup; refusing to overwrite other edits")
            )
        content = read_bytes(self.root / "backups" / (record["id"] + ".hosts"))
        if digest(content) != record["before"]:
            raise ValueError(message("Hosts backup checksum failed"))
        return content
