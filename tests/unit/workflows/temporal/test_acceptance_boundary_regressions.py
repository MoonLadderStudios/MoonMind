"""Acceptance reports retain evidence and authority across their handoff."""

import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio import activity

from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector
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
from tests.artifact_access import access_for_test
from tests.unit.workflows.skills.test_acceptance_source_binding import (  # noqa: F401
    source_bound_report,
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


@pytest.fixture
def publisher_parent_client(monkeypatch):
    from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse

    from tests.unit.workflows.temporal.test_source_brief_artifact_authority import (
        _recorded_server,
    )

    server, _ = _recorded_server(DescribeWorkflowExecutionResponse(
        workflow_execution_info={
            "execution": {"workflow_id": "parent:agent:verify", "run_id": "child-run"},
            "parent_execution": {"workflow_id": "parent", "run_id": "parent-run"},
            "parent_namespace_id": "namespace-default",
        }
    ))
    monkeypatch.setattr(
        activity, "client",
        lambda: SimpleNamespace(namespace="default", workflow_service=server),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("requirement_count", [1, 40])
@pytest.mark.parametrize("remediation_wrapper", [False, True])
async def test_published_full_report_is_readable_by_parent_only(
    tmp_path, monkeypatch, source_bound_report, requirement_count, remediation_wrapper,  # noqa: F811
    publisher_parent_client,
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
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
        if requirement_count > 1:
            evidence_row = binding["evidence"][0]
            binding["scope"]["requirementIds"] = [
                f"AC-{index}" for index in range(requirement_count)
            ]
            binding["verificationMap"] = []
            binding["evidence"] = []
            for identifier in binding["scope"]["requirementIds"]:
                row = {
                    **plan, "id": identifier,
                    "requiredBehavior": identifier + " 修正🙂検証。" * 250,
                }
                binding["verificationMap"].append(row)
                binding["evidence"].append({
                    **evidence_row, "requirementId": identifier,
                    "obligationDigest": _digest(row),
                })
            binding["evidence"][-1].update(status="PARTIAL", evidenceRefs=[])
            report.update(
                verdict="ADDITIONAL_WORK_NEEDED",
                recommendedNextAction="reattempt_current_step",
                remainingWork=[{
                    "requirement": "AC-39", "gapType": "implementation",
                    "remainingWork": "Repair the final requirement's retry behavior.",
                    "suggestedEvidence": ["tests/test_retry.py"],
                    "recoverableInCurrentRuntime": True,
                }],
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
            result=AgentRunResult(metadata={
                "moonmind": {"remediationCadence": {
                    "role": "moonspec-verification-gate", "attempt": 1,
                }},
            } if remediation_wrapper else {}),
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
            artifact_id=report_ref,
            access=access_for_test("workflow:parent:agent:verify"),
        )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=report_ref, access=access_for_test("workflow:unrelated")
            )
        monkeypatch.setattr(
            run_module.workflow,
            "info",
            lambda: SimpleNamespace(
                namespace="default",
                workflow_id="parent",
                run_id="parent-run",
                task_queue="mm.workflow.user.v2",
                parent=None,
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
        parsed = parse_step_gate_result(full, require_acceptance=True)
        assert parsed.verdict == report["verdict"]
        assert not parsed.invalid and not parsed.degraded
        parent._record_moonspec_verify_gate(node_id="verify", outputs=hydrated["outputs"])
        assert not parent._publish_context["moonSpecGate"]["invalid"]
        previous = parent._merge_trusted_issue_context({
            "trustedSource": "moonmind.github.get_issue", "issueRef": "example/repo#1",
        })
        request = parent._build_agent_execution_request(
            node_inputs={
                "targetRuntime": "omnigent", "repository": "example/repo",
                "instructions": "Continue from the controlling verifier findings.",
                "previousOutputs": previous,
            },
            node_id="remediate", tool_name="omnigent",
        )
        assert len(request.model_dump_json(by_alias=True).encode()) < 64 * 1024
        assert f"artifact://{full['gateResultRef']}" in request.input_refs
        fresh_workspace = tmp_path / "fresh-remediation-workspace"
        fresh_workspace.mkdir()
        await session.commit()
        gateway = TemporalOmnigentArtifactGateway(
            async_sessionmaker(session.bind, expire_on_commit=False)
        )
        projector = WorkspaceArtifactProjector(gateway)
        # Repeated fresh-turn preparation must preserve both the first and last
        # findings byte-for-byte, without relying on the verifier's old workspace.
        report_path.unlink()
        for _ in range(2):
            attachments = await projector.project_attachments(
                fresh_workspace, refs=tuple(request.input_refs), workflow_id="parent",
                runtime_uid=os.getuid(), runtime_gid=os.getgid(),
            )
            attached = json.loads((fresh_workspace / attachments[0]["path"]).read_bytes())
            if remediation_wrapper:
                assert attached["artifactType"] == "remediation.verification"
                assert attached["remainingGaps"] == report.get("remainingWork", [])
                attached = attached["moonSpecVerify"]
            assert attached["validatedRefs"]["acceptance"] == binding
            rows = attached["validatedRefs"]["acceptance"]["verificationMap"]
            assert rows[0] == binding["verificationMap"][0]
            assert rows[-1] == binding["verificationMap"][-1]
            assert attached.get("remainingWork") == report.get("remainingWork")


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["omitted", "empty", "null", "unresolved", "failed", "mixed"]
)
async def test_publisher_preserves_source_bound_unverified_evidence(
    tmp_path, monkeypatch, source_bound_report, case, publisher_parent_client  # noqa: F811
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
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
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        source_bytes = (
            b'{"issue_ref":"example/repo#1","description":"Observe live parity."}\n'
        )
        if case == "mixed":
            source_bytes = (
                b'{"issue_ref":"example/repo#1","description":"Observe live parity.",'
                b'"acceptance_criteria":"Reject invalid input."}\n'
            )
        brief, _ = await service.create(
            principal="system:agent_runtime", content_type="application/json"
        )
        await service.write_complete(
            artifact_id=brief.artifact_id,
            principal="system:agent_runtime",
            payload=source_bytes,
            content_type="application/json",
        )
        binding = source_bound_report["validatedRefs"]["acceptance"]
        binding["scope"]["sourceDigest"] = _digest(source_bytes)
        binding["sourceBinding"] = {"artifactRef": brief.artifact_id, "pointer": ""}
        plan = binding["verificationMap"][0]
        plan.update(
            sourceDigest=_digest(source_bytes),
            sourceLocator="/description",
            sourceQuote="Observe live parity.",
        )
        binding["evidenceSet"] = {}
        binding["evidenceSetDigest"] = _digest(binding["evidenceSet"])
        row = binding["evidence"][0]
        row.pop("evidenceRefs")
        row.pop("proofMethod")
        row.update(
            status="UNVERIFIED",
            obligationDigest=_digest(plan),
            unverified={
                "reason": "The live provider credentials are unavailable.",
                "verifiableBy": "existing qualified runner",
            },
        )
        if case == "empty":
            row["evidenceRefs"] = []
        elif case == "null":
            row["evidenceRefs"] = None
        elif case == "unresolved":
            row["evidenceRefs"] = ["artifact://missing"]
        elif case == "failed":
            row["status"] = "FAILED"
            row.pop("unverified")
        elif case == "mixed":
            defect_plan = {
                **plan,
                "id": "DEFECT-1",
                "sourceLocator": "/acceptance_criteria",
                "sourceQuote": "Reject invalid input.",
                "requiredBehavior": "Reject invalid input.",
                "executableChecks": ["pytest test_invalid_input"],
                "proofMethods": ["unit_test"],
            }
            binding["scope"]["requirementIds"].append("DEFECT-1")
            binding["verificationMap"].append(defect_plan)
            failed_bytes = b'{"observed":"Invalid input was accepted","passed":false}\n'
            failed_run, _ = await service.create(
                principal="system:agent_runtime", content_type="application/json"
            )
            await service.write_complete(
                artifact_id=failed_run.artifact_id,
                principal="system:agent_runtime",
                payload=failed_bytes,
                content_type="application/json",
            )
            failed_ref = "artifact://" + failed_run.artifact_id
            binding["evidenceSet"] = {failed_ref: _digest(failed_bytes)}
            binding["evidenceSetDigest"] = _digest(binding["evidenceSet"])
            binding["evidence"].append(
                {
                    "requirementId": "DEFECT-1",
                    "status": "FAILED",
                    "obligationDigest": _digest(defect_plan),
                    "evidenceRefs": [failed_ref],
                    "proofMethod": "unit_test",
                }
            )
            source_bound_report.update(
                verdict="ADDITIONAL_WORK_NEEDED",
                recommendedNextAction="reattempt_current_step",
                remainingWork=[
                    {
                        "requirement": "DEFECT-1",
                        "gapType": "implementation",
                        "remainingWork": "Reject the invalid input observed in the test.",
                    }
                ],
            )
        valid = case in {"omitted", "empty", "mixed"}
        assert (acceptance_evidence(source_bound_report) is not None) == (
            valid and case != "mixed"
        )
        report_path.write_text(json.dumps(source_bound_report))
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(
            activities,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await activities.agent_runtime_publish_artifacts(
            AgentRunResult(
                metadata={
                    "agentRunId": "strict-verify",
                    "verify_artifact_path": "artifacts/verify.json",
                    "acceptanceContract": "acceptance/v1",
                    "acceptanceScopeContract": "source-bound/v1",
                    "parentWorkflowId": "parent",
                    "parentRunId": "parent-run",
                    "briefArtifactRef": brief.artifact_id,
                    "acceptanceSubject": binding["subject"],
                    "evidenceSet": binding["evidenceSet"],
                    "evidenceSetDigest": binding["evidenceSetDigest"],
                }
            )
        )
        published = AgentRunResult.model_validate_json(
            published.model_dump_json(by_alias=True)
        )
        gate = published.metadata["moonSpecVerify"]
        assert bool(gate.get("contractViolations")) is not valid
        assert ("acceptanceProjection" in gate.get("validatedRefs", {})) == valid
        _, report_bytes = await service.read(
            artifact_id=gate["gateResultRef"],
            access=access_for_test("system:agent_runtime"),
        )
        stored = json.loads(report_bytes)
        assert stored["validatedRefs"]["acceptance"]["evidence"][0] == row
        if case == "mixed":
            monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
            monkeypatch.setattr(
                run_module.workflow, "now", lambda: datetime.now(timezone.utc)
            )
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
            parent._assessment_context = {"issueRef": "example/repo#1"}
            parent._execute_kwargs_for_route = lambda _: {}
            real_reads = TemporalArtifactActivities(service)

            async def dispatch(name, request, **kwargs):
                assert name == "artifact.read"
                assert request.principal == "workflow:parent"
                return await real_reads.artifact_read(request)

            monkeypatch.setattr(run_module, "execute_typed_activity", dispatch)
            hydrated = await parent._hydrate_acceptance_result(
                parent._map_agent_run_result(published)
            )
            full = hydrated["outputs"]["moonSpecVerify"]
            assert full["validatedRefs"]["acceptance"] == binding
            assert acceptance_evidence(full) is None
            parent._record_moonspec_verify_gate(
                node_id="verify", outputs=hydrated["outputs"]
            )
            recorded = parent._publish_context["moonSpecGate"]
            assert recorded["verdict"] == "ADDITIONAL_WORK_NEEDED"
            assert not recorded["invalid"] and not recorded["degraded"]
            assert parent._apply_blocking_moonspec_gate_to_publish() is True
            section = parent._moonspec_unverified_body_section()
            assert (
                "LIVE-1: The live provider credentials are unavailable. "
                "Verifiable by: existing qualified runner"
            ) in section
            assert "DEFECT-1" not in section
            assert gate["gateResultRef"] in section
            assert acceptance_evidence(recorded) is None


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
