from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ARCHIVED_STATUS_DOC = "docs/Workflows/WorkflowStatus.md"
ALLOWED_ARCHIVAL_POINTERS = {
    Path("docs/Workflows/WorkflowStatus.md"),
    Path("docs/Temporal/StatusDomainMatrix.md"),
    Path("tests/unit/docs/test_status_authority_references.py"),
    Path("tests/unit/tools/test_status_domain_audit.py"),
}
SCAN_ROOTS = (
    "api_service",
    "docs",
    "frontend/src",
    "moonmind",
    "tests",
)
TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


def test_archived_workflow_status_doc_is_not_cited_as_active_authority() -> None:
    offenders: list[str] = []
    for scan_root in SCAN_ROOTS:
        base = REPO_ROOT / scan_root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(REPO_ROOT)
            if relative in ALLOWED_ARCHIVAL_POINTERS:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if ARCHIVED_STATUS_DOC in text or "WorkflowStatus.md" in text:
                offenders.append(relative.as_posix())

    assert offenders == []
