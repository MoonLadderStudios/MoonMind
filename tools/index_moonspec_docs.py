#!/usr/bin/env python3
"""MM-930 advisory MoonSpec documentation indexer.

Builds a machine-readable JSON index of canonical MoonSpec documents and stable
heading claims without mutating checked-in documentation.

Traceability: MM-930 (source issue MM-927, "Moon Spec Doc Architecture
Alignment"); covers DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-010, and
DESIGN-REQ-011.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from check_documentation_architecture import (
    NON_CANONICAL_DOC_DIRS,
    REPO_ROOT,
    Finding,
    all_doc_paths,
    is_canonical_doc,
    load_docs,
    run_checks,
)


ISSUE = "MM-930"
DEFAULT_OUTPUT = Path("artifacts/moonspec-doc-index/index.json")
CONSTITUTION_PATH = ".specify/memory/constitution.md"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
METADATA_RE = re.compile(r"^\s*\*\*([^*:\n]+):\*\*\s*(.*?)\s*$")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMPLEMENTATION_TOKEN_RE = re.compile(
    r"(?:^|[\s,(])((?:api_service|dashboard|moonmind|scripts|tests|tools|worker|"
    r"workers|docs|\.agents|\.specify)/[^\s,)`]+|[A-Za-z_][A-Za-z0-9_]*\([^)]*\)|"
    r"[A-Za-z_][A-Za-z0-9_.]*[A-Za-z0-9_])"
)

VIEWPOINTS = {
    "system architecture view",
    "module architecture view",
    "system / feature design view",
    "feature design view",
    "module contract specification",
    "cross-cutting concept view",
}


@dataclass(frozen=True)
class DocumentEntry:
    path: str
    documentClass: str
    viewpoint: str
    authority: str
    owningSurface: str
    relatedDocs: list[str]
    relatedImplementation: list[str]


@dataclass(frozen=True)
class ClaimEntry:
    id: str
    documentPath: str
    heading: str
    type: str
    anchor: str
    digest: str


def _normalize_metadata_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def _metadata(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines()[:80]:
        match = METADATA_RE.match(line)
        if match:
            values[_normalize_metadata_key(match.group(1))] = match.group(2).strip()
    return values


def _strip_markdown(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    return value.strip()


def _split_metadata_list(value: str) -> list[str]:
    if not value or value.strip().lower() in {"none", "n/a", "na"}:
        return []
    links = [target.strip() for _, target in MARKDOWN_LINK_RE.findall(value)]
    without_links = MARKDOWN_LINK_RE.sub("", value)
    parts = [
        _strip_markdown(part).strip(" .;")
        for part in re.split(r",|;", without_links)
        if _strip_markdown(part).strip(" .;")
    ]
    return sorted(dict.fromkeys([*links, *parts]))


def _related_implementation(value: str) -> list[str]:
    if not value or value.strip().lower() in {"none", "n/a", "na"}:
        return []
    explicit = _split_metadata_list(value)
    tokens = [
        token.strip(" .;`")
        for token in IMPLEMENTATION_TOKEN_RE.findall(value)
        if token.strip(" .;`")
    ]
    return sorted(dict.fromkeys([*explicit, *tokens]))


def _infer_viewpoint(path: str, metadata: dict[str, str]) -> str:
    declared_viewpoint = metadata.get("viewpoint", "")
    if declared_viewpoint:
        return declared_viewpoint

    declared_class = metadata.get("documentclass", "")
    if declared_class.lower() in VIEWPOINTS:
        return declared_class

    name = path.rsplit("/", 1)[-1].lower()
    if "contract" in name:
        return "Module Contract Specification"
    if name.endswith("architecture.md"):
        if path.count("/") == 1:
            return "System Architecture View"
        return "Module Architecture View"
    if name.endswith("design.md") or name.endswith("system.md"):
        return "System / Feature Design View"
    return "Cross-Cutting Concept View"


def _anchor_for_heading(heading: str) -> str:
    anchor = heading.strip().lower()
    anchor = re.sub(r"`([^`]+)`", r"\1", anchor)
    anchor = re.sub(r"<[^>]+>", "", anchor)
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor.strip())
    return anchor


def _heading_sections(text: str) -> list[tuple[int, str, str]]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    sections: list[tuple[int, str, str]] = []
    for position, (line_index, level, heading) in enumerate(headings):
        next_index = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        section_text = "\n".join(lines[line_index:next_index]).strip()
        sections.append((level, heading, section_text))
    return sections


def _claim_type(level: int) -> str:
    if level == 1:
        return "document"
    if level == 2:
        return "section"
    return "subsection"


def _claim_id(path: str, anchor: str) -> str:
    raw = f"{path}#{anchor}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"claim:{digest}"


def _claim_digest(path: str, heading: str, section_text: str) -> str:
    raw = f"{path}\n{heading}\n{section_text}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _warning(rule: str, path: str, message: str, detail: str = "") -> Finding:
    return Finding(
        rule=rule,
        severity="advisory",
        path=path,
        message=message,
        detail=detail,
    )


def _constitution_doc(root: Path) -> tuple[str, str] | None:
    path = root / CONSTITUTION_PATH
    try:
        return CONSTITUTION_PATH, path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _index_paths(paths: Iterable[str], *, root: Path) -> list[str]:
    docs = [path for path in paths if is_canonical_doc(path)]
    if (root / CONSTITUTION_PATH).is_file():
        docs.append(CONSTITUTION_PATH)
    return sorted(dict.fromkeys(docs))


def build_index(paths: Iterable[str] | None = None, *, root: Path | None = None) -> dict:
    """Build the advisory documentation index payload."""

    root = root or REPO_ROOT
    input_paths = paths if paths is not None else all_doc_paths(root=root)
    selected_paths = _index_paths(input_paths, root=root)
    doc_files = load_docs(
        [path for path in selected_paths if path != CONSTITUTION_PATH],
        root=root,
    )

    constitution = _constitution_doc(root)
    if constitution is not None and CONSTITUTION_PATH in selected_paths:
        constitution_path, constitution_text = constitution
        from check_documentation_architecture import DocFile

        doc_files.append(DocFile(path=constitution_path, text=constitution_text))

    docs_by_path = {doc.path: doc for doc in doc_files}
    warnings = run_checks(
        [doc for doc in doc_files if doc.path != CONSTITUTION_PATH],
        focus_paths=None,
    )

    document_entries: list[DocumentEntry] = []
    claim_entries: list[ClaimEntry] = []

    for path in selected_paths:
        doc = docs_by_path.get(path)
        if doc is None:
            warnings.append(
                _warning(
                    "document-unreadable",
                    path,
                    "Document could not be read as UTF-8 and was skipped.",
                )
            )
            continue

        metadata = _metadata(doc.text)
        if path == CONSTITUTION_PATH:
            document_class = "Canonical declarative"
            viewpoint = "Constitution / Document Model"
            authority = "Non-negotiable project principles and constraints"
            owning_surface = "MoonMind"
            related_docs: list[str] = ["docs/Workflows/MoonSpecDocumentModel.md"]
            related_implementation: list[str] = []
        else:
            document_class = metadata.get("documentclass") or "Canonical declarative"
            viewpoint = _infer_viewpoint(path, metadata)
            authority = metadata.get("authority", "")
            owning_surface = metadata.get("owningsurface", "")
            related_docs = _split_metadata_list(metadata.get("relateddocs", ""))
            related_implementation = _related_implementation(
                metadata.get("relatedimplementation", "")
            )

        if not metadata.get("documentclass") and path != CONSTITUTION_PATH:
            warnings.append(
                _warning(
                    "missing-index-document-class",
                    path,
                    "Document entry uses location-derived `Canonical declarative`; "
                    "add a Document Class marker for stronger indexing.",
                )
            )
        if not authority:
            warnings.append(
                _warning(
                    "missing-index-authority",
                    path,
                    "Document entry has no declared Authority metadata.",
                )
            )
        if not owning_surface:
            warnings.append(
                _warning(
                    "missing-index-owning-surface",
                    path,
                    "Document entry has no declared Owning Surface metadata.",
                )
            )

        document_entries.append(
            DocumentEntry(
                path=path,
                documentClass=document_class,
                viewpoint=viewpoint,
                authority=authority,
                owningSurface=owning_surface,
                relatedDocs=related_docs,
                relatedImplementation=related_implementation,
            )
        )

        for level, heading, section_text in _heading_sections(doc.text):
            anchor = _anchor_for_heading(heading)
            claim_entries.append(
                ClaimEntry(
                    id=_claim_id(path, anchor),
                    documentPath=path,
                    heading=heading,
                    type=_claim_type(level),
                    anchor=f"{path}#{anchor}",
                    digest=_claim_digest(path, heading, section_text),
                )
            )

    warning_dicts = [
        asdict(warning)
        for warning in sorted(warnings, key=lambda w: (w.path, w.rule, w.message))
    ]
    return {
        "tool": "index_moonspec_docs",
        "issue": ISSUE,
        "sourceIssue": "MM-927",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "advisoryOnly": True,
        "canonicalRoots": ["docs", CONSTITUTION_PATH],
        "excludedCanonicalDocDirs": list(NON_CANONICAL_DOC_DIRS),
        "documentCount": len(document_entries),
        "claimCount": len(claim_entries),
        "warningCount": len(warning_dicts),
        "documents": [asdict(entry) for entry in document_entries],
        "claims": [asdict(entry) for entry in claim_entries],
        "warnings": warning_dicts,
    }


def write_index(payload: dict, output: Path, *, root: Path | None = None) -> Path:
    root = root or REPO_ROOT
    output_path = output if output.is_absolute() else root / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Artifact JSON output path (default: {DEFAULT_OUTPUT.as_posix()}).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Console output format. The artifact file is always JSON.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when advisory warnings exist. Default is advisory-only.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional explicit canonical doc paths to index. docs/tmp remains excluded.",
    )
    args = parser.parse_args(argv)

    payload = build_index(args.paths or None)
    output_path = write_index(payload, args.output)

    if args.format == "json":
        print(json.dumps({"output": str(output_path), **payload}, indent=2, sort_keys=True))
    else:
        print(
            "MM-930 MoonSpec documentation index "
            f"wrote {payload['documentCount']} document(s), "
            f"{payload['claimCount']} claim(s), and "
            f"{payload['warningCount']} advisory warning(s) to {output_path}."
        )

    if args.strict and payload["warningCount"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
