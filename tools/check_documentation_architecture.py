#!/usr/bin/env python3
"""MM-908 advisory documentation-architecture validation helper.

Advisory-only (v1): this helper NEVER blocks CI. It emits *structured* warnings
that flag likely drift from the MoonSpec Documentation Architecture Standard
(``docs/DocumentationArchitecture.md``) and the MoonSpec Document Model
(``docs/Workflows/MoonSpecDocumentModel.md``) so reviewers can catch obvious
documentation-architecture problems early without gating the pipeline.

Findings carry stable rule ids and a severity so the convention can later be
promoted to a blocking CI gate (``--strict``) once it proves stable, without
reworking callers. By default the tool exits ``0`` regardless of findings.

Traceability: MM-908 (source design MM-900, "Implement MoonSpec Documentation
Architecture Standard"); covers DESIGN-REQ-018.

Enumerated advisory checks:
  1. ``missing-document-class``      new canonical docs missing a Document Class.
  2. ``imperative-plan-in-canonical-area``
                                     new ``*Plan.md`` / tracker docs placed
                                     outside ``docs/tmp/`` (canonical folders).
  3. ``duplicate-canonical-authority``
                                     duplicate canonical docs with overlapping
                                     authority.
  4. ``contract-missing-authority-statement``
                                     contract docs missing an authority
                                     statement.
  5. ``discouraged-decision-record`` separate ``decisions/`` or ADR-style docs
                                     introduced instead of embedded rationale.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]

# Canonical declarative documentation lives under ``docs/`` (and the
# constitution) per the Document Model. These ``docs/`` subtrees are NOT part of
# the canonical Architecture Description and are excluded from canonical checks.
CANONICAL_DOCS_ROOT = "docs"
NON_CANONICAL_DOC_DIRS = ("docs/tmp", "docs/assets", "docs/ReleaseNotes")

# Approved imperative working areas for plans/trackers (Document Model §
# "Imperative working documents"). Plans outside these are flagged.
APPROVED_IMPERATIVE_DIRS = ("docs/tmp",)

# A canonical declarative doc should declare its Document Class. v1 accepts an
# explicit ``Document Class:`` marker OR any recognized base class / viewpoint
# name (so existing docs that name their viewpoint inline are not flagged).
DOCUMENT_CLASS_MARKER_RE = re.compile(r"document\s+class\s*[:|]", re.IGNORECASE)
DOCUMENT_CLASS_TERMS = (
    # Document Model base classes.
    "canonical declarative document",
    "temporary execution artifact",
    "imperative working document",
    # Documentation Architecture Standard viewpoints.
    "system architecture view",
    "module architecture view",
    "system / feature design view",
    "feature design view",
    "module contract specification",
    "cross-cutting concept view",
)
DOCUMENT_CLASS_TERMS_RE = re.compile(
    "|".join(re.escape(term) for term in DOCUMENT_CLASS_TERMS), re.IGNORECASE
)

# ``*Plan.md`` plus Status / Checklist Tracker naming (imperative working docs).
IMPERATIVE_DOC_NAME_RE = re.compile(
    r"(?:Plan|Tracker|Checklist|Backlog)\.md$", re.IGNORECASE
)

# Contract docs (Module Contract Specification) by preferred naming.
CONTRACT_DOC_NAME_RE = re.compile(r"Contract(?:s)?\.md$", re.IGNORECASE)

# An authority statement names the single authoritative owner / source of truth
# (Documentation Architecture §6.1 contract authority rule).
AUTHORITY_STATEMENT_RE = re.compile(
    r"authoritative|source of truth|authority|owned by|single .*home|owns\b",
    re.IGNORECASE,
)

# Separate decision records / ADRs are discouraged; rationale should be embedded
# in the owning canonical view instead.
DECISION_DIR_RE = re.compile(r"(?:^|/)(?:decisions|adr|adrs)/", re.IGNORECASE)
ADR_FILE_RE = re.compile(r"(?:^|/)(?:adr[-_].+|\d{3,4}[-_].+-decision|.*\bADR\b.*)\.md$")

# A System Architecture View is one-per-project. It is identified only by an
# explicit ``Document Class: System Architecture View`` declaration -- not by a
# prose mention of the viewpoint -- so docs that merely *discuss* viewpoints
# (the standard, this validation doc) are not miscounted as views.
SYSTEM_ARCH_DECLARATION_RE = re.compile(
    r"document\s+class\s*[:|]\s*\**\s*system architecture view", re.IGNORECASE
)

SEVERITY_ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    """A single structured advisory finding.

    ``rule`` is a stable identifier and ``severity`` is ``advisory`` in v1; both
    are carried explicitly so a future CI gate can select/promote rules without
    changing the emitting code.
    """

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

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def is_canonical_doc(path: str) -> bool:
    """True for canonical declarative docs (``docs/**.md`` minus excluded trees)."""

    if not path.endswith(".md"):
        return False
    if not _is_under(path, CANONICAL_DOCS_ROOT):
        return False
    return not any(_is_under(path, excluded) for excluded in NON_CANONICAL_DOC_DIRS)


def _h1_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip().lower()
        if stripped:
            # Front matter / metadata before the title is fine; keep scanning.
            continue
    return None


def check_missing_document_class(docs: Sequence[DocFile]) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        if not is_canonical_doc(doc.path):
            continue
        if DOCUMENT_CLASS_MARKER_RE.search(doc.text):
            continue
        if DOCUMENT_CLASS_TERMS_RE.search(doc.text):
            continue
        findings.append(
            Finding(
                rule="missing-document-class",
                severity=SEVERITY_ADVISORY,
                path=doc.path,
                message=(
                    "Canonical doc does not declare a Document Class. Add a "
                    "`Document Class:` marker naming its viewpoint "
                    "(see docs/DocumentationArchitecture.md)."
                ),
            )
        )
    return findings


def check_imperative_plan_in_canonical_area(docs: Sequence[DocFile]) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        if not doc.path.endswith(".md"):
            continue
        if not _is_under(doc.path, CANONICAL_DOCS_ROOT):
            continue
        if any(_is_under(doc.path, allowed) for allowed in APPROVED_IMPERATIVE_DIRS):
            continue
        if not IMPERATIVE_DOC_NAME_RE.search(doc.name):
            continue
        findings.append(
            Finding(
                rule="imperative-plan-in-canonical-area",
                severity=SEVERITY_ADVISORY,
                path=doc.path,
                message=(
                    "Imperative plan/tracker doc placed in a canonical folder. "
                    "Move it under docs/tmp/ or a gitignored handoff path "
                    "(see docs/DocumentationArchitecture.md §4)."
                ),
            )
        )
    return findings


def check_duplicate_canonical_authority(docs: Sequence[DocFile]) -> list[Finding]:
    findings: list[Finding] = []
    canonical = [doc for doc in docs if is_canonical_doc(doc.path)]

    # Duplicate H1 titles imply two docs claiming the same canonical authority.
    by_title: dict[str, list[str]] = {}
    for doc in canonical:
        title = _h1_title(doc.text)
        if title:
            by_title.setdefault(title, []).append(doc.path)
    for title, paths in sorted(by_title.items()):
        if len(paths) < 2:
            continue
        ordered = sorted(paths)
        for path in ordered:
            others = [other for other in ordered if other != path]
            findings.append(
                Finding(
                    rule="duplicate-canonical-authority",
                    severity=SEVERITY_ADVISORY,
                    path=path,
                    message=(
                        "Multiple canonical docs share the title "
                        f"'{title}', implying overlapping authority. Consolidate "
                        "to one source of truth (Document Model precedence rule)."
                    ),
                    detail="overlaps: " + ", ".join(others),
                )
            )

    # More than one doc declaring the System Architecture View viewpoint
    # (one-per-project, §3.1). Identified by declaration, not naming, so the
    # documentation-architecture standard itself is not miscounted as a view.
    system_views = sorted(
        doc.path for doc in canonical if SYSTEM_ARCH_DECLARATION_RE.search(doc.text)
    )
    if len(system_views) > 1:
        for path in system_views:
            others = [other for other in system_views if other != path]
            findings.append(
                Finding(
                    rule="duplicate-canonical-authority",
                    severity=SEVERITY_ADVISORY,
                    path=path,
                    message=(
                        "More than one doc declares the System Architecture View "
                        "viewpoint; there is one Architecture Description per "
                        "project (see docs/DocumentationArchitecture.md §3.1)."
                    ),
                    detail="overlaps: " + ", ".join(others),
                )
            )
    return findings


def check_contract_missing_authority_statement(
    docs: Sequence[DocFile],
) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        if not is_canonical_doc(doc.path):
            continue
        if not CONTRACT_DOC_NAME_RE.search(doc.name):
            continue
        if AUTHORITY_STATEMENT_RE.search(doc.text):
            continue
        findings.append(
            Finding(
                rule="contract-missing-authority-statement",
                severity=SEVERITY_ADVISORY,
                path=doc.path,
                message=(
                    "Contract doc has no authority statement naming its single "
                    "authoritative owner / source of truth "
                    "(see docs/DocumentationArchitecture.md §6.1)."
                ),
            )
        )
    return findings


def check_discouraged_decision_record(docs: Sequence[DocFile]) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        if not doc.path.endswith(".md"):
            continue
        if not _is_under(doc.path, CANONICAL_DOCS_ROOT):
            continue
        if DECISION_DIR_RE.search(doc.path) or ADR_FILE_RE.search(doc.path):
            findings.append(
                Finding(
                    rule="discouraged-decision-record",
                    severity=SEVERITY_ADVISORY,
                    path=doc.path,
                    message=(
                        "Separate decisions/ or ADR-style doc is discouraged. "
                        "Embed the rationale in the owning canonical view "
                        "instead (see docs/DocumentationArchitecture.md)."
                    ),
                )
            )
    return findings


ALL_CHECKS = (
    check_missing_document_class,
    check_imperative_plan_in_canonical_area,
    check_duplicate_canonical_authority,
    check_contract_missing_authority_statement,
    check_discouraged_decision_record,
)


def run_checks(
    docs: Sequence[DocFile], *, focus_paths: Iterable[str] | None = None
) -> list[Finding]:
    """Run every advisory check over ``docs``.

    ``focus_paths`` scopes *reported* findings to a subset of paths while still
    evaluating every check against the full ``docs`` context. Global checks such
    as ``check_duplicate_canonical_authority`` must see unchanged canonical docs
    to detect a changed doc duplicating an existing one; passing the full
    canonical set as ``docs`` and the changed subset as ``focus_paths`` keeps
    that detection working without reporting on docs the caller did not target.
    When ``focus_paths`` is ``None`` every finding is reported.
    """

    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(docs))
    if focus_paths is not None:
        focus = set(focus_paths)
        findings = [finding for finding in findings if finding.path in focus]
    return sorted(findings, key=lambda f: (f.path, f.rule, f.message))


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


def _git_lines(args: Sequence[str], *, root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def changed_doc_paths(base_ref: str, *, root: Path = REPO_ROOT) -> list[str] | None:
    """Markdown docs added/modified vs ``base_ref`` plus untracked working-tree docs.

    Returns ``None`` when git is unavailable or the base ref cannot be resolved,
    so the caller can fall back to a full scan.
    """

    tracked = _git_lines(
        ["diff", "--name-only", "--diff-filter=AMR", base_ref, "--"], root=root
    )
    if tracked is None:
        return None
    untracked = (
        _git_lines(["ls-files", "--others", "--exclude-standard"], root=root) or []
    )
    paths = {
        path
        for path in (*tracked, *untracked)
        if path.endswith(".md") and _is_under(path, CANONICAL_DOCS_ROOT)
    }
    return sorted(paths)


def _format_text(findings: Sequence[Finding], *, scope: str) -> str:
    header = (
        "MM-908 advisory documentation-architecture check "
        f"(scope={scope}, advisory-only — does not block CI)."
    )
    if not findings:
        return f"{header}\nNo advisory findings."
    lines = [header, f"{len(findings)} advisory finding(s):"]
    for finding in findings:
        line = (
            f"  [{finding.severity}] {finding.path}: {finding.rule}: "
            f"{finding.message}"
        )
        if finding.detail:
            line += f" ({finding.detail})"
        lines.append(line)
    return "\n".join(lines)


def _format_json(findings: Sequence[Finding], *, scope: str) -> str:
    payload = {
        "tool": "check_documentation_architecture",
        "issue": "MM-908",
        "scope": scope,
        "advisory_only": True,
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("changed", "all"),
        default="changed",
        help=(
            "'changed' (default): only docs added/modified vs --base. "
            "'all': scan the whole docs/ tree."
        ),
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Git base ref for --scope changed (default: origin/main).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero when advisory findings exist. v1 CI MUST NOT use this; "
            "reserved for a future promotion to a blocking gate."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Explicit doc paths to check (overrides --scope).",
    )
    args = parser.parse_args(argv)

    # ``focus_paths`` scopes which paths are *reported*; ``context_paths`` is the
    # full set loaded so global checks (duplicate authority) can compare a
    # changed doc against unchanged canonical docs. ``focus_paths=None`` reports
    # every finding (the ``all`` / full-scan scopes).
    focus_paths: list[str] | None
    if args.paths:
        scope = "explicit"
        focus_paths = list(args.paths)
        context_paths = sorted(set(focus_paths) | set(all_doc_paths()))
    elif args.scope == "all":
        scope = "all"
        focus_paths = None
        context_paths = all_doc_paths()
    else:
        changed = changed_doc_paths(args.base)
        if changed is None:
            scope = "all (git unavailable, fell back to full scan)"
            focus_paths = None
            context_paths = all_doc_paths()
        else:
            scope = f"changed vs {args.base}"
            focus_paths = changed
            context_paths = sorted(set(changed) | set(all_doc_paths()))

    docs = load_docs(context_paths)
    findings = run_checks(docs, focus_paths=focus_paths)

    if args.format == "json":
        print(_format_json(findings, scope=scope))
    else:
        print(_format_text(findings, scope=scope))

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
