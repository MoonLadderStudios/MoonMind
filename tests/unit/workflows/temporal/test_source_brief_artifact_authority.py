"""The trusted issue-loader brief retains its scope at verifier publication.

SQLite/local blobs cover the real artifact and Activity publication boundaries;
the external issue API is represented by a registered tool handler fixture.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.api.failure.v1 import Failure
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceResponse,
    DescribeWorkflowExecutionResponse,
)
from temporalio.converter import DataConverter, DefaultFailureConverter, DefaultPayloadConverter
from temporalio.service import RPCError, RPCStatusCode, WorkflowService
from temporalio.workflow import ParentInfo

from api_service.services.checkpoint_branch_turn_execution import (
    build_branch_turn_execution_identity,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.temporal.activity_runtime import (
    ArtifactParentAuthorityUnavailable,
    TemporalAgentRuntimeActivities,
    TemporalSkillActivities,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.story_output_tools import (
    GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    TRUSTED_EXECUTION_ARTIFACT_HANDOFF_PATCH_ID,
    MoonMindAgentRun,
)
from tests.unit.omnigent.test_remediation_artifact_authority import artifacts  # noqa: F401
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    source_bound_report,  # noqa: F401
)
from tests.unit.workflows.temporal.test_acceptance_boundary_regressions import (
    _digest,
    _request,
)


def _recorded_server(described, launch_request=None):
    async def rpc(*, rpc, req, service, resp_type, retry, metadata, timeout):
        assert service == "workflow"
        assert retry is True
        assert timeout == timedelta(seconds=10)
        if rpc == "describe_workflow_execution":
            return described
        if rpc == "get_workflow_execution_history" and launch_request is not None:
            from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryResponse

            record = described.workflow_execution_info
            return GetWorkflowExecutionHistoryResponse(history={"events": [{
                "event_id": 1,
                "workflow_execution_started_event_attributes": {
                    "workflow_type": {"name": "MoonMind.AgentRun"},
                    "original_execution_run_id": record.execution.run_id,
                    "parent_workflow_namespace": "default",
                    "parent_workflow_namespace_id": record.parent_namespace_id,
                    "parent_workflow_execution": record.parent_execution,
                    "input": {"payloads": await DataConverter.default.encode([
                        launch_request.model_dump(mode="json", by_alias=True),
                    ])},
                },
            }]})
        assert rpc == "describe_namespace"
        return DescribeNamespaceResponse(namespace_info={"id": "namespace-default"})

    calls = AsyncMock(side_effect=rpc)
    return WorkflowService(SimpleNamespace(_rpc_call=calls)), calls


@pytest.mark.asyncio
@pytest.mark.parametrize("new_history", (True, False))
@pytest.mark.parametrize("parent_identity", ("ordinary", "branch"))
@pytest.mark.parametrize(
    "scope", ("own", "unrelated", "missing", "restricted", "quarantined", "temporary")
)
async def test_loader_brief_read_uses_trusted_verifier_parent(
    artifacts,  # noqa: F811
    tmp_path,
    monkeypatch,
    source_bound_report,  # noqa: F811
    scope,
    new_history,
    parent_identity,
):
    parent_id, child_id, semantic_run_id = "parent", "parent:agent:verify", "parent-run"
    if parent_identity == "branch":
        identity = build_branch_turn_execution_identity(
            branch_id="source-branch", branch_turn_id="source-turn",
            logical_step_id="verify", ordinal=1,
        )
        parent_id, child_id = identity.workflow_id, identity.agent_run_workflow_id
        semantic_run_id = identity.semantic_run_id
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
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch: new_history
        if patch == TRUSTED_EXECUTION_ARTIFACT_HANDOFF_PATCH_ID
        else True,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=child_id,
            run_id="child-run",
            task_queue="mm.workflow.user.v2",
            namespace="default",
            parent=ParentInfo(namespace="default", workflow_id=parent_id, run_id="parent-run"),
        ),
    )
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id=parent_id,
            workflow_run_id="parent-run",
        ),
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        dispatcher = SkillActivityDispatcher()
        tool_name = GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME
        dispatcher.register_skill(
            skill_name=tool_name,
            handler=lambda inputs, context: SkillResult(
                status="COMPLETED",
                outputs={
                    "artifactPath": "artifacts/issue-brief.json",
                    "issue_ref": "example/repo#1",
                    "description": "Observe live parity.",
                },
            ),
        )
        snapshot = create_registry_snapshot(
            skills=parse_skill_registry(
                {"skills": [_default_registry_skill_payload(name=tool_name)]}
            ),
            artifact_store=InMemoryArtifactStore(),
        )
        loaded = await TemporalSkillActivities(
            dispatcher=dispatcher, artifact_service=service
        ).mm_tool_execute(
            invocation_payload={
                "id": "brief",
                "tool": {"name": tool_name},
                "inputs": {},
            },
            registry_snapshot=snapshot,
            principal=f"workflow:{parent_id}",
        )
        brief_id = loaded.outputs["briefArtifactRef"]
        brief, source_bytes = await service.read(
            artifact_id=brief_id, principal=f"workflow:{parent_id}"
        )
        assert brief.created_by_principal == f"workflow:{parent_id}"
        if scope == "restricted":
            from api_service.db.models import TemporalArtifactRedactionLevel

            brief.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        elif scope == "quarantined":
            brief.metadata_json = {"quarantine": True}
        await session.commit()
        binding = source_bound_report["validatedRefs"]["acceptance"]
        source_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
        binding["scope"]["sourceDigest"] = source_digest
        binding["sourceBinding"] = {"artifactRef": brief_id, "pointer": ""}
        plan = binding["verificationMap"][0]
        plan.update(
            sourceDigest=source_digest,
            sourceLocator="/description",
            sourceQuote="Observe live parity.",
        )
        binding["evidence"][0]["obligationDigest"] = _digest(plan)
        report_path.write_text(json.dumps(source_bound_report))
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())
        child = MoonMindAgentRun()
        child.run_id = "strict-verify"
        request = _request(
            targetSkill="moonspec-verify",
            verify_artifact_path="artifacts/verify.json",
            acceptanceContract="acceptance/v1",
            acceptanceScopeContract="source-bound/v1",
            briefArtifactRef=brief_id,
        )
        request.step_execution = request.step_execution.model_copy(update={
            "workflow_id": parent_id,
            "run_id": semantic_run_id,
            "step_execution_id": f"{parent_id}:{semantic_run_id}:verify:execution:1",
        })
        if scope == "unrelated":
            request.step_execution = request.step_execution.model_copy(
                update={
                    "workflow_id": "unrelated",
                    "run_id": "other-run",
                    "step_execution_id": "unrelated:other-run:verify:execution:1",
                }
            )
        elif scope == "missing":
            request.step_execution = None
        enriched = child._enrich_result_metadata(
            request=request,
            include_parent_execution=True,
            result=AgentRunResult(
                metadata={"parentWorkflowId": parent_id, "parentRunId": semantic_run_id}
            ),
        )
        monkeypatch.setattr(
            activity,
            "info",
            lambda: SimpleNamespace(
                namespace="default",
                workflow_id=child_id,
                workflow_run_id="child-run",
            ),
        )
        recorded_parent = (
            None
            if scope == "missing"
            else {
                "workflow_id": "unrelated" if scope == "unrelated" else parent_id,
                "run_id": "other-run" if scope == "unrelated" else "parent-run",
            }
        )
        described = DescribeWorkflowExecutionResponse(
            workflow_execution_info={
                "execution": {
                    "workflow_id": child_id,
                    "run_id": "child-run",
                },
                **(
                    {
                        "parent_execution": recorded_parent,
                        "parent_namespace_id": "namespace-default",
                    }
                    if recorded_parent
                    else {}
                ),
            }
        )
        server, calls = _recorded_server(described, launch_request=request)
        if scope == "temporary":
            real_transport = calls.side_effect
            unavailable = RPCError(
                "ancestry service temporarily unavailable", RPCStatusCode.UNAVAILABLE, b""
            )

            async def fail_once(**kwargs):
                if calls.await_count == 1:
                    raise unavailable
                return await real_transport(**kwargs)

            calls.side_effect = fail_once
        monkeypatch.setattr(
            activity,
            "client",
            lambda: SimpleNamespace(
                namespace="default", workflow_service=server, data_converter=DataConverter.default,
            ),
        )
        if scope == "temporary":
            enriched.metadata["instructionRef"] = brief_id
            create_calls = AsyncMock(wraps=service.create)
            monkeypatch.setattr(service, "create", create_calls)
            quota_before = await service._repository.quota_usage_for_scope(
                "system:agent_runtime"
            )
            with pytest.raises(
                ArtifactParentAuthorityUnavailable,
                match="Artifact parent authority is unavailable",
            ) as failure:
                await activities.agent_runtime_publish_artifacts(enriched)
            assert failure.value.__cause__ is unavailable
            serialized_failure = Failure()
            DefaultFailureConverter().to_failure(
                failure.value, DefaultPayloadConverter(), serialized_failure
            )
            assert serialized_failure.application_failure_info.type == (
                "ArtifactParentAuthorityUnavailable"
            )
            assert not serialized_failure.application_failure_info.non_retryable
            create_calls.assert_not_awaited()
            assert await service._repository.quota_usage_for_scope(
                "system:agent_runtime"
            ) == quota_before
            activities.execution_notify_completion.assert_not_awaited()
        # A fresh Activity invocation represents the existing publication retry.
        links_before = {
            link.id for link in await service._repository.list_links(brief_id)
        }
        brief_reads = AsyncMock(wraps=service.read)
        monkeypatch.setattr(service, "read", brief_reads)
        published = await activities.agent_runtime_publish_artifacts(enriched)
        if scope in {"missing", "unrelated"}:
            assert {
                link.id for link in await service._repository.list_links(brief_id)
            } == links_before
            read_scopes = [
                call.kwargs["admitted_principal"]
                for call in brief_reads.await_args_list
                if call.kwargs.get("artifact_id") == brief_id
            ]
            assert read_scopes
            if scope == "missing":
                assert read_scopes == [None]
            else:
                assert all(value in {None, "workflow:unrelated"} for value in read_scopes)
        gate = published.metadata["moonSpecVerify"]
        if scope in {"own", "restricted", "temporary"} or (
            scope in {"unrelated", "missing"}
        ):
            assert not gate.get("contractViolations")
            assert "acceptanceProjection" in gate["validatedRefs"]
            if scope == "temporary":
                assert calls.await_count == (4 if parent_identity == "branch" else 3)
                activities.execution_notify_completion.assert_awaited_once()
        else:
            assert (
                "Original source evidence is unreadable; retry its artifact read"
                in gate["contractViolations"]
            )
            assert "acceptanceProjection" not in gate.get("validatedRefs", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ("missing", "unreadable", "malformed", "nonobject", "failed"))
async def test_ineligible_source_verifier_does_not_lookup_ancestry(
    artifacts, tmp_path, monkeypatch, output  # noqa: F811
):
    from moonmind.workflows.temporal import activity_runtime

    workspace = tmp_path / "workspace"
    report_path = workspace / "artifacts/verify.json"
    report_path.parent.mkdir(parents=True)
    if output != "missing":
        report_path.write_text(
            "{" if output == "malformed" else "[]" if output == "nonobject" else "{}"
        )
    if output == "unreadable":
        read_text = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == report_path:
                raise OSError("fixture report unavailable")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="strict-verify",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    lookup = AsyncMock(side_effect=AssertionError("ineligible output must not lookup ancestry"))
    monkeypatch.setattr(activity_runtime, "_verified_artifact_parent_scope", lookup)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default", workflow_id="parent:agent:verify", workflow_run_id="child-run"
        ),
    )
    async with artifacts() as session:
        activities = TemporalAgentRuntimeActivities(
            artifact_service=TemporalArtifactService(TemporalArtifactRepository(session)),
            run_store=run_store,
        )
        monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())
        result = AgentRunResult(
            failureClass="execution_error" if output == "failed" else None,
            metadata={
                "agentRunId": "strict-verify",
                "verifyArtifactPath": "artifacts/verify.json",
                "acceptanceContract": "acceptance/v1",
                "acceptanceScopeContract": "source-bound/v1",
                "briefArtifactRef": "art_absent",
                "parentWorkflowId": "parent",
                "parentRunId": "parent-run",
            },
        )
        published = await activities.agent_runtime_publish_artifacts(result)
        lookup.assert_not_awaited()
        assert "moonSpecVerify" not in published.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "own",
        "child",
        "child_run",
        "parent",
        "parent_run",
        "namespace",
        "unknown_namespace",
        "no_parent",
        "unavailable",
        "no_client",
    ),
)
async def test_parent_read_scope_requires_exact_server_ancestry(monkeypatch, case):
    from moonmind.workflows.temporal.activity_runtime import (
        _verified_artifact_parent_scope,
    )

    described = DescribeWorkflowExecutionResponse(
        workflow_execution_info={
            "execution": {"workflow_id": "child", "run_id": "child-run"},
            "parent_execution": {"workflow_id": "parent", "run_id": "parent-run"},
            "parent_namespace_id": "namespace-default",
        }
    )
    record = described.workflow_execution_info
    if case == "child":
        record.execution.workflow_id = "another-child"
    elif case == "child_run":
        record.execution.run_id = "stale-child-run"
    elif case == "parent":
        record.parent_execution.workflow_id = "unrelated"
    elif case == "parent_run":
        record.parent_execution.run_id = "stale-parent-run"
    elif case == "namespace":
        record.parent_namespace_id = "unrelated-namespace"
    elif case == "unknown_namespace":
        record.parent_namespace_id = ""
    elif case == "no_parent":
        record.ClearField("parent_execution")
    server, rpc_calls = _recorded_server(described)
    if case == "unavailable":
        rpc_calls.side_effect = RuntimeError("service unavailable")

    def client():
        if case == "no_client":
            raise RuntimeError("not in an Activity")
        return SimpleNamespace(namespace="default", workflow_service=server)

    monkeypatch.setattr(activity, "client", client)
    async def lookup():
        return await _verified_artifact_parent_scope(
            info=SimpleNamespace(
                namespace="default", workflow_id="child", workflow_run_id="child-run"
            ),
            parent_workflow_id="parent",
            parent_run_id="parent-run",
        )

    if case in {"unavailable", "no_client"}:
        with pytest.raises(
            ArtifactParentAuthorityUnavailable,
            match="Artifact parent authority is unavailable",
        ) as failure:
            await lookup()
        assert isinstance(failure.value.__cause__, RuntimeError)
    else:
        assert await lookup() == ("workflow:parent" if case == "own" else None)
    if case != "no_client":
        request = rpc_calls.await_args_list[0].kwargs["req"]
        assert request.namespace == "default"
        assert request.execution.workflow_id == "child"
        assert request.execution.run_id == "child-run"


@pytest.mark.asyncio
async def test_parent_scope_lookup_bounds_the_whole_operation(monkeypatch, caplog):
    from contextlib import asynccontextmanager
    from moonmind.workflows.temporal import activity_runtime

    entered = []

    @asynccontextmanager
    async def exhausted_timeout(seconds):
        entered.append(seconds)
        raise TimeoutError("total ancestry budget exhausted")
        yield

    monkeypatch.setattr(activity_runtime.asyncio, "timeout", exhausted_timeout)
    with pytest.raises(
        ArtifactParentAuthorityUnavailable,
        match="Artifact parent authority is unavailable",
    ) as failure:
        await activity_runtime._verified_artifact_parent_scope(
            info=SimpleNamespace(
                namespace="default", workflow_id="child", workflow_run_id="child-run"
            ),
            parent_workflow_id="parent",
            parent_run_id="parent-run",
        )
    assert entered == [10]
    assert isinstance(failure.value.__cause__, TimeoutError)
    assert (
        "Artifact parent authority is unavailable for child (TimeoutError)"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_parent_scope_lookup_preserves_external_cancellation(monkeypatch):
    import asyncio
    from moonmind.workflows.temporal.activity_runtime import (
        _verified_artifact_parent_scope,
    )

    server, calls = _recorded_server(DescribeWorkflowExecutionResponse())
    calls.side_effect = asyncio.CancelledError
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(namespace="default", workflow_service=server),
    )
    with pytest.raises(asyncio.CancelledError):
        await _verified_artifact_parent_scope(
            info=SimpleNamespace(
                namespace="default", workflow_id="child", workflow_run_id="child-run"
            ),
            parent_workflow_id="parent",
            parent_run_id="parent-run",
        )
