from __future__ import annotations

import logging

import pytest

from api_service.core.sync import _coerce_mm_state
from api_service.db.models import MoonMindWorkflowState
from moonmind.schemas.temporal_activity_models import ExecutionTerminalStateInput
from moonmind.workflows.automation import models as automation_models
from moonmind.workflows.automation.repositories import _coerce_run_status
from moonmind.workflows.no_commit_compatibility import (
    LEGACY_FINISH_OUTCOME_ALIASES,
    LEGACY_WORKFLOW_STATE_ALIASES,
    canonicalize_legacy_finish_outcome_code,
    canonicalize_legacy_workflow_state,
    normalize_no_commit_finish_summary_aliases,
    parse_canonical_workflow_state,
)


def test_legacy_alias_maps_are_explicit() -> None:
    assert LEGACY_WORKFLOW_STATE_ALIASES == {"no_changes": "no_commit"}
    assert LEGACY_FINISH_OUTCOME_ALIASES == {"NO_CHANGES": "NO_COMMIT"}


def test_workflow_state_alias_observation_is_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.no_commit_compatibility")

    with caplog.at_level(logging.INFO, logger=logger.name):
        result = canonicalize_legacy_workflow_state(
            "no_changes",
            domain="temporal_search_attribute.mm_state",
            logger=logger,
        )

    assert result == "no_commit"
    assert (
        "legacy_alias_observed domain=temporal_search_attribute.mm_state "
        "alias=no_changes canonical=no_commit"
    ) in caplog.text
    assert "secret" not in caplog.text.lower()


def test_finish_summary_alias_boundary_canonicalizes_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.no_commit_finish_summary")

    with caplog.at_level(logging.INFO, logger=logger.name):
        normalized = normalize_no_commit_finish_summary_aliases(
            {
                "finishOutcome": {
                    "code": "NO_CHANGES",
                    "reason": "publish skipped: no local changes",
                },
                "publish": {
                    "reasonCode": "no_changes",
                    "reason": "no local changes",
                },
            },
            domain="unit.finishSummary",
            logger=logger,
        )

    assert normalized is not None
    assert normalized["finishOutcome"]["code"] == "NO_COMMIT"
    assert normalized["finishOutcome"]["reason"] == "No repository commit was needed."
    assert normalized["publish"]["reasonCode"] == "no_commit"
    assert (
        "legacy_alias_observed domain=unit.finishSummary.finishOutcome.code "
        "alias=NO_CHANGES canonical=NO_COMMIT"
    ) in caplog.text
    assert (
        "legacy_alias_observed domain=unit.finishSummary.publish.reasonCode "
        "alias=no_changes canonical=no_commit"
    ) in caplog.text


def test_finish_outcome_code_boundary_preserves_none() -> None:
    logger = logging.getLogger("tests.no_commit_finish_outcome")

    assert (
        canonicalize_legacy_finish_outcome_code(
            None,
            domain="unit.finishOutcome.code",
            logger=logger,
        )
        is None
    )


def test_finish_summary_alias_boundary_updates_snake_case_publish_reason_code() -> None:
    logger = logging.getLogger("tests.no_commit_finish_summary")

    normalized = normalize_no_commit_finish_summary_aliases(
        {
            "publish": {
                "reasonCode": "no_changes",
                "reason_code": "no_changes",
                "reason": "no local changes",
            },
        },
        domain="unit.finishSummary",
        logger=logger,
    )

    assert normalized is not None
    assert normalized["publish"]["reasonCode"] == "no_commit"
    assert normalized["publish"]["reason_code"] == "no_commit"


def test_direct_canonical_workflow_state_parser_rejects_legacy_alias() -> None:
    with pytest.raises(ValueError, match="Legacy workflow state alias"):
        parse_canonical_workflow_state("no_changes")


def test_direct_finish_outcome_values_are_not_semantically_rewritten() -> None:
    logger = logging.getLogger("tests.no_commit_finish_outcome")

    assert (
        canonicalize_legacy_finish_outcome_code(
            "PUBLISHED_PR",
            domain="unit.finishOutcome.code",
            logger=logger,
        )
        == "PUBLISHED_PR"
    )


def test_temporal_search_attribute_boundary_accepts_legacy_state() -> None:
    assert (
        _coerce_mm_state({"mm_state": ["no_changes"]})
        is MoonMindWorkflowState.NO_COMMIT
    )


def test_automation_status_enum_emits_only_canonical_no_commit() -> None:
    assert "no_changes" not in {
        status.value for status in automation_models.AutomationRunStatus
    }
    assert (
        _coerce_run_status("no_changes")
        is automation_models.AutomationRunStatus.NO_COMMIT
    )


def test_automation_status_type_decodes_legacy_persisted_value() -> None:
    status_type = automation_models.AutomationRunStatusType()

    assert (
        status_type.process_result_value("no_changes", None)
        is automation_models.AutomationRunStatus.NO_COMMIT
    )
    assert (
        status_type.process_bind_param("no_changes", None)
        == automation_models.AutomationRunStatus.NO_COMMIT.value
    )


def test_terminal_state_activity_input_accepts_legacy_no_changes_state() -> None:
    model = ExecutionTerminalStateInput.model_validate(
        {"workflowId": "wf-1", "state": "no_changes"}
    )

    assert model.state == "no_changes"
