from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.check_documentation_architecture import metadata_fields

REPO_ROOT = Path(__file__).resolve().parents[3]

STEP_DOC = REPO_ROOT / "docs" / "Steps" / "StepExecutionsAndCheckpointing.md"
LEDGER_DOC = REPO_ROOT / "docs" / "Temporal" / "StepLedgerAndProgressModel.md"
ACTIVITY_DOC = REPO_ROOT / "docs" / "Temporal" / "ActivityCatalogAndWorkerTopology.md"
RUN_HISTORY_DOC = (
    REPO_ROOT / "docs" / "Temporal" / "WorkflowRunHistoryAndNewRunSemantics.md"
)
ROADMAP_DOC = REPO_ROOT / "docs" / "MoonMindRoadmap.md"
TEMP_PLAN = REPO_ROOT / "docs" / "tmp" / "StepExecutionsCheckpointingGapPlan.md"
CONDITIONAL_DOCS = (
    REPO_ROOT / "docs" / "ManagedAgents" / "DockerBackendService.md",
    REPO_ROOT / "docs" / "Security" / "SecretsSystem.md",
)


CANONICAL_DOCS = (
    STEP_DOC,
    LEDGER_DOC,
    ACTIVITY_DOC,
    RUN_HISTORY_DOC,
)

SECRET_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"ghp_[A-Za-z0-9_]+",
        r"github_pat_[A-Za-z0-9_]+",
        r"AIza[A-Za-z0-9_\-]+",
        r"ATATT[A-Za-z0-9_\-]+",
        r"AKIA[A-Z0-9]+",
        r"token\s*=",
        r"password\s*=",
        r"BEGIN .*PRIVATE KEY",
    )
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_docs_are_declarative_not_migration_tracking_surfaces() -> None:
    for path in CANONICAL_DOCS:
        text = _read(path)
        assert "Implementation tracking:" not in text, path
        assert "migration checklists in canonical `docs/`" not in text, path
        metadata = metadata_fields(text)
        assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", metadata["last updated"]), path

    assert metadata_fields(_read(RUN_HISTORY_DOC))["status"] != "Draft"
    assert metadata_fields(_read(LEDGER_DOC))["status"] == "Normative"


def test_temp_plan_cleanup_guard_removes_plan_after_final_dod_closes() -> None:
    assert not TEMP_PLAN.exists()

    canonical_text = "\n".join(_read(path) for path in CANONICAL_DOCS)
    assert "Status: Execution plan (disposable; not canonical)" not in canonical_text
    assert "Final definition of done" not in canonical_text


def test_docs_do_not_inline_secrets_or_raw_evidence() -> None:
    from moonmind.omnigent.conformance import SECRET_PATTERN as SHARED_SECRET_PATTERN

    checked_paths = [*CANONICAL_DOCS, ROADMAP_DOC]
    for path in checked_paths:
        text = _read(path)
        for pattern in SECRET_PATTERNS:
            assert pattern.search(text) is None, path
        # Production shared-scanner twin (#3964): the canonical credential
        # guardrail must agree, so deleting this doc assertion cannot hide a
        # leak from the owning redaction boundary.
        assert SHARED_SECRET_PATTERN.search(text) is None, path
        assert "BEGIN_PROVIDER_PAYLOAD" not in text, path
        assert "```diff" not in text, path


def test_conditional_docs_remain_present_after_temp_plan_cleanup() -> None:
    assert not TEMP_PLAN.exists()
    for path in CONDITIONAL_DOCS:
        assert path.exists()
