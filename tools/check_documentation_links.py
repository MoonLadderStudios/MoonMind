#!/usr/bin/env python3
"""MM-3966 bounded local documentation link/anchor verifier.

Advisory-only: this helper NEVER blocks CI. It validates *local* Markdown
link targets under ``docs/`` so reviewers catch broken relative paths,
images, reference-style links, and ``#anchor`` fragments without claiming the
broader architecture checker (``tools/check_documentation_architecture.py``)
catches rules it does not implement.

Scope (documented per MoonLadderStudios/MoonMind#3966):

* Default scope is the canonical declarative set from
  :func:`check_documentation_architecture.is_canonical_doc` (``docs/**.md``
  minus ``docs/tmp``, ``docs/assets``, ``docs/ReleaseNotes``), further minus
  frozen review evidence that must not be edited in place:
  ``docs/DocsReview.md`` (2026-08-11 review snapshot) and
  ``docs/tmp/historical/`` (archived evidence). Both are dispositioned in
  ``docs/tmp/DocsReviewRevalidationMM3966.md``, not repaired.
* Code-fence examples are *classified separately*, not link-checked: an
  ``../X.md`` path inside a fenced block may be a correct example for a
  ``docs/tmp/`` working document, so fence contents are stripped before
  scanning.
* External URLs (``http(s):``, ``mailto:``, ``data:``) are counted, never
  fetched: no indiscriminate network probing.
* Existence checks use the OS filesystem, which is case-sensitive on the
  Linux checkout, so case mismatches fail like any other missing target.

Finding rules (stable ids, ``advisory`` severity):

* ``broken-local-link``   -- relative link/image target does not exist.
* ``broken-local-anchor`` -- ``#fragment`` matches no heading slug or
  explicit ``<a name|id>`` anchor in the target document.
* ``undefined-link-reference`` -- a ``[text][ref]`` / ``[ref][]`` usage has
  no ``[ref]: target`` definition.

Usage mirrors the architecture checker::

    python tools/check_documentation_links.py                 # changed docs
    python tools/check_documentation_links.py --scope all     # canonical set
    python tools/check_documentation_links.py --format json
    python tools/check_documentation_links.py docs/MyDoc.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from check_documentation_architecture import is_canonical_doc
except ImportError:  # pragma: no cover - fallback when tools/ is not on sys.path
    _ROOT = "docs"
    _EXCLUDED = ("docs/tmp", "docs/assets", "docs/ReleaseNotes")

    def _is_under(path: str, prefix: str) -> bool:
        return path == prefix or path.startswith(prefix + "/")

    def is_canonical_doc(path: str) -> bool:
        if not path.endswith(".md"):
            return False
        if path.endswith(".template.md"):
            return False
        if not _is_under(path, _ROOT):
            return False
        return not any(_is_under(path, excluded) for excluded in _EXCLUDED)


# Frozen review evidence: dispositioned, never repaired in place (see
# docs/tmp/DocsReviewRevalidationMM3966.md). Excluded from link findings.
FROZEN_EVIDENCE_PATHS = ("docs/DocsReview.md",)
FROZEN_EVIDENCE_DIRS = ("docs/tmp/historical",)

SEVERITY_ADVISORY = "advisory"

_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)")
_REF_USE_RE = re.compile(r"\[[^\]]*\]\[([^\]]*)\]")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_EXPLICIT_ANCHOR_RE = re.compile(
    r'<a\s+[^>]*(?:name|id)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE
)
_EXTERNAL_RE = re.compile(r"^(?:https?://|mailto:|data:)", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class DocFile:
    path: str  # repo-relative POSIX path
    text: str


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def is_frozen_evidence(path: str) -> bool:
    if path in FROZEN_EVIDENCE_PATHS:
        return True
    return any(_is_under(path, d) for d in FROZEN_EVIDENCE_DIRS)


def is_link_scope(path: str) -> bool:
    """True for docs covered by this verifier's default scope."""
    return is_canonical_doc(path) and not is_frozen_evidence(path)


