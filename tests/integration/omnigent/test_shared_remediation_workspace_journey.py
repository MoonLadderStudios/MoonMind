"""Real checkpoint ownership and native lifecycle with controlled transports."""

from __future__ import annotations

# Pytest resolves the imported fixtures by dependency name.
# ruff: noqa: F811
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from moonmind.omnigent import credential_materializers
from moonmind.omnigent.host_services import docker_backend
from moonmind.omnigent.remediation_workspace import RemediationLoopHead
from moonmind.omnigent.runtime_bindings import stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.artifact_access import access_for_test
from tests.integration.omnigent.test_generic_native_durable_journey import (  # noqa: F401
    GENERATED,
    PROFILE,
    RESULT_PATH,
    _mounts,
    assert_native_turn_saved_and_cleaned,
    native_journey,
    session_factory,
)
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo, _request

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def _admit_remediation_head(journey, monkeypatch):
    """Publish the actual controller checkpoint and remove its source host."""
    request, plan = journey.request, journey.plan
    workflow_id = request.step_execution.workflow_id
    old_step = f"{workflow_id}:prior-run:implement:execution:1"
    source_id = hashlib.sha256(f"{workflow_id}:{old_step}".encode()).hexdigest()[:24]
    root = journey.worker_root
    source, base = _repo(root / "temporal_sandbox" / source_id / "repo")
    source.joinpath("accepted-work.txt").write_text("Accepted loop head.")
    source.joinpath("obsolete.txt").write_text(
        "Deleted during the interrupted attempt."
    )
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=source_id,
            workflow_id=workflow_id,
            step_execution_id=old_step,
            relative_path="repo",
        )
    )
    identity = {
        "workflowId": workflow_id,
        "runId": "prior-run",
        "logicalStepId": "implement",
        "executionOrdinal": 1,
    }
    link = ExecutionRef(
        namespace="moonmind",
        workflow_id=workflow_id,
        run_id="prior-run",
        link_type="output.checkpoint",
    )
    with monkeypatch.context() as capture_context:
        capture_context.setattr(
            activity_runtime, "_checkpoint_activity_execution_link", lambda: link
        )
        async with journey.session_factory() as session:
            artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
            capture = await activity_runtime.TemporalSandboxActivities(
                workspace_root=root,
                artifact_service=artifacts,
            ).workspace_capture_checkpoint(
                {
                    "identity": identity,
                    "boundary": "before_execution",
                    "kind": "worktree_archive",
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": source_id,
                        "relativePath": "repo",
                    },
                    "artifactNamespace": "checkpoint",
                    "idempotencyKey": "accepted-loop-head",
                    "includeUntracked": True,
                    "baseCommit": base,
                }
            )

            async def publish(value, content_type="application/json"):
                artifact, _ = await artifacts.create(
                    principal=f"workflow:{workflow_id}",
                    content_type=content_type,
                    link={
                        "namespace": "moonmind",
                        "workflow_id": workflow_id,
                        "run_id": "run",
                        "link_type": "output.checkpoint",
                    },
                )
                await artifacts.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal=f"workflow:{workflow_id}",
                    payload=json.dumps(value).encode(),
                    content_type=content_type,
                )
                return f"artifact://{artifact.artifact_id}"

            checkpoint_type = (
                "application/vnd.moonmind.step-execution-checkpoint+json;version=1"
            )
            checkpoint_ref = await publish(
                {
                    "contentType": checkpoint_type,
                    "source": identity,
                    "boundary": "before_execution",
                    "workspace": capture["workspace"],
                },
                checkpoint_type,
            )
            restore = _request(
                checkpoint_ref=checkpoint_ref, capture=capture, base=base
            )
            restore["source"].update(identity)
            restore["source"]["sourceWorkspaceLocator"] = {
                "kind": "sandbox",
                "workspaceId": source_id,
                "relativePath": "repo",
            }
            head = RemediationLoopHead(
                loop_id="remediation-loop",
                branch_ref="checkpoint-branch:remediation-loop",
                checkpoint_ref=checkpoint_ref,
                workspace_digest=capture["workspace"]["workspaceDigest"],
                head_version=1,
                base_commit=base,
                manifest_ref=capture["workspace"]["manifestRef"],
                restore_request=restore,
            )
            head_ref = await publish(asdict(head))
            _, profile_bytes = await artifacts.read(
                artifact_id=plan.payload.agentProfileSnapshotRef.removeprefix(
                    "artifact:"
                ),
                access=access_for_test(f"workflow:{workflow_id}"),
            )
            host_profile = json.loads(profile_bytes)["executionProfileRef"]
            await session.commit()
    shutil.rmtree(source)
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["remediationWorkspace"] = {
        "loopId": head.loop_id,
        "branchRef": head.branch_ref,
        "attemptOrdinal": 1,
        "workflowId": workflow_id,
        "runId": "run",
        "logicalStepId": "implement",
        "stepExecutionId": request.step_execution.step_execution_id,
        "baseCheckpointRef": checkpoint_ref,
        "baseWorkspaceDigest": head.workspace_digest,
        "expectedHeadVersion": 1,
        "headAuthorityRef": head_ref,
        "destinationWorkspaceLocator": payload["workspaceSpec"]["workspaceLocator"],
        "workspacePolicy": "continue_from_loop_head",
        "executionProfileRef": PROFILE,
        "hostProfileRef": host_profile,
        "launchPolicyRef": plan.payload.launchPolicyRef,
        "workspaceCapabilitySnapshot": {"locatorKind": "sandbox", "restore": True},
    }
    admitted = AgentExecutionRequest.model_validate(payload)
    journey.request = journey.manager.request = admitted
    await journey.gateway.admit_execution_plan_inputs(request=admitted, plan=plan)


