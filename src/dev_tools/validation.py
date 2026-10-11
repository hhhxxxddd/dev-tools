"""Validate release metadata, local Markdown links and TOML examples without executing them."""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit


def prose(content: str) -> str:
    return re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", content)


def anchors(content: str) -> set[str]:
    result, counts = set(), {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", prose(content)):
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading).lower()
        slug = "".join(
            char for char in heading if char in "-_ " or unicodedata.category(char)[0] in "LN"
        ).replace(" ", "-")
        count = counts.get(slug, 0)
        result.add(f"{slug}-{count}" if count else slug)
        counts[slug] = count + 1
    return result


def validate(root: Path, package: str, tag: str | None = None) -> dict[str, int | str]:
    root = root.resolve()
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tree = ast.parse((root / "src" / package / "__init__.py").read_text(encoding="utf-8"))
    versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
    ]
    version = metadata["project"]["version"]
    if versions != [version] or (tag is not None and tag != "v" + version):
        raise ValueError("Tag, pyproject and controller versions must agree")
    documents = sorted(
        {path for path in root.glob("*.md")}
        | {path for folder in ("docs", "examples") for path in (root / folder).rglob("*.md")}
    )
    links = examples = pairs = 0
    for path in documents:
        content = path.read_text(encoding="utf-8")
        blocks = re.findall(r"(?ms)^```toml\s*\n(.*?)^```\s*$", content)
        for block in blocks:
            tomllib.loads(block)
            examples += 1
        for destination in re.findall(r"\]\(([^\s)]+)\)", prose(content)):
            reference = urlsplit(destination.strip("<>"))
            if reference.scheme or reference.netloc:
                continue
            target = (path.parent / unquote(reference.path)).resolve() if reference.path else path
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError(f"{path.relative_to(root)}: missing local link {destination}")
            if (
                reference.fragment
                and target.suffix == ".md"
                and unquote(reference.fragment) not in anchors(target.read_text(encoding="utf-8"))
            ):
                raise ValueError(f"{path.relative_to(root)}: missing anchor {destination}")
            links += 1
        if path.name.endswith(".en.md"):
            partner = path.with_name(path.name.removesuffix(".en.md") + ".md")
            if not partner.is_file():
                raise ValueError(f"Missing Chinese document for {path.relative_to(root)}")
            chinese = partner.read_text(encoding="utf-8")
            levels = lambda text: re.findall(r"(?m)^(#{1,6})\s", prose(text))
            if levels(content) != levels(chinese):
                raise ValueError(f"Bilingual section structure differs: {path.relative_to(root)}")
            other_blocks = re.findall(r"(?ms)^```toml\s*\n(.*?)^```\s*$", chinese)
            if [tomllib.loads(block) for block in blocks] != [
                tomllib.loads(block) for block in other_blocks
            ]:
                raise ValueError(f"Bilingual TOML examples differ: {path.relative_to(root)}")
            pairs += 1
    for folder in ("config", "examples"):
        for path in (root / folder).rglob("*.toml"):
            tomllib.loads(path.read_text(encoding="utf-8"))
            examples += 1
    return {
        "version": version,
        "documents": len(documents),
        "links": links,
        "toml": examples,
        "pairs": pairs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    print(validate(root, __package__, args.tag))


if __name__ == "__main__":
    main()
