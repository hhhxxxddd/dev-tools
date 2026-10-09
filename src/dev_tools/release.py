"""Build reproducible release assets and the Scoop manifest from one version."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

from . import __version__
from .controller import REPOSITORY, UPSTREAM


def payload_files(root: Path) -> dict[str, bytes]:
    paths = [
        root / name
        for name in (
            "README.md",
            "README.en.md",
            "LICENSE",
            "AGENTS.md",
            "CHANGELOG.md",
            "pyproject.toml",
        )
    ]
    for directory in ("src/dev_tools", "scripts", "config", "docs", "examples", "systemd"):
        paths.extend((root / directory).rglob("*"))
    files = {}
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"Release payload must not contain symlinks: {path}")
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or ".local." in path.name
        ):
            continue
        name = path.relative_to(root).as_posix()
        content = path.read_bytes()
        if path.suffix in {".py", ".ps1", ".sh", ".toml", ".md", ".json", ".service"} or name in {
            "scripts/dev-tools",
            "scripts/dev-info",
            "LICENSE",
        }:
            content = content.replace(b"\r\n", b"\n")
        files[name] = content
    return files


def scoop_manifest(version: str, checksum: str) -> dict:
    base = f"https://github.com/{UPSTREAM}/releases/download/v{version}"

    def hook(action: str) -> str:
        return f'& pwsh -NoProfile -File "$dir\\scripts\\scoop.ps1" -Action {action}; if ($LASTEXITCODE -ne 0) {{ throw "dev-tools {action} failed" }}'

    return {
        "version": version,
        "description": "Project discovery, preparation and native Windows/WSL development control",
        "homepage": f"https://github.com/{UPSTREAM}",
        "license": "MIT",
        "depends": ["main/mise", "main/pwsh"],
        "url": f"{base}/dev-tools-{version}.zip",
        "hash": checksum,
        "bin": [["scripts/scoop-entry.ps1", "dev-tools"]],
        "pre_install": hook("check"),
        "installer": {"script": hook("install")},
        "pre_uninstall": hook("check"),
        "uninstaller": {"script": hook("uninstall")},
        "checkver": "github",
        "autoupdate": {
            "url": f"https://github.com/{UPSTREAM}/releases/download/v$version/dev-tools-$version.zip",
            "hash": {
                "url": f"https://github.com/{UPSTREAM}/releases/download/v$version/SHA256SUMS"
            },
        },
    }


def build(root: Path, output: Path, tag: str | None = None) -> dict:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if metadata["project"]["version"] != __version__ or (tag and tag != "v" + __version__):
        raise ValueError("Release tag, pyproject version and controller version must match")
    files = payload_files(root)
    receipt = {
        "repository": UPSTREAM,
        "version": __version__,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files[".release.json"] = (json.dumps(receipt, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"dev-tools-{__version__}.zip"
    # Python distributions can use different deflate implementations. This small
    # source bundle uses stored entries so Windows and Linux produce identical bytes.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as package:
        for name, content in sorted(files.items()):
            item = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_STORED
            item.create_system = 3
            mode = (
                0o755
                if name in {"scripts/dev-tools", "scripts/dev-info"} or name.endswith(".sh")
                else 0o644
            )
            item.external_attr = (0o100000 | mode) << 16
            package.writestr(item, content)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    (output / "dev-tools.json").write_text(
        json.dumps(scoop_manifest(__version__, checksum), indent=2) + "\n", encoding="utf-8"
    )
    return {"version": __version__, "archive": str(archive), "sha256": checksum}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "dist")
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(json.dumps(build(REPOSITORY, args.output, args.tag), indent=2))


if __name__ == "__main__":
    main()
