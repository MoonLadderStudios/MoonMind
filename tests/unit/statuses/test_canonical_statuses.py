"""Regression coverage for MM-1081 canonical backend status ownership."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from moonmind.statuses.close_status import TemporalExecutionCloseStatus
from moonmind.statuses.compat import WORKFLOW_STATE_COMPATIBILITY_ALIASES
from moonmind.statuses.integration import INTEGRATION_STATUS_VALUES
from moonmind.statuses.step_execution import (
    STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS,
    STEP_EXECUTION_ARTIFACT_STATUS_VALUES,
    StepExecutionArtifactStatus,
)
from moonmind.statuses.step_ledger import STEP_LEDGER_STATUS_VALUES, StepLedgerStatus
from moonmind.statuses.temporal_status import TEMPORAL_STATUS_VALUES
from moonmind.statuses.workflow import (
    TERMINAL_WORKFLOW_STATES,
    WORKFLOW_STATE_TO_CLOSE_STATUS,
    WORKFLOW_STATE_VALUES,
    MoonMindWorkflowState,
    coerce_workflow_state,
    workflow_state_to_close_status,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_doc(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _fenced_values_after_heading(markdown: str, heading: str) -> set[str]:
    pattern = rf"### {re.escape(heading)}.*?```text\n(?P<body>.*?)\n```"
    match = re.search(pattern, markdown, flags=re.DOTALL)
    assert match is not None
    return {
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip()
    }


def _status_table_values(markdown: str) -> set[str]:
    section = markdown.split("## 8. Step status vocabulary", maxsplit=1)[1]
    section = section.split("## 9.", maxsplit=1)[0]
    return {
        match.group("status")
        for match in re.finditer(r"^\| `(?P<status>[a-z_]+)` \|", section, re.MULTILINE)
    }


def test_workflow_state_values_match_temporal_visibility_doc() -> None:
    markdown = _read_doc("docs/Temporal/VisibilityAndUiQueryModel.md")

    assert WORKFLOW_STATE_VALUES == _fenced_values_after_heading(
        markdown,
        "5.1 `mm_state` value set",
    )


def test_workflow_close_status_mapping_covers_terminal_states() -> None:
    assert set(WORKFLOW_STATE_TO_CLOSE_STATUS) == set(TERMINAL_WORKFLOW_STATES)
    assert workflow_state_to_close_status(
        MoonMindWorkflowState.NO_COMMIT
    ) is TemporalExecutionCloseStatus.COMPLETED
    assert workflow_state_to_close_status(
        MoonMindWorkflowState.COMPLETED
    ) is TemporalExecutionCloseStatus.COMPLETED
    assert workflow_state_to_close_status(
        MoonMindWorkflowState.FAILED
    ) is TemporalExecutionCloseStatus.FAILED
    assert workflow_state_to_close_status(
        MoonMindWorkflowState.CANCELED
    ) is TemporalExecutionCloseStatus.CANCELED


def test_workflow_state_parsing_fails_fast_outside_compat_boundary() -> None:
    assert coerce_workflow_state("no_changes") is MoonMindWorkflowState.NO_COMMIT
    assert coerce_workflow_state(" No_Changes ") is MoonMindWorkflowState.NO_COMMIT
    assert WORKFLOW_STATE_COMPATIBILITY_ALIASES == {"no_changes": "no_commit"}

    with pytest.raises(ValueError):
        coerce_workflow_state("done")


def test_step_ledger_values_match_step_ledger_doc() -> None:
    markdown = _read_doc("docs/Temporal/StepLedgerAndProgressModel.md")

    assert STEP_LEDGER_STATUS_VALUES == _status_table_values(markdown)


def test_step_execution_artifact_statuses_map_to_ledger_statuses() -> None:
    assert set(STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS) == set(
        StepExecutionArtifactStatus
    )
    assert STEP_EXECUTION_ARTIFACT_STATUS_VALUES == {
        item.value for item in StepExecutionArtifactStatus
    }
    assert {
        status.value
        for status in STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS.values()
    } <= STEP_LEDGER_STATUS_VALUES
    assert (
        STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS[
            StepExecutionArtifactStatus.CHECKING
        ]
        is StepLedgerStatus.REVIEWING
    )
    assert (
        STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS[
            StepExecutionArtifactStatus.COMPLETED
        ]
        is StepLedgerStatus.COMPLETED
    )
    assert (
        STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS[
            StepExecutionArtifactStatus.SUPERSEDED
        ]
        is StepLedgerStatus.SKIPPED
    )


def test_temporal_status_and_integration_status_domains_are_separate() -> None:
    assert TEMPORAL_STATUS_VALUES == {"running", "completed", "failed", "canceled"}
    assert INTEGRATION_STATUS_VALUES == {
        "queued",
        "running",
        "completed",
        "failed",
        "canceled",
        "unknown",
    }


def test_workflow_domain_excludes_provider_aliases() -> None:
    provider_aliases = {
        "done",
        "cancelled",
        "in-progress",
        "processing",
        "awaiting_user_feedback",
    }

    assert WORKFLOW_STATE_VALUES.isdisjoint(provider_aliases)
