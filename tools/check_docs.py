#!/usr/bin/env python3
"""Verify that repository-relative Markdown links point to existing files."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
EXTERNAL_PREFIXES = ("#", "http:", "https:", "mailto:", "tel:", "data:")


def markdown_files(root: Path = ROOT) -> list[Path]:
    paths = [root / "README.md"]
    docs = root / "docs"
    if docs.is_dir():
        paths.extend(docs.rglob("*.md"))
    return sorted(path for path in paths if path.is_file())


def broken_links(paths: Iterable[Path], *, root: Path = ROOT) -> list[tuple[Path, str]]:
    """Return local Markdown links whose file target does not exist."""
    broken: list[tuple[Path, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            target = match.group(1).strip("<>")
            target_path = target.split("#", 1)[0].split("?", 1)[0]
            if not target_path or target_path.casefold().startswith(EXTERNAL_PREFIXES):
                continue
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                broken.append((path, target))
    return broken


def main() -> int:
    failures = broken_links(markdown_files())
    if not failures:
        print("Markdown links: OK")
        return 0
    for path, target in failures:
        print(f"Broken Markdown link: {path.relative_to(ROOT)} -> {target}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
