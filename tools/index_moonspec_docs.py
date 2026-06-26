#!/usr/bin/env python3
"""MM-938 advisory MoonSpec documentation indexer.

Builds a machine-readable JSON index of canonical MoonSpec documents and stable
heading claims without mutating checked-in documentation.

Traceability: MM-938, MM-930 (source issue MM-927, "Moon Spec Doc Architecture
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
    CANONICAL_CLAIM_ID_RE,
    CANONICAL_CLAIM_PREFIXES,
    NON_CANONICAL_DOC_DIRS,
    REPO_ROOT,
    Finding,
    all_doc_paths,
    is_canonical_doc,
    load_docs,
    run_checks,
)


ISSUE = "MM-930"
IMPLEMENTATION_ISSUE = "MM-938"
DEFAULT_OUTPUT = Path("artifacts/moonspec-doc-index/index.json")
CONSTITUTION_PATH = ".specify/memory/constitution.md"
CLAIM_PREFIX_CLASS = {
    "DOC-REQ": "requirement",
    "CONTRACT": "contract",
    "INV": "invariant",
    "NON-GOAL": "non-goal",
    "QUALITY": "quality",
    "TEST": "test",
}

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
STABLE_CLAIM_PREFIX_RE = "|".join(
    re.escape(prefix) for prefix in CANONICAL_CLAIM_PREFIXES
)
STABLE_CLAIM_HEADING_RE = re.compile(
    rf"^(?P<id>(?:{STABLE_CLAIM_PREFIX_RE})-\d{{3}})"
    r"(?P<separator>\s+|:\s*| -\s*|$)(?P<summary>.*)$"
)
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
    identityKind: str
    documentPath: str
    sourcePath: str
    section: str
    heading: str
    summary: str
    type: str
    claimClass: str
    anchor: str
    digest: str
    authority: str
    owningSurface: str
    relatedDocs: list[str]
    relatedImplementation: list[str]


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
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
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


def _parse_stable_claim_heading(heading: str) -> tuple[str, str, str] | None:
    normalized = _strip_markdown(heading)
    match = STABLE_CLAIM_HEADING_RE.match(normalized)
    if not match:
        return None
    claim_id = match.group("id")
    if not CANONICAL_CLAIM_ID_RE.fullmatch(claim_id):
        return None
    prefix = claim_id.rsplit("-", 1)[0]
    summary = match.group("summary").strip(" -:\t") or claim_id
    return claim_id, CLAIM_PREFIX_CLASS.get(prefix, "claim"), summary


def _claim_digest(path: str, heading: str, section_text: str) -> str:
    raw = f"{path}\n{heading}\n{section_text}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unique_anchor(anchor: str, seen: dict[str, int]) -> str:
    count = seen.get(anchor, 0)
    seen[anchor] = count + 1
    if count == 0:
        return anchor
    return f"{anchor}-{count}"


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
    resolved_root = root.resolve()
    docs: list[str] = []
    for path in paths:
        path_obj = Path(path)
        try:
            candidate = path_obj if path_obj.is_absolute() else root / path_obj
            resolved_candidate = candidate.resolve()
            if resolved_candidate.is_relative_to(resolved_root):
                normalized = resolved_candidate.relative_to(resolved_root).as_posix()
            else:
                normalized = path_obj.as_posix()
        except (OSError, RuntimeError, ValueError):
            normalized = path_obj.as_posix()
        if is_canonical_doc(normalized):
            docs.append(normalized)
    if (root / CONSTITUTION_PATH).is_file():
        docs.append(CONSTITUTION_PATH)
    return sorted(dict.fromkeys(docs))


def _input_source_warnings(paths: Iterable[str], *, root: Path) -> list[Finding]:
    resolved_root = root.resolve()
    warnings: list[Finding] = []
    for path in paths:
        path_obj = Path(path)
        try:
            candidate = path_obj if path_obj.is_absolute() else root / path_obj
            resolved_candidate = candidate.resolve()
            if resolved_candidate.is_relative_to(resolved_root):
                normalized = resolved_candidate.relative_to(resolved_root).as_posix()
            else:
                normalized = path_obj.as_posix()
        except (OSError, RuntimeError, ValueError):
            normalized = path_obj.as_posix()
        if is_canonical_doc(normalized):
            continue
        if normalized == CONSTITUTION_PATH:
            continue
        warnings.append(
            _warning(
                "non-file-source-skipped",
                normalized,
                "Input is not a canonical repository markdown file and was not indexed.",
                "The doc index never fabricates stable canonical claim IDs for Jira text, inline text, or other non-file sources.",
            )
        )
    return warnings


def build_index(
    paths: Iterable[str] | None = None,
    *,
    root: Path | None = None,
    missing_stable_claim_policy: str = "report",
) -> dict:
    """Build the advisory documentation index payload."""

    if missing_stable_claim_policy not in {"report", "fail", "ignore"}:
        raise ValueError(
            "missing_stable_claim_policy must be one of: report, fail, ignore"
        )

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
    if paths is not None:
        warnings.extend(_input_source_warnings(paths, root=root))

    document_entries: list[DocumentEntry] = []
    claim_entries: list[ClaimEntry] = []
    missing_stable_claim_paths: list[str] = []

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

        seen_anchors: dict[str, int] = {}
        stable_claim_count = 0
        for level, heading, section_text in _heading_sections(doc.text):
            anchor = _unique_anchor(_anchor_for_heading(heading), seen_anchors)
            stable_claim = _parse_stable_claim_heading(heading)
            claim_id = _claim_id(path, anchor)
            identity_kind = "generated"
            claim_class = _claim_type(level)
            summary = _strip_markdown(heading)
            if stable_claim is not None:
                claim_id, claim_class, summary = stable_claim
                identity_kind = "stable"
                stable_claim_count += 1
            claim_entries.append(
                ClaimEntry(
                    id=claim_id,
                    identityKind=identity_kind,
                    documentPath=path,
                    sourcePath=path,
                    section=f"{path}#{anchor}",
                    heading=heading,
                    summary=summary,
                    type=_claim_type(level),
                    claimClass=claim_class,
                    anchor=f"{path}#{anchor}",
                    digest=_claim_digest(path, heading, section_text),
                    authority=authority,
                    owningSurface=owning_surface,
                    relatedDocs=related_docs,
                    relatedImplementation=related_implementation,
                )
            )
        if (
            missing_stable_claim_policy != "ignore"
            and path != CONSTITUTION_PATH
            and stable_claim_count == 0
        ):
            missing_stable_claim_paths.append(path)
            warnings.append(
                _warning(
                    "missing-stable-claim-id",
                    path,
                    "Canonical doc has no stable DOC-REQ, CONTRACT, INV, NON-GOAL, QUALITY, or TEST claim headings.",
                    "Assign stable claim IDs to durable canonical claims, or run with --missing-stable-claim-policy ignore for exploratory indexing.",
                )
            )

    warning_dicts = [
        asdict(warning)
        for warning in sorted(warnings, key=lambda w: (w.path, w.rule, w.message))
    ]
    return {
        "tool": "index_moonspec_docs",
        "issue": ISSUE,
        "implementationIssue": IMPLEMENTATION_ISSUE,
        "sourceIssue": "MM-927",
        "traceability": [IMPLEMENTATION_ISSUE, ISSUE, "MM-927"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "advisoryOnly": True,
        "missingStableClaimPolicy": missing_stable_claim_policy,
        "missingStableClaimDocumentCount": len(missing_stable_claim_paths),
        "missingStableClaimDocuments": missing_stable_claim_paths,
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
        "--missing-stable-claim-policy",
        choices=("report", "fail", "ignore"),
        default="report",
        help=(
            "How to handle canonical docs with no stable claim headings: report "
            "advisory findings, fail after writing the artifact, or ignore."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional explicit canonical doc paths to index. docs/tmp remains excluded.",
    )
    args = parser.parse_args(argv)

    payload = build_index(
        args.paths or None,
        missing_stable_claim_policy=args.missing_stable_claim_policy,
    )
    output_path = write_index(payload, args.output)

    if args.format == "json":
        print(json.dumps({"output": str(output_path), **payload}, indent=2, sort_keys=True))
    else:
        print(
            "MM-938 MoonSpec documentation index "
            f"wrote {payload['documentCount']} document(s), "
            f"{payload['claimCount']} claim(s), and "
            f"{payload['warningCount']} advisory warning(s) to {output_path}."
        )

    if (
        args.missing_stable_claim_policy == "fail"
        and payload["missingStableClaimDocumentCount"]
    ):
        return 1
    if args.strict and payload["warningCount"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
