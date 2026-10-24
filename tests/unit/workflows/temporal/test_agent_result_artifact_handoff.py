"""The real child publisher delegates generated envelopes to its proven parent."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
from temporalio.workflow import ParentInfo

from moonmind.schemas.agent_runtime_models import AgentRunResult
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
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.unit.workflows.temporal.test_acceptance_boundary_regressions import _request
from tests.unit.workflows.temporal.test_remediation_context import temporal_db
from tests.unit.workflows.temporal.test_source_brief_artifact_authority import (
    _recorded_server,
)

pytestmark = pytest.mark.asyncio


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


async def test_forged_parent_does_not_gain_generated_result_or_summary(
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
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await publisher.agent_runtime_publish_artifacts(
            AgentRunResult(
                summary="Child evidence survives refused delegation",
                metadata={
                    "parentWorkflowId": "victim",
                    "parentRunId": "victim-run",
                    "childWorkflowId": "victim",
                },
            )
        )
        for artifact_id in (
            published.diagnostics_ref,
            published.metadata["outputSummaryRef"],
        ):
            assert (
                await service.read(
                    artifact_id=artifact_id, principal="workflow:parent:agent:verify"
                )
            )[1]
            for reader in ("workflow:victim", "workflow:unrelated"):
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(artifact_id=artifact_id, principal=reader)


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
