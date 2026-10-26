"""The real child publisher delegates generated envelopes to its proven parent."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
from temporalio.workflow import ParentInfo

from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.schemas.temporal_activity_models import ArtifactReadInput
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    ArtifactParentAuthorityUnavailable,
    TemporalAgentRuntimeActivities,
    build_activity_bindings,
)
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
from tests.unit.workflows.temporal.test_acceptance_boundary_regressions import _request
from tests.unit.workflows.temporal.test_remediation_context import temporal_db
from tests.unit.workflows.temporal.test_source_brief_artifact_authority import (
    _recorded_server,
)

pytestmark = pytest.mark.asyncio


async def test_reporting_failure_preserves_confirmed_result_and_original_diagnostics(
    tmp_path, monkeypatch
):
    execution_context(monkeypatch)
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(artifact_service=service)
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(side_effect=RuntimeError("report transport unavailable")),
        )
        produced = AgentRunResult(
            executionState="completed",
            objectiveOutcome="succeeded",
            publicationState="published",
            diagnosticsRef="art_original",
        )
        published = await publisher.agent_runtime_publish_artifacts(produced)
        assert published.failure_class is None
        assert published.objective_outcome == "succeeded"
        assert published.publication_state == "published"
        assert "art_original" in published.diagnostic_refs
        assert [error.code for error in published.secondary_errors] == [
            "completion_notification_unavailable"
        ]
        mapped = run_module.MoonMindRunWorkflow()._map_agent_run_result(published)
        assert (
            mapped["outputs"]["agentResult"]["secondaryErrors"][0]["code"]
            == "completion_notification_unavailable"
        )


def execution_context(monkeypatch):
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="parent:agent:verify",
            workflow_run_id="child-run",
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="parent:agent:verify",
            run_id="child-run",
            parent=ParentInfo(
                namespace="default", workflow_id="parent", run_id="parent-run"
            ),
        ),
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    server, calls = _recorded_server(
        DescribeWorkflowExecutionResponse(
            workflow_execution_info={
                "execution": {
                    "workflow_id": "parent:agent:verify",
                    "run_id": "child-run",
                },
                "parent_execution": {"workflow_id": "parent", "run_id": "parent-run"},
                "parent_namespace_id": "namespace-default",
            },
        )
    )
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(namespace="default", workflow_service=server),
    )
    return calls


async def test_generated_result_and_summary_reach_actual_parent_reader_only(
    tmp_path, monkeypatch
):
    execution_context(monkeypatch)
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        private, _ = await service.create(
            principal="workflow:unrelated", content_type="text/plain"
        )
        await service.write_complete(
            artifact_id=private.artifact_id,
            principal="workflow:unrelated",
            payload=b"unrelated private bytes",
            content_type="text/plain",
        )
        child = MoonMindAgentRun()
        result = child._enrich_result_metadata(
            request=_request(),
            result=AgentRunResult(
                summary="Preserved child result", outputRefs=[private.artifact_id]
            ),
            include_parent_execution=True,
        )
        publisher = TemporalAgentRuntimeActivities(artifact_service=service)
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await publisher.agent_runtime_publish_artifacts(result)
        # Exercise the exact serialized producer result and parent mapper used
        # before its diagnostics/PR-evidence fallback reads the envelope.
        returned = AgentRunResult.model_validate_json(
            published.model_dump_json(by_alias=True)
        )
        parent = run_module.MoonMindRunWorkflow()
        mapped = parent._map_agent_run_result(returned)
        monkeypatch.setattr(
            run_module.workflow, "info", lambda: SimpleNamespace(workflow_id="parent")
        )
        catalog = build_default_activity_catalog()
        selected = TemporalActivityCatalog(
            activities=tuple(
                a for a in catalog.activities if a.activity_type == "artifact.read"
            ),
            fleets=catalog.fleets,
        )
        (read,) = build_activity_bindings(
            selected, artifact_activities=TemporalArtifactActivities(service)
        )
        result_bytes = await read.handler(
            ArtifactReadInput(
                principal=parent._principal(),
                artifact_ref=mapped["outputs"]["diagnosticsRef"],
            )
        )
        restored = AgentRunResult.model_validate_json(result_bytes)
        assert restored.summary == "Preserved child result"
        summary_bytes = await read.handler(
            ArtifactReadInput(
                principal=parent._principal(),
                artifact_ref=returned.metadata["outputSummaryRef"],
            )
        )
        assert json.loads(summary_bytes)["summary"] == "Preserved child result"
        for artifact_id in (
            returned.diagnostics_ref,
            returned.metadata["outputSummaryRef"],
        ):
            assert (
                await service.read(
                    artifact_id=artifact_id, principal="workflow:parent:agent:verify"
                )
            )[1]
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=artifact_id, principal="workflow:unrelated"
                )
        # Knowing a ref in provider output does not delegate that source blob.
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=private.artifact_id, principal="workflow:parent"
            )


@pytest.mark.parametrize("sidecar", [None, "assessment", "brief", "legacy_verifier"])
@pytest.mark.parametrize("claimed_parent", ["victim", "parent"])
async def test_generated_outputs_require_the_recorded_parent(
    tmp_path, monkeypatch, sidecar, claimed_parent
):
    calls = execution_context(monkeypatch)
    workspace = tmp_path / "workspace"
    (workspace / "artifacts").mkdir(parents=True)
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="handoff-run",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    metadata = {
        "agentRunId": "handoff-run",
        "parentWorkflowId": claimed_parent,
        "parentRunId": f"{claimed_parent}-run",
        "childWorkflowId": "victim",
    }
    if sidecar:
        path = f"artifacts/{sidecar}.json"
        (workspace / path).write_text(
            json.dumps(
                {
                    "issue_provider": "github",
                    "issue_ref": "example/repo#1",
                    "verdict": (
                        "FULLY_IMPLEMENTED"
                        if sidecar == "legacy_verifier"
                        else "NOT_IMPLEMENTED"
                    ),
                    "mode": "main",
                    "requirements": [],
                    "schemaVersion": 1,
                    "recommendedNextAction": "advance",
                    "recoverableInCurrentRuntime": True,
                    "remainingWork": [],
                }
            )
        )
        metadata[
            {
                "assessment": "assessment_artifact_path",
                "brief": "brief_artifact_path",
                "legacy_verifier": "verificationArtifactPath",
            }[sidecar]
        ] = path
        if sidecar == "legacy_verifier":
            metadata["moonmind"] = {
                "remediationCadence": {
                    "role": "moonspec-verification-gate",
                    "attempt": 1,
                    "maxAttempts": 3,
                }
            }
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await publisher.agent_runtime_publish_artifacts(
            AgentRunResult(
                summary="Child evidence survives refused delegation",
                metadata=metadata,
            )
        )
        refs = [
            published.diagnostics_ref,
            published.metadata["outputSummaryRef"],
        ]
        if sidecar:
            refs.append(
                published.metadata[
                    {
                        "assessment": "assessmentArtifactRef",
                        "brief": "briefArtifactRef",
                        "legacy_verifier": "moonSpecVerifyArtifactRef",
                    }[sidecar]
                ]
            )
            if sidecar == "legacy_verifier":
                refs.append(published.metadata["sourceMoonSpecVerifyArtifactRef"])
                assert published.metadata["remediationVerificationArtifactRef"] in refs
                assert len(set(refs)) == 4
        for artifact_id in refs:
            assert (
                await service.read(
                    artifact_id=artifact_id, principal="workflow:parent:agent:verify"
                )
            )[1]
            if claimed_parent == "parent":
                assert (
                    await service.read(
                        artifact_id=artifact_id, principal="workflow:parent"
                    )
                )[1]
            denied_readers = ["workflow:victim", "workflow:unrelated"]
            if claimed_parent != "parent":
                denied_readers.append("workflow:parent")
            for reader in denied_readers:
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(artifact_id=artifact_id, principal=reader)
        assert (
            sum(
                call.kwargs["rpc"] == "describe_workflow_execution"
                for call in calls.await_args_list
            )
            == 1
        )


async def test_unavailable_parent_proof_retries_publication_instead_of_incomplete_success(
    tmp_path, monkeypatch
):
    calls = execution_context(monkeypatch)
    calls.side_effect = TimeoutError("ancestry temporarily unavailable")
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(artifact_service=service)
        result = AgentRunResult(
            metadata={"parentWorkflowId": "parent", "parentRunId": "parent-run"}
        )
        with pytest.raises(ArtifactParentAuthorityUnavailable):
            await publisher.agent_runtime_publish_artifacts(result)


async def test_parent_link_failure_retries_instead_of_incomplete_success(
    tmp_path, monkeypatch
):
    execution_context(monkeypatch)
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        original_link = service.link_artifact

        async def failing_link(*, execution_ref, **kwargs):
            if execution_ref.link_type == "input.agent_result_handoff":
                raise RuntimeError("parent result link temporarily unavailable")
            return await original_link(execution_ref=execution_ref, **kwargs)

        monkeypatch.setattr(service, "link_artifact", failing_link)
        publisher = TemporalAgentRuntimeActivities(artifact_service=service)
        result = AgentRunResult(
            metadata={"parentWorkflowId": "parent", "parentRunId": "parent-run"}
        )
        with pytest.raises(RuntimeError, match="parent result link temporarily"):
            await publisher.agent_runtime_publish_artifacts(result)
        monkeypatch.setattr(service, "link_artifact", original_link)
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        completed = await publisher.agent_runtime_publish_artifacts(result)
        for artifact_id in (
            completed.diagnostics_ref,
            completed.metadata["outputSummaryRef"],
        ):
            assert (
                await service.read(artifact_id=artifact_id, principal="workflow:parent")
            )[1]
