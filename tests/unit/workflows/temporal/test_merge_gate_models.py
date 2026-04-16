from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    MergeGatePolicyModel,
    MergeGateStartInput,
    PullRequestRefModel,
    ReadinessEvidenceModel,
)


def _valid_pull_request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo": "MoonLadderStudios/MoonMind",
        "number": 341,
        "url": "https://github.com/MoonLadderStudios/MoonMind/pull/341",
        "headSha": "abc123",
    }
    payload.update(overrides)
    return payload


def test_pull_request_ref_requires_compact_identity() -> None:
    with pytest.raises(ValidationError):
        PullRequestRefModel(repo="MoonLadderStudios/MoonMind", number=341, url="")


def test_merge_gate_start_rejects_unsupported_policy_value() -> None:
    with pytest.raises(ValidationError):
        MergeGateStartInput(
            workflowType="MoonMind.MergeGate",
            parent={"workflowId": "mm:parent"},
            pullRequest=_valid_pull_request(),
            policy={"checks": "sometimes"},
        )


def test_readiness_evidence_rejects_ready_with_blockers() -> None:
    with pytest.raises(ValidationError):
        ReadinessEvidenceModel(
            headSha="abc123",
            ready=True,
            blockers=[
                {
                    "kind": "checks_running",
                    "summary": "Checks still running",
                    "retryable": True,
                }
            ],
        )


def test_policy_defaults_to_required_checks_and_reviews() -> None:
    policy = MergeGatePolicyModel()

    assert policy.checks == "required"
    assert policy.automated_review == "required"
    assert policy.jira_status == "optional"
    assert policy.merge_method == "squash"

