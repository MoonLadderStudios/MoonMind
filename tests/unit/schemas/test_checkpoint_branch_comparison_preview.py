"""Unit coverage for MoonLadderStudios/MoonMind#2215 comparison-preview hardening.

Covers Codex review findings on PR #4551: positive finite preview budgets,
distinct resolved branches, non-blank shared source intent, mutually exclusive
reuse/new-run candidate modes, and the single supported comparison rubric.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchComparisonPreviewRequest,
)


def _request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "objective": "Compare two candidates.",
        "rubricId": "checkpoint-branch-gates",
        "allowNewRuns": True,
        "candidates": [
            {
                "candidateId": "left",
                "sourceIntentRef": "artifact://intent/shared",
                "maxBudgetUsd": 2.0,
            },
            {
                "candidateId": "right",
                "sourceIntentRef": "artifact://intent/shared",
                "maxBudgetUsd": 3.0,
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_preview_rejects_zero_budget_for_requested_runs() -> None:
    """Zero budgets must fail like ordinary admission (budget_exhausted)."""

    payload = _request()
    candidates = list(payload["candidates"])  # type: ignore[union-attr]
    candidates[0] = {
        "candidateId": "left",
        "sourceIntentRef": "artifact://intent/shared",
        "maxBudgetUsd": 0,
    }
    payload["candidates"] = candidates
    with pytest.raises(ValidationError):
        CheckpointBranchComparisonPreviewRequest.model_validate(payload)


def test_preview_rejects_non_finite_budget_for_requested_runs() -> None:
    """Overflowing budgets must not produce an unserializable preview."""

    payload = _request()
    candidates = list(payload["candidates"])  # type: ignore[union-attr]
    candidates[0] = {
        "candidateId": "left",
        "sourceIntentRef": "artifact://intent/shared",
        "maxBudgetUsd": float("inf"),
    }
    payload["candidates"] = candidates
    with pytest.raises(ValidationError):
        CheckpointBranchComparisonPreviewRequest.model_validate(payload)


def test_preview_rejects_duplicate_resolved_branches() -> None:
    """Two labels over one saved branch are not a two-candidate comparison."""

    with pytest.raises(ValidationError, match="branchId"):
        CheckpointBranchComparisonPreviewRequest.model_validate(
            _request(
                allowNewRuns=False,
                candidates=[
                    {"candidateId": "left", "branchId": "branch-same"},
                    {"candidateId": "right", "branchId": "branch-same"},
                ],
            )
        )


def test_preview_rejects_whitespace_only_source_intent() -> None:
    """Blank shared intent must not authorize requested runs."""

    with pytest.raises(ValidationError, match="sourceIntentRef"):
        CheckpointBranchComparisonPreviewRequest.model_validate(
            _request(
                candidates=[
                    {
                        "candidateId": "left",
                        "sourceIntentRef": "   ",
                        "maxBudgetUsd": 2.0,
                    },
                    {
                        "candidateId": "right",
                        "sourceIntentRef": "   ",
                        "maxBudgetUsd": 2.0,
                    },
                ]
            )
        )


def test_preview_rejects_candidates_mixing_reuse_and_new_run_intent() -> None:
    """A candidate naming a branch must not carry new-run settings."""

    with pytest.raises(ValidationError, match="branchId"):
        CheckpointBranchComparisonPreviewRequest.model_validate(
            _request(
                candidates=[
                    {
                        "candidateId": "left",
                        "branchId": "branch-left",
                        "sourceIntentRef": "artifact://intent/shared",
                        "maxBudgetUsd": 2.0,
                    },
                    {"candidateId": "right", "branchId": "branch-right"},
                ]
            )
        )


def test_preview_rejects_unsupported_rubric_id() -> None:
    """Only the applied gate rubric may be claimed as provenance."""

    with pytest.raises(ValidationError, match="rubricId"):
        CheckpointBranchComparisonPreviewRequest.model_validate(
            _request(rubricId="custom-quality-rubric")
        )
