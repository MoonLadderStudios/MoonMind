"""Terminal evidence stays readable across the AgentRun/parent boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from temporalio import activity, workflow
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceResponse,
    DescribeWorkflowExecutionResponse,
)

from api_service.db.models import TemporalArtifactRedactionLevel
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.temporal.activity_runtime import (
    ArtifactParentAuthorityUnavailable,
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def handoff(tmp_path, monkeypatch):
    info = SimpleNamespace(
        namespace="default",
        workflow_id="parent:agent:node-1",
        workflow_run_id="child-run",
    )
    server = SimpleNamespace(
        describe_workflow_execution=AsyncMock(
            return_value=DescribeWorkflowExecutionResponse(
                workflow_execution_info={
                    "execution": {
                        "workflow_id": info.workflow_id,
                        "run_id": "child-run",
                    },
                    "parent_execution": {
                        "workflow_id": "parent",
                        "run_id": "parent-run",
                    },
                    "parent_namespace_id": "namespace-default",
                }
            )
        ),
        describe_namespace=AsyncMock(
            return_value=DescribeNamespaceResponse(
                namespace_info={"id": "namespace-default"}
            )
        ),
    )
    monkeypatch.setattr(activity, "info", lambda: info)
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(namespace="default", workflow_service=server),
    )
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="parent", namespace="default"),
    )
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(artifact_service=service)
        monkeypatch.setattr(publisher, "execution_notify_completion", AsyncMock())

        async def read_activity(name, payload, **kwargs):
            assert name == "artifact.read"
            assert payload.principal == "workflow:parent"
            return await TemporalArtifactActivities(service).artifact_read(payload)

        monkeypatch.setattr(run_module, "execute_typed_activity", read_activity)
        yield SimpleNamespace(
            service=service,
            publisher=publisher,
            info=info,
            server=server,
            workspace=tmp_path / "repo",
        )


async def _evaluate(handoff, disposition="merged", *, auto=False):
    execution_ref = "parent:parent-run:node-1:execution:1"
    no_op = disposition == "review_clean"
    publish = {
        "schemaVersion": "moonmind.publish.auto.v1",
        "mode": "auto",
        "owner": "agent",
        "skillId": "fix-comments" if auto else "pr-resolver",
        "executionRef": execution_ref,
        "status": "no_op_verified" if no_op else "verified",
        "action": "none" if no_op else "push" if auto else "merge",
        "repository": "org/repo",
        "branch": "feature",
        "localHead": "a" * 40,
        "remoteBranchHead": "a" * 40 if auto or no_op else None,
        "remoteVerified": True,
        "pushed": auto and not no_op,
        "merged": not auto and not no_op,
        "prUrl": "https://github.com/org/repo/pull/1",
        "verificationCommands": ["gh pr view 1"],
    }
    publish_path = handoff.workspace / "artifacts/publish_result.json"
    publish_path.parent.mkdir(parents=True)
    publish_path.write_text(json.dumps(publish))
    contract = {
        "contractId": "auto_publish_terminal.v1" if auto else "pr_resolver_terminal.v1",
        "relativePath": (
            "artifacts/publish_result.json" if auto else "var/pr_resolver/result.json"
        ),
        "executionRef": execution_ref,
    }
    if not auto:
        terminal_path = handoff.workspace / "var/pr_resolver/result.json"
        terminal_path.parent.mkdir(parents=True)
        terminal_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "executionRef": execution_ref,
                    "mergeAutomationDisposition": disposition,
                    "status": "completed" if no_op else "merged",
                }
            )
        )
    evaluated = await handoff.publisher.agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(handoff.workspace),
            "terminalContract": contract,
            "result": {
                "summary": "Verified result.",
                "metadata": {
                    "parentWorkflowId": "parent",
                    "parentRunId": "parent-run",
                    "parentTemporalRunId": "parent-run",
                },
            },
        }
    )
    assert evaluated.failure_class is None
    assert evaluated.metadata["terminalContractSatisfied"] is True
    return evaluated


@pytest.mark.parametrize(
    "auto,disposition",
    [
        (False, "merged"),
        (False, "already_merged"),
        (False, "review_clean"),
        (True, "merged"),
        (True, "review_clean"),
    ],
)
async def test_parent_finalizes_from_child_terminal_evidence(
    handoff, auto, disposition
):
    evaluated = await _evaluate(handoff, disposition, auto=auto)
    refs = {
        evaluated.metadata[key]
        for key in ("terminalContractEvidenceRef", "publishEvidence")
    }
    originals = {}
    for ref in refs:
        artifact, payload = await handoff.service.read(
            artifact_id=ref, principal=f"workflow:{handoff.info.workflow_id}"
        )
        originals[ref] = (artifact.sha256, artifact.created_by_principal, payload)
        with pytest.raises(TemporalArtifactAuthorizationError):
            await handoff.service.read(artifact_id=ref, principal="workflow:parent")

    for _ in range(2):  # Publication retries reuse evidence and idempotent links.
        published = await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
        parent = run_module.MoonMindRunWorkflow()
        await parent._record_publish_result_from_execution(
            parameters={"publishMode": "auto"},
            execution_result=published.model_dump(by_alias=True),
        )
        assert parent._publish_status == (
            "not_required" if disposition == "review_clean" else "published"
        )
        assert (
            parent._publish_context["evidenceRef"]
            == evaluated.metadata["publishEvidence"]
        )
        for ref, (sha256, owner, payload) in originals.items():
            artifact, received = await handoff.service.read(
                artifact_id=ref, principal="workflow:parent"
            )
            assert received == payload
            assert artifact.created_by_principal == owner
            assert artifact.sha256 == sha256
            links = await handoff.service._repository.list_links(ref)
            assert len([link for link in links if link.workflow_id == "parent"]) == 1
            with pytest.raises(TemporalArtifactAuthorizationError):
                await handoff.service.read(
                    artifact_id=ref, principal="workflow:sibling"
                )


@pytest.mark.parametrize(
    "source", ["other_workflow", "other_run", "other_namespace", "input"]
)
async def test_handoff_rejects_evidence_outside_this_child_output(handoff, source):
    artifact, _ = await handoff.service.create(
        principal="system:agent_runtime",
        content_type="application/json",
        link=ExecutionRef(
            namespace="other" if source == "other_namespace" else "default",
            workflow_id=(
                "unrelated" if source == "other_workflow" else handoff.info.workflow_id
            ),
            run_id="other-run" if source == "other_run" else "child-run",
            link_type=(
                "input.attachment" if source == "input" else "output.publish_evidence"
            ),
        ),
    )
    await handoff.service.write_complete(
        artifact_id=artifact.artifact_id,
        principal="system:agent_runtime",
        payload=b"{}",
    )
    result = AgentRunResult(
        metadata={
            "parentWorkflowId": "parent",
            "parentRunId": "parent-run",
            "publishEvidence": artifact.artifact_id,
        }
    )
    with pytest.raises(
        (TemporalActivityRuntimeError, TemporalArtifactAuthorizationError)
    ):
        await handoff.publisher.agent_runtime_publish_artifacts(result)
    links = await handoff.service._repository.list_links(artifact.artifact_id)
    assert not any(link.workflow_id == "parent" for link in links)
    handoff.publisher.execution_notify_completion.assert_not_awaited()


async def test_forged_parent_does_not_gain_terminal_evidence(handoff):
    evaluated = await _evaluate(handoff)
    evaluated.metadata.update(parentWorkflowId="unrelated", parentRunId="unrelated-run")
    await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    for key in ("terminalContractEvidenceRef", "publishEvidence"):
        ref = evaluated.metadata[key]
        links = await handoff.service._repository.list_links(ref)
        assert {link.workflow_id for link in links} == {handoff.info.workflow_id}
        with pytest.raises(TemporalArtifactAuthorizationError):
            await handoff.service.read(artifact_id=ref, principal="workflow:unrelated")


async def test_parent_lookup_retry_preserves_completed_evidence(handoff, monkeypatch):
    evaluated = await _evaluate(handoff)
    described = handoff.server.describe_workflow_execution.return_value
    handoff.server.describe_workflow_execution.side_effect = [
        RuntimeError("unavailable"),
        described,
    ]
    creates = AsyncMock(wraps=handoff.service.create)
    monkeypatch.setattr(handoff.service, "create", creates)
    with pytest.raises(ArtifactParentAuthorityUnavailable):
        await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    creates.assert_not_awaited()
    handoff.publisher.execution_notify_completion.assert_not_awaited()
    published = await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    assert (
        published.metadata["publishEvidence"] == evaluated.metadata["publishEvidence"]
    )
    assert (
        await handoff.service.read(
            artifact_id=published.metadata["publishEvidence"],
            principal="workflow:parent",
        )
    )[1]


async def test_partial_handoff_retry_keeps_original_evidence(handoff, monkeypatch):
    evaluated = await _evaluate(handoff)
    link = handoff.service.link_artifact
    failed_once = False

    async def interrupted_link(**kwargs):
        nonlocal failed_once
        if (
            kwargs["execution_ref"].link_type == "input.publish_evidence_handoff"
            and not failed_once
        ):
            failed_once = True
            raise RuntimeError("link temporarily unavailable")
        return await link(**kwargs)

    monkeypatch.setattr(handoff.service, "link_artifact", interrupted_link)
    with pytest.raises(RuntimeError, match="link temporarily unavailable"):
        await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    handoff.publisher.execution_notify_completion.assert_not_awaited()
    published = await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    for key in ("terminalContractEvidenceRef", "publishEvidence"):
        assert published.metadata[key] == evaluated.metadata[key]
        links = await handoff.service._repository.list_links(evaluated.metadata[key])
        assert len([link for link in links if link.workflow_id == "parent"]) == 1


@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
async def test_parent_handoff_preserves_raw_read_policy(handoff, protection):
    evaluated = await _evaluate(handoff)
    ref = evaluated.metadata["publishEvidence"]
    artifact = await handoff.service._repository.get_artifact(ref)
    if protection == "restricted":
        artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        denied = TemporalArtifactAuthorizationError
    else:
        artifact.metadata_json = {**artifact.metadata_json, "quarantine": True}
        denied = TemporalArtifactStateError
    await handoff.service._repository.commit()
    await handoff.publisher.agent_runtime_publish_artifacts(evaluated)
    links = await handoff.service._repository.list_links(ref)
    assert any(link.workflow_id == "parent" for link in links)
    with pytest.raises(denied):
        await handoff.service.read(artifact_id=ref, principal="workflow:parent")
