"""File discovery and claim identifiers for documentation indexing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCS_ROOT = "docs"
NON_CANONICAL_DOC_DIRS = ("docs/tmp", "docs/assets", "docs/ReleaseNotes")


CANONICAL_CLAIM_PREFIXES = (
    "DOC-REQ",
    "CONTRACT",
    "INV",
    "NON-GOAL",
    "QUALITY",
    "TEST",
)


CANONICAL_CLAIM_PREFIX_RE = "|".join(
    re.escape(prefix) for prefix in CANONICAL_CLAIM_PREFIXES
)


CANONICAL_CLAIM_ID_RE = re.compile(
    rf"^(?:{CANONICAL_CLAIM_PREFIX_RE})-\d{{3}}$"
)


@dataclass(frozen=True)
class Finding:
    """A structured indexing diagnostic."""

    rule: str
    severity: str
    path: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class DocFile:
    """A markdown doc under ``docs/`` paired with its current text."""

    path: str  # repo-relative POSIX path
    text: str


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def is_canonical_doc(path: str) -> bool:
    """True for canonical declarative docs (``docs/**.md`` minus excluded trees)."""

    if not path.endswith(".md"):
        return False
    if path.endswith(".template.md"):
        return False
    if not _is_under(path, CANONICAL_DOCS_ROOT):
        return False
    return not any(_is_under(path, excluded) for excluded in NON_CANONICAL_DOC_DIRS)


def _load_doc(root: Path, rel_path: str) -> DocFile | None:
    abs_path = root / rel_path
    try:
        text = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return DocFile(path=rel_path, text=text)


def load_docs(paths: Iterable[str], *, root: Path = REPO_ROOT) -> list[DocFile]:
    docs: list[DocFile] = []
    for rel_path in sorted(set(paths)):
        if not rel_path.endswith(".md"):
            continue
        doc = _load_doc(root, rel_path)
        if doc is not None:
            docs.append(doc)
    return docs


def all_doc_paths(*, root: Path = REPO_ROOT) -> list[str]:
    docs_dir = root / CANONICAL_DOCS_ROOT
    if not docs_dir.is_dir():
        return []
    return [
        child.relative_to(root).as_posix()
        for child in sorted(docs_dir.rglob("*.md"))
        if child.is_file()
    ]
