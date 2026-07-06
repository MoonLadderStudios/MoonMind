#!/usr/bin/env python3
"""Audit MoonMind status-domain tokens.

MM-1084: keep workflow, step, and projection statuses tied to their canonical
domain documents instead of recreating a generic global status vocabulary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_STATUS_POINTER = Path("docs/Workflows/WorkflowStatus.md")

WORKFLOW_STATE_DOC = Path("docs/Temporal/VisibilityAndUiQueryModel.md")
STEP_STATUS_DOC = Path("docs/Temporal/StepLedgerAndProgressModel.md")

STATUS_DOMAIN_AUDIT_TRACEABILITY = {
    "jiraIssue": "MM-1084",
    "sourceIssue": "MM-1073",
    "sourceDocument": WORKFLOW_STATUS_POINTER.as_posix(),
    "canonicalClaimIds": ["docs/Workflows/WorkflowStatus.md#archived-pointer-004"],
    "requirements": [
        "DESIGN-REQ-008",
        "DESIGN-REQ-010",
        "DESIGN-REQ-011",
        "DESIGN-REQ-012",
    ],
}

DOMAIN_PATH_PATTERNS = (
    "api_service/core/",
    "api_service/api/routers/executions.py",
    "api_service/db/models.py",
    "moonmind/schemas/temporal_models.py",
    "moonmind/workflows/temporal/",
    "frontend/src/utils/executionStatusPillClasses.ts",
    "frontend/src/generated/openapi.ts",
)

ARCHIVED_POINTER_REFERENCE_ALLOWED_PATHS = frozenset(
    {
        WORKFLOW_STATUS_POINTER.as_posix(),
        "tools/status_domain_audit.py",
    }
)

WORKFLOW_ROLLUP_STATUSES = frozenset(
    {
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    }
)
TEMPORAL_CLOSE_STATUSES = frozenset(
    {"completed", "failed", "canceled", "terminated", "timed_out", "continued_as_new"}
)
LEGACY_NO_COMMIT_TOKENS = frozenset({"no_changes"})

PROVIDER_STATUS_NORMALIZER_PATHS = (
    "moonmind/jules/",
    "moonmind/workflows/adapters/",
    "tests/unit/jules/",
    "tests/unit/workflows/adapters/",
)

ALLOWED_STATUS_TOKEN_LOCATIONS = (
    {
        "domain": "historical_migration",
        "action": "allow_existing_schema_history",
        "reason": (
            "Migrations preserve historical database enum values and are not "
            "active implementation authority."
        ),
        "path_prefixes": ("api_service/migrations/",),
    },
    {
        "domain": "generated_api_fixture",
        "action": "allow_generated_contract_snapshot",
        "reason": (
            "Generated OpenAPI output mirrors backend schemas; source contracts "
            "are audited separately."
        ),
        "path_prefixes": ("frontend/src/generated/",),
    },
    {
        "domain": "provider_normalizer",
        "action": "allow_provider_raw_statuses_at_adapter_boundary",
        "reason": "Provider statuses are valid inside provider and integration normalizers.",
        "path_prefixes": PROVIDER_STATUS_NORMALIZER_PATHS,
    },
    {
        "domain": "test_fixture",
        "action": "allow_test_fixture_status_literals",
        "reason": (
            "Tests may contain raw tokens to prove normalization, rejection, "
            "and compatibility behavior."
        ),
        "path_prefixes": (
            "tests/",
            "frontend/src/**/*.test.ts",
            "frontend/src/**/*.test.tsx",
        ),
    },
)

STATUS_CONTEXT_RE = re.compile(
    r"""
    (?<![.\w])
    (?:
        (?P<keyquote>["'`])
        (?P<quoted_key>
            mm_state
            |currentTargetState
            |current_target_state
            |dashboardStatus
            |dashboard_status
            |temporalStatus
            |temporal_status
            |closeStatus
            |close_status
            |rawState
            |raw_state
            |workflowStatus
            |workflow_status
            |stepStatus
            |step_status
            |state
            |status
        )
        (?P=keyquote)
        |
        (?P<unquoted_key>
            mm_state
            |currentTargetState
            |current_target_state
            |dashboardStatus
            |dashboard_status
            |temporalStatus
            |temporal_status
            |closeStatus
            |close_status
            |rawState
            |raw_state
            |workflowStatus
            |workflow_status
            |stepStatus
            |step_status
            |state
            |status
        )
    )
    \s*(?P<operator>:|=|={2,3}|!==?|\|)\s*
    (?P<quote>["'`])(?P<token>[a-z][a-z0-9_-]*)(?P=quote)
    """,
    re.VERBOSE,
)
STATUS_CASE_RE = re.compile(
    r"""\bcase\s+(?P<quote>["'`])(?P<token>[a-z][a-z0-9_-]*)(?P=quote)""",
    re.VERBOSE,
)

STRICT_STATUS_KEYS = frozenset(
    {
        "mm_state",
        "currentTargetState",
        "current_target_state",
        "dashboardStatus",
        "dashboard_status",
        "temporalStatus",
        "temporal_status",
        "closeStatus",
        "close_status",
        "rawState",
        "raw_state",
        "workflowStatus",
        "workflow_status",
        "stepStatus",
        "step_status",
    }
)
GENERIC_STATUS_SCHEMA_FILES = frozenset(
    {
        "api_service/db/models.py",
        "moonmind/schemas/temporal_models.py",
        "frontend/src/utils/executionStatusPillClasses.ts",
        "frontend/src/generated/openapi.ts",
    }
)

GENERIC_GLOBAL_VOCABULARY_RE = re.compile(
    r"\b(?:GLOBAL|GENERIC|COMMON|UNIFIED)_STATUS(?:_VOCABULARY|_TOKENS|_VALUES|ES)?\b"
)
STATUS_DOMAIN_LINE_RE = re.compile(
    (
        r"\b(?:workflow|step|dashboard|temporal|close|raw|current_target)_status"
        r"|mm_state|currentTargetState|dashboardStatus|temporalStatus|closeStatus|rawState\b"
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditFinding:
    path: Path
    line_number: int
    code: str
    message: str
    token: str | None = None

    def render(self) -> str:
        location = f"{self.path.as_posix()}:{self.line_number}"
        token = f" token={self.token!r}" if self.token else ""
        return f"{location}: {self.code}: {self.message}{token}"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _relative(path: Path, root: Path = REPO_ROOT) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return path


def _extract_text_block_after_heading(text: str, heading: str) -> set[str]:
    try:
        marker = text.index(heading)
    except ValueError as exc:
        raise RuntimeError(f"Could not find heading {heading!r} in document") from exc
    try:
        block_start = text.index("```text", marker)
    except ValueError as exc:
        raise RuntimeError(
            f"Could not find code block start '```text' after heading {heading!r}"
        ) from exc
    try:
        value_start = text.index("\n", block_start) + 1
        value_end = text.index("```", value_start)
    except ValueError as exc:
        raise RuntimeError(
            f"Could not find code block end '```' after heading {heading!r}"
        ) from exc
    return {line.strip() for line in text[value_start:value_end].splitlines() if line.strip()}


def canonical_workflow_states(root: Path = REPO_ROOT) -> set[str]:
    text = _read_text(root / WORKFLOW_STATE_DOC)
    return _extract_text_block_after_heading(text, "### 5.1 `mm_state` value set")


def canonical_step_statuses(root: Path = REPO_ROOT) -> set[str]:
    text = _read_text(root / STEP_STATUS_DOC)
    status_table = re.search(
        r"The canonical v1 step statuses are:\n\n(?P<table>\| Status \| Meaning \|.*?)(?:\n\n|$)",
        text,
        flags=re.DOTALL,
    )
    if not status_table:
        raise RuntimeError("Could not locate canonical step status table")
    statuses: set[str] = set()
    for match in re.finditer(r"\| `(?P<status>[a-z][a-z0-9_-]*)` \|", status_table.group("table")):
        statuses.add(match.group("status"))
    if not statuses:
        raise RuntimeError("Could not parse canonical step statuses")
    return statuses


def approved_status_tokens(root: Path = REPO_ROOT) -> set[str]:
    return (
        canonical_workflow_states(root)
        | canonical_step_statuses(root)
        | WORKFLOW_ROLLUP_STATUSES
        | TEMPORAL_CLOSE_STATUSES
        | LEGACY_NO_COMMIT_TOKENS
    )


def _is_domain_path(path: Path) -> bool:
    rel = path.as_posix()
    return any(
        rel == pattern.rstrip("/") or rel.startswith(pattern)
        for pattern in DOMAIN_PATH_PATTERNS
    )


def _is_allowlisted_path(path: Path) -> bool:
    rel = path.as_posix()
    for entry in ALLOWED_STATUS_TOKEN_LOCATIONS:
        for prefix in entry["path_prefixes"]:
            if "*" in prefix:
                if fnmatch.fnmatch(rel, prefix):
                    return True
            elif rel.startswith(prefix):
                return True
    return False


def _should_audit_status_match(
    *,
    path: Path,
    key: str,
    key_is_quoted: bool,
    operator: str,
    line: str,
    require_domain_path: bool,
) -> bool:
    if not require_domain_path:
        return True
    is_comparison = operator in {"==", "===", "!=", "!=="}
    if not key_is_quoted and not is_comparison:
        return False
    if key in STRICT_STATUS_KEYS:
        return True
    if path.as_posix() in GENERIC_STATUS_SCHEMA_FILES:
        if key == "status":
            return bool(STATUS_DOMAIN_LINE_RE.search(line))
        return True
    if key == "status" and _is_domain_path(path):
        return bool(STATUS_DOMAIN_LINE_RE.search(line))
    if key == "state" and is_comparison and _is_domain_path(path):
        return True
    return False


def _iter_candidate_files(root: Path) -> Iterable[Path]:
    ignored_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        "artifacts",
        "artifacts-root-owned-context",
        "var",
    }
    suffixes = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".md"}
    for dirpath, dirs, filenames in os.walk(root):
        dirs[:] = [directory for directory in dirs if directory not in ignored_dirs]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix in suffixes:
                yield path


def audit_text_for_status_tokens(
    text: str,
    *,
    path: Path,
    allowed_tokens: set[str],
    require_domain_path: bool = True,
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    rel_path = path
    if _is_allowlisted_path(rel_path):
        return []
    is_domain_path = _is_domain_path(rel_path)
    for line_number, line in enumerate(text.splitlines(), start=1):
        if GENERIC_GLOBAL_VOCABULARY_RE.search(line):
            findings.append(
                AuditFinding(
                    rel_path,
                    line_number,
                    "generic-global-status-vocabulary",
                    "generic status vocabularies are forbidden; keep values domain-specific",
                )
            )
        if require_domain_path and not is_domain_path:
            continue
        for match in STATUS_CONTEXT_RE.finditer(line):
            key = match.group("quoted_key") or match.group("unquoted_key")
            if not _should_audit_status_match(
                path=rel_path,
                key=key,
                key_is_quoted=bool(match.group("quoted_key")),
                operator=match.group("operator"),
                line=line,
                require_domain_path=require_domain_path,
            ):
                continue
            token = match.group("token")
            if key == "status" and token.startswith("mm_"):
                continue
            if token not in allowed_tokens:
                findings.append(
                    AuditFinding(
                        rel_path,
                        line_number,
                        "unknown-status-token",
                        (
                            "raw status token is not in an approved workflow, "
                            "step, rollup, close, provider, fixture, or "
                            "historical domain"
                        ),
                        token,
                    )
                )
        for match in STATUS_CASE_RE.finditer(line):
            token = match.group("token")
            if token not in allowed_tokens:
                findings.append(
                    AuditFinding(
                        rel_path,
                        line_number,
                        "unknown-status-token",
                        (
                            "raw status token is not in an approved workflow, "
                            "step, rollup, close, provider, fixture, or "
                            "historical domain"
                        ),
                        token,
                    )
                )
    return findings


def audit_archived_workflow_status_refs(root: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    needle = WORKFLOW_STATUS_POINTER.as_posix()
    for path in _iter_candidate_files(root):
        rel = _relative(path, root)
        if rel.as_posix() in ARCHIVED_POINTER_REFERENCE_ALLOWED_PATHS:
            continue
        if rel.as_posix().startswith("tests/"):
            continue
        text = _read_text(path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                findings.append(
                    AuditFinding(
                        rel,
                        line_number,
                        "archived-workflow-status-authority",
                        (
                            "archived WorkflowStatus.md may not be cited as "
                            "active implementation authority"
                        ),
                    )
                )
    return findings


def audit_repository(root: Path = REPO_ROOT) -> list[AuditFinding]:
    allowed_tokens = approved_status_tokens(root)
    findings: list[AuditFinding] = []
    for path in _iter_candidate_files(root):
        rel = _relative(path, root)
        findings.extend(
            audit_text_for_status_tokens(
                _read_text(path),
                path=rel,
                allowed_tokens=allowed_tokens,
                require_domain_path=True,
            )
        )
    findings.extend(audit_archived_workflow_status_refs(root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    findings = audit_repository(args.repo_root)
    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1
    print("status domain audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
