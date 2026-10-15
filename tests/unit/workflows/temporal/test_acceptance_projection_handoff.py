"""The parent rereads complete acceptance before it can decide publication."""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.skills.acceptance_contract import acceptance_evidence
from moonmind.workflows.temporal import agent_result_payloads as payloads
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    source_bound_report,
)  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "unreadable", "bytes", "reference", "subject"]
)
async def test_large_projection_is_read_before_parent_acceptance(
    source_bound_report, monkeypatch, failure
):
    report = source_bound_report
    # Legitimate detailed source map exceeds the child metadata limit.
    plan = report["validatedRefs"]["acceptance"]["verificationMap"][0]
    plan["requiredBehavior"] = "Observe required behavior. " * 1200
    canonical = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    report["validatedRefs"]["acceptance"]["evidence"][0]["obligationDigest"] = (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )
    raw = json.dumps(report).encode()
    assert len(raw) > 16384
    report_ref = "artifact://full-report"
    descriptor = payloads.build_acceptance_projection(
        report,
        report_ref=report_ref,
        report_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    projected = payloads.compact_moonspec_verify_metadata(
        report, acceptance_projection=descriptor
    )
    assert acceptance_evidence(projected) is None
    if failure == "reference":
        descriptor["reportRef"] = "artifact://other"
    elif failure == "subject":
        descriptor["subject"] = {**descriptor["subject"], "revision": "wrong"}
    projected["validatedRefs"]["acceptanceProjection"] = descriptor
    result = {"status": "COMPLETED", "outputs": {"moonSpecVerify": projected}}
    parent = run_module.MoonMindRunWorkflow()
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent._principal = lambda: "workflow:owner"
    parent._execute_kwargs_for_route = lambda _: {}

    async def read(_name, request, **_kwargs):
        assert request.principal == "workflow:owner"
        if failure == "unreadable":
            raise ValueError("Report is unreadable")
        if failure == "reference":
            raise ValueError("Wrong report reference is inaccessible")
        return raw + b" " if failure == "bytes" else raw

    activity = AsyncMock(side_effect=read)
    monkeypatch.setattr(run_module, "execute_typed_activity", activity)
    if failure:
        with pytest.raises(ValueError):
            await parent._hydrate_acceptance_result(result)
    else:
        hydrated = await parent._hydrate_acceptance_result(result)
        assert acceptance_evidence(hydrated["outputs"]["moonSpecVerify"]) is not None
        assert len(json.dumps(hydrated)) > 16384
        assert "acceptance" not in result["outputs"]["moonSpecVerify"]["validatedRefs"]
    activity.assert_awaited_once()


@pytest.mark.parametrize(
    "gap_type,action,eligible",
    [
        ("evidence", "blocked", True),
        ("environment", "blocked", True),
        ("verification", "blocked", True),
        ("implementation", "blocked", False),
        ("evidence", "needs_human", False),
    ],
)
def test_initial_current_evidence_gap_can_enter_existing_draft_owner(
    source_bound_report, monkeypatch, gap_type, action, eligible
):
    report = source_bound_report
    report.update(
        verdict="NO_DETERMINATION",
        recommendedNextAction=action,
        remainingWork=[
            {
                "requirement": "LIVE-1",
                "gapType": gap_type,
                "remainingWork": "Required proof pending",
            }
        ],
    )
    report["validatedRefs"]["acceptance"]["evidence"][0]["status"] = "UNVERIFIED"
    report["validatedRefs"]["acceptance"]["evidence"][0]["evidenceRefs"] = []
    raw = json.dumps(report).encode()
    descriptor = payloads.build_acceptance_projection(
        report,
        report_ref="artifact://report",
        report_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    projected = payloads.compact_moonspec_verify_metadata(
        report, acceptance_projection=descriptor
    )
    hydrated = payloads.hydrate_acceptance_projection(
        projected, report_bytes=raw, report_ref="artifact://report"
    )
    parent = run_module.MoonMindRunWorkflow()
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent._record_moonspec_verify_gate(
        node_id="verify", outputs={"moonSpecVerify": hydrated}
    )
    assert parent._publish_context["moonSpecGate"]["invalid"] is False
    assert parent._moonspec_gate_qualifies_for_draft_publish() is eligible
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: False)
    assert parent._moonspec_gate_qualifies_for_draft_publish() is False
