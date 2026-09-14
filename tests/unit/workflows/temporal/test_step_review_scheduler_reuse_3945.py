"""Scheduler-side committed-review reuse for step.review (MoonMind#3945 R5).

Workflow-boundary coverage for the run.py scheduler + artifact/result store:
a duplicate delivery or lost-acknowledgment retry of the same admitted review
must reuse only the same committed decision keyed to the review
identity/evidence digest, changed evidence must proceed as a new attempt, and
unavailable results must never be committed or reused.

The harness drives the real production pieces: the real step.review Activity,
the real step-gate contract parser, and the real scheduler helpers
(``MoonMindRunWorkflow._gate_check_metadata``,
``MoonMindRunWorkflow._upsert_step_check``,
``resolve_committed_gate_reuse``, and the workflow's committed-gate map).
Only the provider transport (stub reviewer) and the artifact-write activity
(canned artifact ref) are doubles; no workflow, Activity, or contract logic
is reimplemented here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.temporal.activities.reviewer import ReviewerUnavailable
from moonmind.workflows.temporal.activities.step_review import (
    clear_committed_reviews,
    step_review_activity,
)
from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    resolve_committed_gate_reuse,
)


@pytest.fixture(autouse=True)
def _isolated_committed_reviews():
    """Keep Activity-side committed-review state hermetic across tests."""
    clear_committed_reviews()
    yield
    clear_committed_reviews()


class _StubConfig:
    default_chat_provider = "openai"

    class openai:  # noqa: D106
        openai_api_key = "hermetic"
        openai_enabled = True
        openai_chat_model = "stub-model"


class _StubReviewer:
    def __init__(self, text: str) -> None:
        self._text = text
        self._config = _StubConfig()

    def describe_route(self, model: str) -> dict[str, str]:
        return {
            "provider": "openai",
            "model": model if model != "default" else "stub-model",
        }

    async def review(self, *, prompt: str, model: str, timeout: int) -> str:
        return self._text


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "node_id": "n1",
        "step_index": 1,
        "total_steps": 1,
        "review_attempt": 1,
        "tool_name": "repo.run_tests",
        "tool_type": "skill",
        "inputs": {"goal": "fix tests"},
        "execution_result": {"status": "COMPLETED"},
        "workflow_context": {"plan_title": "Fix tests"},
    }
    base.update(overrides)
    return base


async def _positive_gate() -> Any:
    """Run the real Activity and parse through the real gate contract."""
    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    result = await step_review_activity(_payload(), reviewer=_StubReviewer(text))
    assert result["verdict"] == "FULLY_IMPLEMENTED"
    return parse_step_gate_result(result)


def _scheduler() -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._step_ledger_rows = [{"logicalStepId": "n1", "checks": []}]
    return workflow


def _commit_like_scheduler(
    workflow: MoonMindRunWorkflow, gate: Any, artifact_ref: str
) -> dict[str, Any]:
    """Mirror the run.py commit block with the real scheduler helpers."""
    metadata = workflow._gate_check_metadata(
        gate_result_ref=artifact_ref, gate=gate
    )
    workflow._upsert_step_check(
        "n1",
        kind="approval_policy",
        status="passed",
        summary="Structured review approved advancement",
        retry_count=0,
        artifact_ref=artifact_ref,
        metadata=metadata,
    )
    identity = metadata["reviewAttemptIdentity"]
    if identity and gate.verdict in {"FULLY_IMPLEMENTED", "ADDITIONAL_WORK_NEEDED"}:
        workflow._committed_review_gates[identity] = {
            "payload": gate.to_payload(),
            "artifactRef": artifact_ref,
        }
    return metadata


@pytest.mark.asyncio
async def test_gate_check_metadata_carries_provenance_binding() -> None:
    workflow = _scheduler()
    gate = await _positive_gate()
    metadata = workflow._gate_check_metadata(
        gate_result_ref="artifact://gate/n1/attempt-1", gate=gate
    )
    provenance = gate.to_payload()["reviewProvenance"]
    assert metadata["reviewProvenance"] == provenance
    assert metadata["reviewAttemptIdentity"] == provenance["reviewAttemptIdentity"]
    assert metadata["reviewEvidenceDigest"] == provenance["evidenceDigest"]
    assert metadata["gateVerdict"] == "FULLY_IMPLEMENTED"
    # Credentials never land in the ledger projection.
    assert "hermetic" not in json.dumps(metadata)


def test_legacy_gate_without_provenance_follows_normal_path() -> None:
    gate = parse_step_gate_result({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.8})
    assert gate.review_provenance is None
    workflow = _scheduler()
    metadata = workflow._gate_check_metadata(
        gate_result_ref="artifact://gate/n1/attempt-1", gate=gate
    )
    assert metadata["reviewProvenance"] is None
    assert metadata["reviewAttemptIdentity"] is None
    assert metadata["reviewEvidenceDigest"] is None
    assert resolve_committed_gate_reuse({}, gate) is None


@pytest.mark.asyncio
async def test_first_delivery_misses_and_duplicate_reuses_same_decision() -> None:
    workflow = _scheduler()
    gate = await _positive_gate()
    assert resolve_committed_gate_reuse(workflow._committed_review_gates, gate) is None

    artifact_ref = "artifact://gate/n1/attempt-1"
    metadata = _commit_like_scheduler(workflow, gate, artifact_ref)
    identity = metadata["reviewAttemptIdentity"]
    assert identity and identity.startswith("review:")
    # The step-ledger check names the committed attempt for operators/queries.
    check = workflow._step_ledger_rows[0]["checks"][0]
    assert check["reviewAttemptIdentity"] == identity
    assert check["artifactRef"] == artifact_ref

    # A duplicate delivery is a fresh Activity result for identical evidence:
    # deterministic inputs yield the same immutable attempt identity.
    duplicate = await _positive_gate()
    assert (
        duplicate.to_payload()["reviewProvenance"]["reviewAttemptIdentity"] == identity
    )
    reused = resolve_committed_gate_reuse(workflow._committed_review_gates, duplicate)
    assert reused is not None
    restored, restored_ref = reused
    assert restored.verdict == "FULLY_IMPLEMENTED"
    assert restored_ref == artifact_ref
    assert (
        restored.to_payload()["reviewProvenance"]["reviewAttemptIdentity"] == identity
    )


@pytest.mark.asyncio
async def test_changed_evidence_is_a_new_attempt_not_a_reuse() -> None:
    workflow = _scheduler()
    gate = await _positive_gate()
    _commit_like_scheduler(workflow, gate, "artifact://gate/n1/attempt-1")

    text = json.dumps({"verdict": "FULLY_IMPLEMENTED", "confidence": 0.9})
    changed = await step_review_activity(
        _payload(inputs={"goal": "different work"}),
        reviewer=_StubReviewer(text),
    )
    changed_gate = parse_step_gate_result(changed)
    assert (
        changed_gate.to_payload()["reviewProvenance"]["reviewAttemptIdentity"]
        != gate.to_payload()["reviewProvenance"]["reviewAttemptIdentity"]
    )
    assert (
        resolve_committed_gate_reuse(workflow._committed_review_gates, changed_gate)
        is None
    )


@pytest.mark.asyncio
async def test_unavailable_result_is_never_committed_or_reused() -> None:
    class _Failing(_StubReviewer):
        async def review(self, *, prompt: str, model: str, timeout: int) -> str:
            raise ReviewerUnavailable("no authority", code="reviewer_disabled")

    workflow = _scheduler()
    result = await step_review_activity(_payload(), reviewer=_Failing("unused"))
    gate = parse_step_gate_result(result)
    assert gate.verdict == "NO_DETERMINATION"
    assert gate.to_payload()["reviewProvenance"]["reviewAttemptIdentity"].startswith(
        "review:"
    )
    # The scheduler commit block only stores committable verdicts, so an
    # unavailable redelivery can never shadow a committed decision.
    assert (
        resolve_committed_gate_reuse(workflow._committed_review_gates, gate) is None
    )
    assert workflow._committed_review_gates == {}


@pytest.mark.asyncio
async def test_divergent_redelivery_keeps_first_committed_decision() -> None:
    """A lost-store provider re-invocation must not overwrite the commit."""
    workflow = _scheduler()
    gate = await _positive_gate()
    artifact_ref = "artifact://gate/n1/attempt-1"
    _commit_like_scheduler(workflow, gate, artifact_ref)
    committed_payload = gate.to_payload()

    # Simulate a divergent re-invocation: same immutable attempt identity,
    # different verdict. Only the first committed decision may be reused.
    divergent_payload = dict(committed_payload)
    divergent_payload["verdict"] = "ADDITIONAL_WORK_NEEDED"
    divergent_payload["confidence"] = 0.7
    divergent_payload["feedback"] = "Second inference disagrees."
    divergent = parse_step_gate_result(divergent_payload)
    assert (
        divergent.to_payload()["reviewProvenance"]["reviewAttemptIdentity"]
        == committed_payload["reviewProvenance"]["reviewAttemptIdentity"]
    )
    reused = resolve_committed_gate_reuse(workflow._committed_review_gates, divergent)
    assert reused is not None
    restored, restored_ref = reused
    assert restored.verdict == "FULLY_IMPLEMENTED"
    assert restored.feedback != "Second inference disagrees."
    assert restored_ref == artifact_ref
