from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    MergeAutomationConfigModel,
    MergeAutomationStartInput,
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


def test_merge_automation_start_rejects_unsupported_policy_value() -> None:
    with pytest.raises(ValidationError):
        MergeAutomationStartInput(
            workflowType="MoonMind.MergeAutomation",
            parentWorkflowId="mm:parent",
            publishContextRef="artifact://publish-context",
            pullRequest=_valid_pull_request(),
            mergeAutomationConfig={"gate": {"github": {"checks": "sometimes"}}},
            resolverTemplate={"repository": "MoonLadderStudios/MoonMind"},
        )


def test_merge_automation_start_requires_publish_context_ref() -> None:
    with pytest.raises(ValidationError):
        MergeAutomationStartInput(
            workflowType="MoonMind.MergeAutomation",
            parentWorkflowId="mm:parent",
            publishContextRef="",
            pullRequest=_valid_pull_request(),
            resolverTemplate={"repository": "MoonLadderStudios/MoonMind"},
        )


def test_merge_automation_start_normalizes_fallback_poll_seconds() -> None:
    payload = MergeAutomationStartInput(
        workflowType="MoonMind.MergeAutomation",
        parentWorkflowId="mm:parent",
        publishContextRef="artifact://publish-context",
        pullRequest=_valid_pull_request(),
        mergeAutomationConfig={"timeouts": {"fallbackPollSeconds": -1}},
        resolverTemplate={"repository": "MoonLadderStudios/MoonMind"},
    )

    assert payload.config.timeouts.fallback_poll_seconds == 120


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
    policy = MergeAutomationConfigModel().gate.github

    assert policy.checks == "required"
    assert policy.automated_review == "required"
    assert MergeAutomationConfigModel().gate.jira.status == "optional"
    assert MergeAutomationConfigModel().resolver.merge_method == "squash"