def _strip_fences(text: str) -> str:
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _split_target(raw: str) -> tuple[str, str | None]:
    raw = raw.strip().strip("<>").strip()
    if "#" in raw:
        path_part, anchor = raw.split("#", 1)
        return path_part.strip(), anchor.strip() or None
    return raw, None


def _github_slug(heading: str) -> str:
    # GitHub heading slugs: lowercase, strip punctuation, then map *each*
    # remaining space to one hyphen (runs are NOT collapsed, so "A  B"
    # becomes "a--b").
    text = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text).strip("-")


def document_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for line in _strip_fences(text).splitlines():
        match = _HEADING_RE.match(line)
        if match:
            anchors.add(_github_slug(match.group(1)))
    for match in _EXPLICIT_ANCHOR_RE.finditer(text):
        anchors.add(match.group(1))
    return anchors


def _iter_link_targets(body: str) -> list[tuple[str, bool]]:
    """Return ``(raw_target, is_image)`` for inline links and images."""
    targets: list[tuple[str, bool]] = []
    for match in _INLINE_LINK_RE.finditer(body):
        full = match.group(0)
        targets.append((match.group(1).strip(), full.startswith("!")))
    return targets


def _iter_reference_targets(body: str) -> tuple[dict[str, str], list[str]]:
    """Return ``(definitions, used_refs)`` for reference-style links."""
    definitions: dict[str, str] = {}
    for line in body.splitlines():
        match = _REF_DEF_RE.match(line)
        if match:
            name_match = re.match(r"^\s{0,3}\[([^\]]+)\]", line)
            if name_match:
                definitions[name_match.group(1).strip().lower()] = match.group(1)
    used: list[str] = []
    for match in _REF_USE_RE.finditer(body):
        ref = match.group(1).strip()
        if not ref:
            # ``[text][]`` collapsed form: the text itself is the ref.
            text_match = re.match(r"\[([^\]]*)\]\[\]", match.group(0))
            if text_match:
                ref = text_match.group(1).strip()
        used.append(ref.lower())
    return definitions, used


def check_doc(
    doc: DocFile, *, doc_texts: dict[str, str], root: Path = REPO_ROOT
) -> tuple[list[Finding], int]:
    """Check one doc. Returns ``(findings, external_count)``."""
    findings: list[Finding] = []
    external_count = 0
    body = _strip_fences(doc.text)
    base = (root / os.path.dirname(doc.path)).as_posix()

    candidates: list[str] = []
    for raw, _is_image in _iter_link_targets(body):
        if not raw:
            continue
        if _EXTERNAL_RE.match(raw) or raw.startswith("mailto:"):
            external_count += 1
            continue
        path_part, _anchor = _split_target(raw)
        if not path_part:
            candidates.append(raw)  # same-doc anchor; validated below
            continue
        if path_part.startswith("/") or re.match(
            r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path_part
        ):
            external_count += 1
            continue
        candidates.append(raw)

    definitions, used_refs = _iter_reference_targets(body)
    for ref in used_refs:
        if ref and ref not in definitions:
            findings.append(
                Finding(
                    rule="undefined-link-reference",
                    severity=SEVERITY_ADVISORY,
                    path=doc.path,
                    message=(
                        f"Reference-style link `[{ref}]` has no `[ref]: target` "
                        "definition in this document."
                    ),
                )
            )
    for _name, target in definitions.items():
        target = target.strip()
        if _EXTERNAL_RE.match(target):
            external_count += 1
            continue
        path_part, _anchor = _split_target(target)
        if not path_part or path_part.startswith("/"):
            if path_part.startswith("/"):
                external_count += 1
            else:
                candidates.append(target)
            continue
        candidates.append(target)

    for raw in candidates:
        path_part, anchor = _split_target(raw)
        if path_part:
            resolved = os.path.normpath(os.path.join(base, path_part))
            if not (root / resolved).exists():
                findings.append(
                    Finding(
                        rule="broken-local-link",
                        severity=SEVERITY_ADVISORY,
                        path=doc.path,
                        message=(
                            f"Local link target `{raw}` does not exist "
                            f"(resolved to `{resolved}`)."
                        ),
                        detail=f"resolved: {resolved}",
                    )
                )
                continue
            target_rel = Path(resolved).as_posix()
        else:
            target_rel = doc.path
        if anchor:
            target_text = doc_texts.get(target_rel)
            if target_text is None:
                try:
                    target_text = (root / target_rel).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    target_text = None
            if target_text is not None and anchor not in document_anchors(
                target_text
            ):
                findings.append(
                    Finding(
                        rule="broken-local-anchor",
                        severity=SEVERITY_ADVISORY,
                        path=doc.path,
                        message=(
                            f"Anchor `#{anchor}` in `{raw}` matches no heading "
                            f"or explicit anchor in `{target_rel}`."
                        ),
                        detail=f"target: {target_rel}",
                    )
                )
    return findings, external_count


