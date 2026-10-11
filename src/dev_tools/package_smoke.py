"""Verify an extracted candidate through its native entrypoint, without project mutations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .controller import verify_source
from .validation import validate


def smoke(archive: Path) -> dict:
    archive = archive.resolve()
    rows = [row.split() for row in archive.with_name("SHA256SUMS").read_text().splitlines()]
    hashes = [row[0] for row in rows if len(row) == 2 and row[1] == archive.name]
    if hashes != [hashlib.sha256(archive.read_bytes()).hexdigest()]:
        raise ValueError("Archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="dev-tools-package-") as temporary:
        root = Path(temporary)
        package = root / "package with spaces"
        with zipfile.ZipFile(archive) as payload:
            seen = set()
            for member in payload.infolist():
                target = package / member.filename
                if (
                    not target.resolve().is_relative_to(package.resolve())
                    or member.filename.lower() in seen
                    or "\\" in member.filename
                    or (member.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe archive member")
                seen.add(member.filename.lower())
            payload.extractall(package)
        verify_source(package)
        validate(package, "dev_tools")
        receipt = json.loads((package / ".release.json").read_text(encoding="utf-8"))
        interpreter = Path(sys.executable).resolve()
        binding = ".host-python.win" if os.name == "nt" else ".host-python"
        (package / binding).write_text(str(interpreter) + "\n", encoding="utf-8")
        entry = (
            ["pwsh", "-NoProfile", "-File", str(package / "scripts/dev-tools.ps1")]
            if os.name == "nt"
            else ["bash", str(package / "scripts/dev-tools")]
        )
        environment = {**os.environ, "PYTHONUTF8": "1"}
        # Exercise the payload alone, not modules from the original checkout.
        environment.pop("PYTHONPATH", None)
        environment.pop("DEV_TOOLS_CONFIG", None)
        project = root / "empty project"
        project.mkdir()
        (project / "mise.toml").write_text('[tools]\npython = "3.14.8"\n', encoding="utf-8")

        def invoke(*arguments: str) -> str:
            result = subprocess.run(
                [*entry, *arguments],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            if result.returncode:
                raise RuntimeError(f"Package entrypoint failed: {result.stderr}")
            return result.stdout.strip()

        if invoke("-v") != "dev-tools " + receipt["version"]:
            raise ValueError("Package version mismatch")
        for language in ("zh", "en"):
            config = root / f"{language}.toml"
            config.write_text(f'language = "{language}"\n', encoding="utf-8")
            options = ("--config", str(config))
            if invoke(*options) != invoke(*options, "help") or invoke(*options, "help") != invoke(
                *options, "--help"
            ):
                raise ValueError("Help entrypoints disagree")
            if "upgrade" not in invoke(*options, "help", "upgrade"):
                raise ValueError("Upgrade command missing")
            scanned = json.loads(invoke(*options, "scan", str(project), "--json"))
            if not isinstance(scanned, dict) or "schema_version" not in scanned:
                raise ValueError("Package scan JSON is invalid")
        if (package / binding).read_text(encoding="utf-8").strip() != str(interpreter):
            raise ValueError("Controller binding changed")
        return {"version": receipt["version"], "entrypoint": "verified", "languages": ["zh", "en"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    print(json.dumps(smoke(parser.parse_args().archive)))


if __name__ == "__main__":
    main()