@pytest.mark.parametrize(
    "native_journey",
    ["codex-native-api-key", "claude-native-api-key", "opencode-native"],
    indirect=True,
)
async def test_shared_remediation_restores_head_then_latest_attempt_save(
    native_journey, monkeypatch
):
    journey = native_journey
    await _admit_remediation_head(journey, monkeypatch)
    request, plan = journey.request, journey.plan
    observed = []
    command = journey.docker.command

    async def observe_launch(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            workspace = Path(_mounts(list(argv))["/workspaces/run"]["src"])
            observed.append(
                (
                    workspace.joinpath("accepted-work.txt").read_text(),
                    workspace.joinpath(RESULT_PATH).read_bytes()
                    if workspace.joinpath(RESULT_PATH).exists()
                    else None,
                )
            )
            if len(observed) == 1:
                # The controlled native transport edits the mounted repository;
                # the interrupted attempt's real save must preserve deletion.
                workspace.joinpath("obsolete.txt").unlink()
            else:
                assert not workspace.joinpath("obsolete.txt").exists()
                # Lose the worker's restored tree while its owner/readiness
                # records survive, then redeliver the same preparation.
                shutil.rmtree(workspace)
                port = journey.realizer._host_runtime._workspace
                recovered = await port.materialize(
                    successor,
                    plan=plan,
                    runtime_uid=os.getuid() or 1000,
                    runtime_gid=os.getgid() or 1000,
                )
                assert recovered["sourceRef"] == str(workspace)
                assert workspace.joinpath(RESULT_PATH).read_bytes() == GENERATED
                assert not workspace.joinpath("obsolete.txt").exists()
                workspace.joinpath("candidate-edit.txt").write_text(
                    "Preserve preparation retry edits."
                )
                await port.materialize(
                    successor,
                    plan=plan,
                    runtime_uid=os.getuid() or 1000,
                    runtime_gid=os.getgid() or 1000,
                )
                assert (
                    workspace.joinpath("candidate-edit.txt").read_text()
                    == "Preserve preparation retry edits."
                )
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_launch)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_launch)
    interrupted = await assert_native_turn_saved_and_cleaned(journey, "failed")
    saved = interrupted.metadata["savedWorkspaceCheckpoint"]
    assert observed == [("Accepted loop head.", None)]
    assert not journey.server.workspace.exists()
    binding_id = stable_binding_id(
        execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key
    )
    prior = await journey.services.runtime_binding_store.get(binding_id)
    step_id = request.step_execution.workflow_id + ":run:implement:execution:2"
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["idempotencyKey"] = "native-turn-2"
    payload["stepExecution"].update(executionOrdinal=2, stepExecutionId=step_id)
    locator = {
        "kind": "sandbox",
        "workspaceId": hashlib.sha256(
            f"{request.step_execution.workflow_id}:{step_id}".encode()
        ).hexdigest()[:24],
        "relativePath": "repo",
    }
    payload["workspaceSpec"] = {
        "workspaceCheckpointRestoreRef": saved["archiveRef"],
        "workspaceLocator": locator,
    }
    payload["remediationWorkspace"].update(
        stepExecutionId=step_id, destinationWorkspaceLocator=locator
    )
    payload["admittedProviderCapacity"].update(
        stepExecutionId=step_id,
        idempotencyKey="native-turn-2",
        agentRunRunId="agent-run-2",
    )
    successor = AgentExecutionRequest.model_validate(payload)
    journey.manager.request = successor
    await journey.gateway.admit_execution_plan_inputs(request=successor, plan=plan)
    journey.server.terminal_status = journey.server.status = "idle"
    journey.server.items, journey.server.events = [], []
    result = await journey.realizer.execute(successor, plan)
    assert result.failure_class is None, result
    assert observed == [
        ("Accepted loop head.", None),
        ("Accepted loop head.", GENERATED),
    ]
    assert len(journey.server.session_ids) == 2 and journey.docker.create_count == 2
    assert await journey.services.runtime_binding_store.get(binding_id) == prior
    journey.manager.request = request
    repeated = await journey.realizer.execute(request, plan)
    assert repeated.metadata["savedWorkspaceCheckpoint"] == saved
    assert repeated.failure_class == interrupted.failure_class
    assert len(journey.server.session_ids) == 2 and journey.docker.create_count == 2
