"""Trusted issue inputs survive the real child-request/publication boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity

from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.unit.workflows.skills import (
    test_acceptance_source_binding as source_bindings,
)
from tests.unit.workflows.temporal.test_activity_runtime import temporal_db

source_bound_report = source_bindings.source_bound_report


def _configure_runtime(monkeypatch, *, new_history=True):
    info = SimpleNamespace(
        namespace="default",
        task_queue="mm.workflow.user.v2",
        workflow_id="scope-parent",
        run_id="parent-run",
        workflow_run_id="child-run",
        parent=None,
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: info)
    monkeypatch.setattr(activity, "info", lambda: info)
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: (
            new_history if name == "run-acceptance-source-handoff-v1" else True
        ),
    )


def _request(parent):
    return parent._build_agent_execution_request(
        node_inputs={
            "targetRuntime": "codex_cli",
            "selectedSkill": "moonspec-verify",
            "instructions": "Verify the original issue scope.",
            "verify_artifact_path": "artifacts/verify.json",
            # A plan/provider cannot replace the trusted loader's source.
            "assessmentArtifactRef": "art_forged_assessment",
            "briefArtifactRef": "art_forged_brief",
        },
        node_id="verify",
        tool_name="codex_cli",
    )


@pytest.mark.parametrize("new_history", [False, True])
def test_source_refs_survive_dispatch_and_override_provider_metadata(
    monkeypatch, new_history
):
    _configure_runtime(monkeypatch, new_history=new_history)
    parent = run_module.MoonMindRunWorkflow()
    parent._assessment_context = {
        "assessmentArtifactRef": "art_original_assessment",
        "briefArtifactRef": "art_original_brief",
    }
    request = _request(parent)
    provider_metadata = {
        "assessmentArtifactRef": "art_provider_assessment",
        "briefArtifactRef": "art_provider_brief",
    }
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request,
        result=AgentRunResult(metadata=provider_metadata),
    )
    if new_history:
        assert request.parameters["acceptanceScopeContract"] == "source-bound/v1"
        assert result.metadata["acceptanceScopeContract"] == "source-bound/v1"
        for key, ref in parent._assessment_context.items():
            assert request.parameters[key] == ref
            assert result.metadata[key] == ref
    else:
        assert "acceptanceScopeContract" not in request.parameters
        assert {
            key: result.metadata[key] for key in provider_metadata
        } == provider_metadata


def test_missing_trusted_source_cannot_be_supplied_by_provider(monkeypatch):
    _configure_runtime(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    parent._assessment_context = {"assessmentArtifactRef": "art_original_assessment"}
    request = _request(parent)
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request,
        result=AgentRunResult(metadata={"briefArtifactRef": "art_provider_brief"}),
    )
    assert "briefArtifactRef" not in result.metadata


@pytest.mark.asyncio
async def test_dispatch_reads_durable_assessment_before_accepting_blocking_scope(
    tmp_path, monkeypatch
):
    _configure_runtime(monkeypatch)
    scope = {
        "sourceRef": "example/repo#2740",
        "sourceDigest": "sha256:original",
        "complete": True,
        "requirementIds": ["AC-1"],
    }
    workspace = tmp_path / "workspace"
    report_path = workspace / "artifacts/verify.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "verdict": "BLOCKED",
                "recommendedNextAction": "blocked",
                "recoverableInCurrentRuntime": False,
                "scope": scope,
                "remainingWork": [
                    {
                        "requirement": "OTHER-ISSUE-GPU",
                        "gapType": "environment",
                        "remainingWork": "Run unrelated physical release proof",
                    }
                ],
            }
        )
    )
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="scope-run",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(timezone.utc),
            workspacePath=str(workspace),
        )
    )
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        artifact, _ = await service.create(
            principal="system:agent_runtime",
            content_type="application/json",
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="system:agent_runtime",
            payload=json.dumps({"scope": scope}).encode(),
            content_type="application/json",
        )
        parent = run_module.MoonMindRunWorkflow()
        parent._assessment_context = {"assessmentArtifactRef": artifact.artifact_id}
        request = _request(parent)
        child = MoonMindAgentRun()
        child.run_id = "scope-run"
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())

        async def publish(name, result, **_):
            assert name == "agent_runtime.publish_artifacts"
            # Exercise serialization at the actual Activity input boundary.
            typed = AgentRunResult(**result.model_dump(mode="json", by_alias=True))
            return await activities.agent_runtime_publish_artifacts(typed)

        monkeypatch.setattr(child, "_execute_routed_activity", publish)
        result = await child._publish_terminal_result(
            request=request,
            result=AgentRunResult(summary="Verification blocked"),
        )
        assert any(
            "mandatory requirement" in reason
            for reason in result.metadata["moonSpecVerify"]["contractViolations"]
        )
        assert (
            parent._moonspec_verify_gate_result(result.metadata).verdict
            == "NO_DETERMINATION"
        )
        _, stored = await service.read_path(
            artifact_id=artifact.artifact_id,
            principal="system:agent_runtime",
        )
        assert json.loads(stored.read_text()) == {"scope": scope}


@pytest.mark.parametrize("with_current_evidence", [False, True])
def test_delayed_evidence_metadata_uses_only_admitted_observation(
    monkeypatch, with_current_evidence
):
    _configure_runtime(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    parent._assessment_context = {"assessmentArtifactRef": "art_original"}
    request = _request(parent)
    expected = {
        "evidenceSet": {"artifact://job": "sha256:" + "a" * 64},
        "evidenceSetDigest": "sha256:" + "b" * 64,
        "acceptanceSubject": {
            "repository": "example/repo",
            "revision": "current",
            "contentDigest": "git-tree:current",
        },
        "evidenceManifestRef": "art_current_manifest",
    }
    if with_current_evidence:
        request = request.model_copy(
            update={"parameters": {**request.parameters, **expected}}
        )
    provider = {
        "evidenceSet": {"artifact://forged": "sha256:" + "f" * 64},
        "evidenceSetDigest": "sha256:" + "f" * 64,
        "acceptanceSubject": {
            "repository": "other/repo",
            "revision": "other",
            "contentDigest": "git-tree:other",
        },
        "evidenceManifestRef": "art_forged_manifest",
    }
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request,
        result=AgentRunResult(metadata=provider),
    )
    if with_current_evidence:
        assert {key: result.metadata[key] for key in expected} == expected
    else:
        assert not set(provider).intersection(result.metadata)


@pytest.mark.parametrize("has_trusted_source", [False, True])
def test_authored_runtime_cannot_supply_source_or_observed_evidence(
    monkeypatch, has_trusted_source
):
    _configure_runtime(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    if has_trusted_source:
        parent._assessment_context = {"assessmentArtifactRef": "art_original"}
    request = parent._build_agent_execution_request(
        node_inputs={
            "selectedSkill": "moonspec-verify",
            "instructions": "Verify selected scope",
            "runtime": {
                "mode": "codex_cli",
                "parameters": {
                    "acceptanceScopeContract": "source-bound/v1",
                    "assessmentArtifactRef": "art_authored_assessment",
                    "briefArtifactRef": "art_authored_brief",
                    "acceptanceSubject": {
                        "repository": "other/repo",
                        "revision": "forged",
                        "contentDigest": "git-tree:forged",
                    },
                    "evidenceSet": {"artifact://forged": "sha256:" + "f" * 64},
                    "evidenceSetDigest": "sha256:" + "f" * 64,
                    "evidenceManifestRef": "art_authored_manifest",
                },
            },
        },
        node_id="verify",
        tool_name="codex_cli",
    )
    for key in (
        "acceptanceSubject",
        "evidenceSet",
        "evidenceSetDigest",
        "evidenceManifestRef",
        "briefArtifactRef",
    ):
        assert key not in request.parameters
    if has_trusted_source:
        assert request.parameters["assessmentArtifactRef"] == "art_original"
    else:
        assert "assessmentArtifactRef" not in request.parameters
        assert "acceptanceScopeContract" not in request.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "omit_current_requirement,large_report",
    [(False, False), (True, False), (False, True)],
)
async def test_publisher_reconciles_old_blocked_scope_from_original_source_bytes(
    tmp_path,
    monkeypatch,
    source_bound_report,
    omit_current_requirement,
    large_report,
):
    _configure_runtime(monkeypatch)
    source_bytes, old_assessment = source_bindings._source_reconciliation_fixture(
        source_bound_report
    )
    workspace = tmp_path / "workspace"
    report_path = workspace / "artifacts/verify.json"
    report_path.parent.mkdir(parents=True)
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="reconciled-run",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(timezone.utc),
            workspacePath=str(workspace),
        )
    )
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )

        async def store_source(raw):
            ref, _ = await service.create(
                principal="system:agent_runtime",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=ref.artifact_id,
                principal="system:agent_runtime",
                payload=raw,
                content_type="application/json",
            )
            return ref.artifact_id

        assessment_ref = await store_source(json.dumps(old_assessment).encode())
        brief_ref = await store_source(source_bytes)
        binding = source_bound_report["validatedRefs"]["acceptance"]
        binding["sourceBinding"]["artifactRef"] = brief_ref
        if large_report:
            plan = binding["verificationMap"][0]
            plan["requiredBehavior"] = "Required original behavior. " * 1000
            binding["evidence"][0]["obligationDigest"] = source_bindings._digest(plan)
        if omit_current_requirement:
            binding["scope"]["requirementIds"] = ["UNRELATED"]
        report_path.write_text(json.dumps(source_bound_report))
        parent = run_module.MoonMindRunWorkflow()
        parent._assessment_context = {
            "assessmentArtifactRef": assessment_ref,
            "assessmentVerdict": "BLOCKED",
            "briefArtifactRef": brief_ref,
        }
        request = _request(parent)
        child = MoonMindAgentRun()
        child.run_id = "reconciled-run"
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())

        async def publish(name, result, **_):
            assert name == "agent_runtime.publish_artifacts"
            return await activities.agent_runtime_publish_artifacts(
                AgentRunResult(**result.model_dump(mode="json", by_alias=True)),
            )

        monkeypatch.setattr(child, "_execute_routed_activity", publish)
        published = await child._publish_terminal_result(
            request=request,
            result=AgentRunResult(summary="Current source proof complete"),
        )

        async def read_artifact(_name, request, **_kwargs):
            assert _name == "artifact.read"
            _, raw = await service.read(
                artifact_id=request.artifact_ref.removeprefix("artifact://"),
                principal=request.principal,
                allow_restricted_raw=True,
            )
            return raw

        monkeypatch.setattr(run_module, "execute_typed_activity", read_artifact)
        mapped = parent._map_agent_run_result(published)
        hydrated = await parent._hydrate_acceptance_result(mapped)
        gate = parent._moonspec_verify_gate_result(hydrated["outputs"])
        if not omit_current_requirement:
            assert (
                "acceptanceProjection"
                in published.metadata["moonSpecVerify"]["validatedRefs"]
            )
            assert (
                "acceptance"
                not in published.metadata["moonSpecVerify"]["validatedRefs"]
            )
            assert len(published.model_dump_json(by_alias=True).encode()) < 16384
        assert gate.verdict == (
            "NO_DETERMINATION" if omit_current_requirement else "FULLY_IMPLEMENTED"
        )
        assert parent._assessment_context["assessmentVerdict"] == "BLOCKED"
        _, old_bytes = await service.read(
            artifact_id=assessment_ref,
            principal="system:agent_runtime",
            allow_restricted_raw=True,
        )
        assert json.loads(old_bytes) == old_assessment
