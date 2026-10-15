"""Acceptance reports retain evidence and authority across their handoff."""

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity

from moonmind.config.settings import settings
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRuntimeStepExecutionLaunch,
    ManagedRunRecord,
)
from moonmind.workflows.skills.acceptance_contract import acceptance_evidence
from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    source_bound_report,  # noqa: F401
)
from tests.unit.workflows.temporal.test_remediation_context import temporal_db


def _digest(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _request(**parameters):
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="parent",
        idempotencyKey="strict-verify",
        stepExecution=AgentRuntimeStepExecutionLaunch(
            workflowId="parent",
            runId="parent-run",
            logicalStepId="verify",
            executionOrdinal=1,
            stepExecutionId="parent:parent-run:verify:execution:1",
            runtimeContextPolicy="fresh_agent_run",
        ),
        parameters=parameters,
    )


def _child_info():
    return SimpleNamespace(
        workflow_id="parent:agent:verify",
        run_id="child-run",
        task_queue="mm.workflow.user.v2",
    )


@pytest.mark.asyncio
async def test_published_full_report_is_readable_by_parent_only(
    tmp_path, monkeypatch, source_bound_report  # noqa: F811
):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    workspace = tmp_path / "workspace"
    report_path = workspace / "artifacts/verify.json"
    report_path.parent.mkdir(parents=True)
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="strict-verify",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(timezone.utc),
            workspacePath=str(workspace),
        )
    )
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="parent:agent:verify",
            workflow_run_id="child-run",
        ),
    )
    monkeypatch.setattr(run_module.workflow, "info", _child_info)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )

        async def persist(raw):
            artifact, _ = await service.create(
                principal="system:agent_runtime", content_type="application/json"
            )
            return await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system:agent_runtime",
                payload=raw,
                content_type="application/json",
            )

        source_bytes = (
            b'{"issue_ref":"example/repo#1","description":"Observe live parity."}\n'
        )
        brief = await persist(source_bytes)
        evidence_bytes = b'{"observed":"live parity"}\n'
        evidence = await persist(evidence_bytes)
        report = source_bound_report
        binding = report["validatedRefs"]["acceptance"]
        binding["scope"]["sourceDigest"] = _digest(source_bytes)
        binding["sourceBinding"] = {"artifactRef": brief.artifact_id, "pointer": ""}
        plan = binding["verificationMap"][0]
        plan.update(
            sourceDigest=_digest(source_bytes),
            sourceLocator="/description",
            sourceQuote="Observe live parity.",
            requiredBehavior="Observe live parity. " * 1800,
        )
        evidence_ref = "artifact://" + evidence.artifact_id
        binding["evidenceSet"] = {evidence_ref: _digest(evidence_bytes)}
        binding["evidenceSetDigest"] = _digest(binding["evidenceSet"])
        binding["evidence"][0].update(
            obligationDigest=_digest(plan), evidenceRefs=[evidence_ref]
        )
        report_path.write_text(json.dumps(report))
        assert len(report_path.read_bytes()) > 16 * 1024
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(
            activities,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        child = MoonMindAgentRun()
        child.run_id = "strict-verify"
        enriched = child._enrich_result_metadata(
            request=_request(
                targetSkill="moonspec-verify",
                verify_artifact_path="artifacts/verify.json",
                acceptanceContract="acceptance/v1",
                acceptanceScopeContract="source-bound/v1",
                briefArtifactRef=brief.artifact_id,
            ),
            result=AgentRunResult(),
            include_parent_execution=True,
        )
        published = await activities.agent_runtime_publish_artifacts(enriched)
        result = AgentRunResult.model_validate_json(
            published.model_dump_json(by_alias=True)
        )
        gate = result.metadata["moonSpecVerify"]
        assert not gate.get("contractViolations")
        assert "acceptanceProjection" in gate["validatedRefs"]
        report_ref = gate["validatedRefs"]["acceptanceProjection"]["reportRef"]
        # The producer wrote readable bytes; the parent must receive its own grant.
        await service.read(
            artifact_id=report_ref, principal="workflow:parent:agent:verify"
        )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=report_ref, principal="workflow:unrelated")
        monkeypatch.setattr(
            run_module.workflow,
            "info",
            lambda: SimpleNamespace(
                workflow_id="parent",
                run_id="parent-run",
                task_queue="mm.workflow.user.v2",
            ),
        )
        parent = run_module.MoonMindRunWorkflow()
        parent._execute_kwargs_for_route = lambda _: {}
        real_reads = TemporalArtifactActivities(service)

        async def dispatch(name, request, **kwargs):
            assert name == "artifact.read"
            assert request.principal == "workflow:parent"
            return await real_reads.artifact_read(request)

        monkeypatch.setattr(run_module, "execute_typed_activity", dispatch)
        hydrated = await parent._hydrate_acceptance_result(
            parent._map_agent_run_result(result)
        )
        full = hydrated["outputs"]["moonSpecVerify"]
        assert full["validatedRefs"]["acceptance"] == binding
        assert acceptance_evidence(full) is not None


def test_handoff_identity_comes_from_workflow_and_admitted_parent(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(run_module.workflow, "info", _child_info)
    enriched = MoonMindAgentRun()._enrich_result_metadata(
        request=_request(),
        result=AgentRunResult(
            metadata={
                "childWorkflowId": "unrelated-child",
                "childRunId": "unrelated-child-run",
                "parentWorkflowId": "unrelated",
                "parentRunId": "unrelated-run",
            }
        ),
        include_parent_execution=True,
    )
    assert enriched.metadata["childWorkflowId"] == "parent:agent:verify"
    assert enriched.metadata["childRunId"] == "child-run"
    assert enriched.metadata["parentWorkflowId"] == "parent"
    assert enriched.metadata["parentRunId"] == "parent-run"


@pytest.mark.parametrize("candidate_success", [True, False])
@pytest.mark.parametrize("target_completion", [True, False])
def test_advisory_flags_do_not_replace_source_bound_acceptance(
    source_bound_report, candidate_success, target_completion  # noqa: F811
):
    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding.update(
        candidateSuccess=candidate_success, targetCompletion=target_completion
    )
    evidence = acceptance_evidence(source_bound_report)
    assert evidence is not None
    assert (
        parse_step_gate_result(source_bound_report, require_acceptance=True).verdict
        == "FULLY_IMPLEMENTED"
    )
    for by_alias in (True, False):
        assert (
            not {
                "candidateSuccess",
                "targetCompletion",
                "candidate_success",
                "target_completion",
            }
            & evidence.model_dump(by_alias=by_alias).keys()
        )
    assert not evidence.matches_target(
        repository="example/repo", ref="refs/heads/main", revision="base"
    )
    binding["completionTarget"]["contentDigest"] = binding["subject"]["contentDigest"]
    evidence = acceptance_evidence(source_bound_report)
    assert evidence.matches_target(
        repository="example/repo", ref="refs/heads/main", revision="base"
    )
    assert not evidence.matches_target(
        repository="example/repo", ref="refs/heads/main", revision="stale"
    )


@pytest.mark.parametrize("field", ["candidateSuccess", "targetCompletion"])
@pytest.mark.parametrize("value", ["true", "false", 0, 1, [], {}])
def test_advisory_flags_must_be_strict_booleans(
    source_bound_report, field, value  # noqa: F811
):
    source_bound_report["validatedRefs"]["acceptance"][field] = value
    assert acceptance_evidence(source_bound_report) is None
    assert (
        parse_step_gate_result(source_bound_report, require_acceptance=True).verdict
        == "NO_DETERMINATION"
    )