def run_checks(
    docs: Sequence[DocFile],
    *,
    focus_paths: Iterable[str] | None = None,
    root: Path = REPO_ROOT,
) -> tuple[list[Finding], int]:
    doc_texts = {doc.path: doc.text for doc in docs}
    focus = set(focus_paths) if focus_paths is not None else None
    findings: list[Finding] = []
    external_total = 0
    for doc in docs:
        if not is_link_scope(doc.path):
            continue
        if focus is not None and doc.path not in focus:
            continue
        doc_findings, external_count = check_doc(doc, doc_texts=doc_texts, root=root)
        findings.extend(doc_findings)
        external_total += external_count
    # Dedupe repeated identical findings (e.g. one undefined `[1]` citation
    # used ten times in a doc reports once) while preserving order.
    seen: set[tuple[str, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.rule, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    unique.sort(key=lambda f: (f.path, f.rule, f.message))
    return unique, external_total


def _load_doc(root: Path, rel_path: str) -> DocFile | None:
    try:
        text = (root / rel_path).read_text(encoding="utf-8")
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
    docs_dir = root / "docs"
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
        if path.endswith(".md") and path.startswith("docs/")
    }
    return sorted(paths)


def _format_text(
    findings: Sequence[Finding], *, scope: str, external_count: int
) -> str:
    header = (
        "MM-3966 bounded local documentation link check "
        f"(scope={scope}, advisory-only — does not block CI)."
    )
    suffix = f"External URLs observed (not fetched): {external_count}."
    if not findings:
        return f"{header}\nNo advisory findings. {suffix}"
    lines = [header, f"{len(findings)} advisory finding(s):"]
    for finding in findings:
        line = (
            f"  [{finding.severity}] {finding.path}: {finding.rule}: "
            f"{finding.message}"
        )
        if finding.detail:
            line += f" ({finding.detail})"
        lines.append(line)
    lines.append(suffix)
    return "\n".join(lines)


def _format_json(
    findings: Sequence[Finding], *, scope: str, external_count: int
) -> str:
    payload = {
        "tool": "check_documentation_links",
        "issue": "MoonLadderStudios/MoonMind#3966",
        "scope": scope,
        "advisory_only": True,
        "finding_count": len(findings),
        "external_targets_observed_not_fetched": external_count,
        "excluded_frozen_evidence": sorted(FROZEN_EVIDENCE_PATHS)
        + [d + "/" for d in FROZEN_EVIDENCE_DIRS],
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
            "'all': scan the canonical in-scope docs/ tree."
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
            "Exit non-zero when advisory findings exist. Reserved for a future "
            "promotion to a blocking gate; CI MUST NOT use this."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Explicit doc paths to check (overrides --scope).",
    )
    args = parser.parse_args(argv)

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
    findings, external_count = run_checks(docs, focus_paths=focus_paths)

    if args.format == "json":
        print(_format_json(findings, scope=scope, external_count=external_count))
    else:
        print(_format_text(findings, scope=scope, external_count=external_count))

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
