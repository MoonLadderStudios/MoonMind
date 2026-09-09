"""Retired vector admission validation (MoonLadderStudios/MoonMind#4105)."""

import pytest

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    reject_retired_vector_fields,
    strip_absent_vector_fields,
)


def test_rejects_explicit_rag() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"]}}, field_path="payload"
        )


def test_rejects_explicit_follow_up_retrieval() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"followUpRetrieval": {"enabled": True, "collections": ["repo"]}},
            field_path="payload",
        )
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"followUpRetrieval": {"collections": ["repo"]}},
            field_path="payload",
        )


def test_allows_absent_empty_disabled() -> None:
    reject_retired_vector_fields({}, field_path="payload")
    reject_retired_vector_fields({"rag": {}}, field_path="payload")
    reject_retired_vector_fields({"rag": None}, field_path="payload")
    reject_retired_vector_fields(
        {"followUpRetrieval": {"enabled": False}}, field_path="payload"
    )
    reject_retired_vector_fields(
        {"followUpRetrieval": {}}, field_path="payload"
    )


def test_strip_absent_vector_fields() -> None:
    params = {
        "instructions": "hi",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    assert strip_absent_vector_fields(params) == {"instructions": "hi"}
