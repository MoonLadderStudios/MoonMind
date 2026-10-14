"""Strict acceptance metadata is a byte-bound report reference, never proof."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.skills.acceptance_contract import acceptance_evidence
from moonmind.workflows.temporal import agent_result_payloads as payloads
from tests.unit.workflows.skills import test_acceptance_source_binding as bindings

source_bound_report = bindings.source_bound_report


def _large_report(report):
    binding = report["validatedRefs"]["acceptance"]
    plan = binding["verificationMap"][0]
    plan["requiredBehavior"] = "Required original behavior. " * 1000
    binding["evidence"][0]["obligationDigest"] = bindings._digest(plan)
    report["acceptanceScopeContract"] = "source-bound/v1"
    return report


def _stored(report):
    raw = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()


def _project(report):
    raw, digest = _stored(report)
    projection = payloads.build_acceptance_projection(
        report, report_ref="art_complete_report", report_digest=digest
    )
    gate = payloads.compact_moonspec_verify_metadata(
        {**report, "gateResultRef": "art_complete_report"},
        acceptance_projection=projection,
    )
    return gate, raw


def test_large_strict_report_crosses_metadata_boundary_without_becoming_inline_proof(
    source_bound_report,
):
    report = _large_report(source_bound_report)
    raw, _ = _stored(report)
    assert len(raw) > 16 * 1024
    gate, raw = _project(report)
    result = AgentRunResult(metadata={"moonSpecVerify": gate})
    wire = result.model_dump_json(by_alias=True)
    assert len(wire.encode()) < 16 * 1024
    assert acceptance_evidence(gate) is None
    assert gate["acceptanceScopeContract"] == "source-bound/v1"
    projection = gate["validatedRefs"]["acceptanceProjection"]
    for key in (
        "subject",
        "scope",
        "completionTarget",
        "freshness",
        "evidenceSetDigest",
    ):
        assert projection[key] == report["validatedRefs"]["acceptance"][key]
    assert payloads.compact_moonspec_verify_metadata(gate) == gate
    hydrated = payloads.hydrate_acceptance_projection(
        gate, report_bytes=raw, report_ref="artifact://art_complete_report"
    )
    assert acceptance_evidence(hydrated) is not None
    assert (
        hydrated["validatedRefs"]["acceptance"]["verificationMap"]
        == report["validatedRefs"]["acceptance"]["verificationMap"]
    )
    assert hydrated["gateResultRef"] == "art_complete_report"


@pytest.mark.parametrize(
    "mismatch",
    [
        "bytes",
        "ref",
        "subject",
        "scope",
        "target",
        "freshness",
        "inventory",
        "verdict",
        "action",
        "invalid",
        "violations",
    ],
)
def test_projection_readback_fails_closed_on_mismatched_report(
    source_bound_report, mismatch
):
    gate, raw = _project(source_bound_report)
    ref = "art_complete_report"
    descriptor = gate["validatedRefs"]["acceptanceProjection"]
    if mismatch == "bytes":
        raw += b" "
    elif mismatch == "ref":
        ref = "art_another_report"
    elif mismatch == "subject":
        descriptor["subject"]["revision"] = "another"
    elif mismatch == "scope":
        descriptor["scope"]["requirementIds"] = ["INVENTED"]
    elif mismatch == "target":
        descriptor["completionTarget"]["revision"] = "another"
    elif mismatch == "freshness":
        descriptor["freshness"]["policy"] = "report"
    elif mismatch == "inventory":
        descriptor["evidenceSetDigest"] = "sha256:" + "0" * 64
    elif mismatch == "verdict":
        gate["verdict"] = "BLOCKED"
    elif mismatch == "action":
        gate["recommendedNextAction"] = "blocked"
    elif mismatch == "invalid":
        gate["invalid"] = True
    else:
        gate["contractViolations"] = ["Original source mismatch"]
    with pytest.raises(ValueError):
        payloads.hydrate_acceptance_projection(gate, report_bytes=raw, report_ref=ref)


def test_invalid_report_cannot_mint_projection(source_bound_report):
    source_bound_report["contractViolations"] = ["Source artifact is unreadable"]
    _, digest = _stored(source_bound_report)
    with pytest.raises(ValueError):
        payloads.build_acceptance_projection(
            source_bound_report, report_ref="art_report", report_digest=digest
        )


def test_nonpass_projection_keeps_full_gap_report_for_hydration(source_bound_report):
    report = copy.deepcopy(source_bound_report)
    report["verdict"] = "ADDITIONAL_WORK_NEEDED"
    report["recommendedNextAction"] = "reattempt_current_step"
    report["validatedRefs"]["acceptance"]["evidence"] = []
    report["remainingWork"] = [
        {
            "requirement": "LIVE-1",
            "gapType": "evidence",
            "remainingWork": "Obtain current live proof",
        }
    ]
    gate, raw = _project(report)
    assert acceptance_evidence(gate) is None
    hydrated = payloads.hydrate_acceptance_projection(
        gate, report_bytes=raw, report_ref="art_complete_report"
    )
    assert hydrated["remainingWork"] == report["remainingWork"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_story_finalizer_rereads_projection_with_workflow_authority(
    source_bound_report, mismatch
):
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal import story_output_tools

    gate, raw = _project(_large_report(source_bound_report))
    service = type("Service", (), {})()
    service.read = AsyncMock(return_value=(None, raw + b" " if mismatch else raw))
    context = {
        "temporal_artifact_service": service,
        "admittedWorkflowId": "owner",
        "previousOutputs": {
            "moonSpecVerify": gate,
            "gateResultRef": "art_complete_report",
        },
    }
    full = await story_output_tools._objective_verification_payload({}, context)
    if mismatch:
        assert full is None
    else:
        assert acceptance_evidence(full) is not None
    service.read.assert_awaited_once_with(
        artifact_id="art_complete_report",
        principal="system:agent_runtime",
        admitted_principal="workflow:owner",
        allow_restricted_raw=True,
    )


def test_projection_retains_every_requirement_beyond_old_list_limit(
    source_bound_report,
):
    binding = source_bound_report["validatedRefs"]["acceptance"]
    plan, evidence = binding["verificationMap"][0], binding["evidence"][0]
    binding["verificationMap"] = []
    binding["evidence"] = []
    binding["scope"]["requirementIds"] = []
    for index in range(35):
        row = {
            **plan,
            "id": f"AC-{index}",
            "requiredBehavior": "Required behavior. " * 100,
        }
        binding["scope"]["requirementIds"].append(row["id"])
        binding["verificationMap"].append(row)
        binding["evidence"].append(
            {
                **evidence,
                "requirementId": row["id"],
                "obligationDigest": bindings._digest(row),
            }
        )
    gate, raw = _project(source_bound_report)
    assert len(raw) > 16384
    assert (
        gate["validatedRefs"]["acceptanceProjection"]["scope"]["requirementIds"]
        == binding["scope"]["requirementIds"]
    )
    AgentRunResult(metadata={"moonSpecVerify": gate})


def test_marked_strict_report_cannot_compact_into_legacy_shaped_success(
    source_bound_report,
):
    source_bound_report["acceptanceScopeContract"] = "source-bound/v1"
    gate = payloads.compact_moonspec_verify_metadata(source_bound_report)
    assert acceptance_evidence(gate) is None
    assert gate["acceptanceScopeContract"] == "source-bound/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("digest_state", ["match", "mismatch", "missing"])
async def test_ready_activity_checks_published_report_bytes(
    source_bound_report, monkeypatch, digest_state
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal import activity_runtime

    report = _large_report(source_bound_report)
    raw, digest = _stored(report)
    service = SimpleNamespace(read=AsyncMock(return_value=(None, raw)))
    ready = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(GitHubService, "mark_automation_draft_ready", ready)
    monkeypatch.setattr(
        activity_runtime, "_admitted_activity_workflow_id", lambda: "owner"
    )
    binding = report["validatedRefs"]["acceptance"]
    result = await activity_runtime.TemporalIntegrationActivities(
        artifact_service=service
    ).github_issue_collect_evidence(
        {
            "action": "mark_ready",
            "automationOwnedDraft": True,
            "repository": binding["subject"]["repository"],
            "headSha": binding["subject"]["revision"],
            "prNumber": 42,
            "gateResultRef": "art_complete_report",
            "evidenceSetDigest": binding["evidenceSetDigest"],
            "reportDigest": (
                digest
                if digest_state == "match"
                else "sha256:" + "0" * 64 if digest_state == "mismatch" else None
            ),
        }
    )
    assert result["status"] == ("ready" if digest_state == "match" else "blocked")
    assert ready.await_count == (1 if digest_state == "match" else 0)
    assert service.read.await_args.kwargs["admitted_principal"] == "workflow:owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("read_state", ["match", "mismatch", "unreadable"])
@pytest.mark.parametrize("already_hydrated", [False, True])
async def test_jira_completion_rereads_immutable_report_before_mutation(
    source_bound_report, monkeypatch, read_state, already_hydrated
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.integrations.jira.tool import JiraToolService
    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal import activity_runtime

    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding["scope"]["sourceRef"] = "TEST-1"
    binding["verificationMap"][0]["sourceRef"] = "TEST-1"
    binding["evidence"][0]["obligationDigest"] = bindings._digest(
        binding["verificationMap"][0]
    )
    binding["subject"]["revision"] = binding["completionTarget"]["revision"]
    binding["completionTarget"]["contentDigest"] = binding["subject"]["contentDigest"]
    gate, raw = _project(_large_report(source_bound_report))
    if already_hydrated:
        gate = payloads.hydrate_acceptance_projection(
            gate, report_bytes=raw, report_ref="art_complete_report"
        )
    read = AsyncMock(
        return_value=(None, raw + b" " if read_state == "mismatch" else raw)
    )
    if read_state == "unreadable":
        read.side_effect = ValueError("Artifact unreadable")
    service = SimpleNamespace(read=read)
    transition = AsyncMock(return_value={})
    monkeypatch.setattr(
        JiraToolService,
        "get_issue",
        AsyncMock(
            return_value={
                "key": "TEST-1",
                "fields": {
                    "status": {
                        "name": "In Progress",
                        "statusCategory": {"key": "indeterminate"},
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        JiraToolService,
        "get_transitions",
        AsyncMock(
            return_value={
                "transitions": [
                    {
                        "id": "31",
                        "name": "Done",
                        "to": {"name": "Done", "statusCategory": {"key": "done"}},
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(JiraToolService, "transition_issue", transition)
    monkeypatch.setattr(
        GitHubService,
        "read_repository_target",
        AsyncMock(return_value=binding["completionTarget"]),
    )
    monkeypatch.setattr(
        activity_runtime, "_admitted_activity_workflow_id", lambda: "actual-owner"
    )
    result = await activity_runtime.TemporalIntegrationActivities(
        artifact_service=service
    ).merge_automation_complete_post_merge_jira(
        {
            "parentWorkflowId": "actual-owner",
            "jiraIssueKey": "TEST-1",
            "postMergeJira": {"enabled": True, "issueKey": "TEST-1"},
            "candidateContext": {
                "acceptanceGate": gate,
                "completionTargetRef": "refs/heads/main",
            },
        }
    )
    assert transition.await_count == (1 if read_state == "match" else 0), result
    if read_state != "match":
        assert result["status"] == "blocked"
    assert read.await_args.kwargs["admitted_principal"] == "workflow:actual-owner"
