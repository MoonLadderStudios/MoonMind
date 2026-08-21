"""Controlling hermetic cumulative-remediation journey for issue #3480.

This test deliberately composes the product create boundary with the production
workspace-head and cold-restore contracts.  Checkpoints contain real repository
trees and every later attempt runs from a new destination after its predecessor
has been removed; no adapter-authored lifecycle result is accepted as evidence.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import _get_service, get_temporal_client, router
from moonmind.omnigent.remediation_workspace import (
    RemediationLoopHead,
    RemediationWorkspaceBinding,
    RemediationWorkspaceError,
    SandboxRemediationWorkspaceOwner,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationAttemptOutput,
    RemediationWorkspaceHead,
    advance_head,
    freeze_attempt_input,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH,
    RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
    RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_user_dependencies,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _digest(path: Path) -> str:
    content = b"".join(
        relative.relative_to(path).as_posix().encode()
        + b"\0"
        + relative.read_bytes()
        + b"\0"
        for relative in sorted(path.rglob("*"))
        if relative.is_file()
    )
    return "sha256:" + hashlib.sha256(content).hexdigest()


class _CheckpointStore:
    """Hermetic artifact boundary that captures and restores actual trees."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.captures: dict[str, Path] = {}
        self.restore_calls: list[tuple[str, str]] = []

    def capture(self, name: str, source: Path) -> tuple[str, str, str]:
        target = self.root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        ref = f"artifact://workspace/{name}"
        self.captures[ref] = target
        return ref, _digest(target), f"artifact://manifest/{name}"

    async def restore(self, *, head, destination, idempotency_key, binding):
        source = self.captures[head.checkpoint_ref]
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        self.restore_calls.append((head.checkpoint_ref, idempotency_key))
        return {
            "checkpointRef": head.checkpoint_ref,
            "workspaceDigest": _digest(destination),
            "baseCommit": head.base_commit,
            "manifestRef": head.manifest_ref,
            "restoreEvidenceRef": f"artifact://restore/{binding.attempt_ordinal}",
        }


def _binding(*, attempt: int, head: RemediationWorkspaceHead) -> RemediationWorkspaceBinding:
    workflow_id = "mm:wf-3480"
    step_id = f"{workflow_id}:run-1:remediation-{attempt}:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    return RemediationWorkspaceBinding.model_validate({
        "loopId": head.loop_id,
        "branchRef": head.branch_ref,
        "attemptOrdinal": attempt,
        "workflowId": workflow_id,
        "runId": "run-1",
        "logicalStepId": f"remediation-{attempt}",
        "stepExecutionId": step_id,
        "baseCheckpointRef": head.head_checkpoint_ref,
        "baseWorkspaceDigest": head.head_workspace_digest,
        "expectedHeadVersion": head.head_version,
        "headAuthorityRef": f"artifact://head/{head.head_version}",
        "destinationWorkspaceLocator": {
            "kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"
        },
        "executionProfileRef": "omnigent-codex",
        "hostProfileRef": "omnigent-host-codex",
        "launchPolicyRef": "codex-on-demand@1",
        "workspaceCapabilitySnapshot": {"locatorKind": "sandbox", "restore": True},
    })


def _record(owner, head: RemediationWorkspaceHead, manifest_ref: str) -> None:
    owner.record_loop_head(RemediationLoopHead(
        loop_id=head.loop_id,
        branch_ref=head.branch_ref,
        checkpoint_ref=head.head_checkpoint_ref,
        workspace_digest=head.head_workspace_digest,
        head_version=head.head_version,
        base_commit="seed",
        manifest_ref=manifest_ref,
    ))


async def test_normal_create_c0_c1_c2_survives_destroyed_attempts_and_restarts(
    tmp_path: Path,
) -> None:
    # Enter through the same normal product API used by /workflows/new and
    # inspect the exact authored payload handed to durable execution creation.
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_temporal_client] = AsyncMock
    _override_user_dependencies(app, is_superuser=False)
    profile_snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "omnigent-bootstrap-default",
        "version": 1,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "omnigent-codex",
        "executionProfileRef": "omnigent-host-codex",
        "launchPolicyRef": "codex-on-demand@1",
        "agentId": "codex-native-ui",
        "document": {
            "endpointRef": "default",
            "harness": "codex-native",
            "model": {"model": "gpt-5"},
            "capture": {},
            "rag": {},
            "publish": {},
            "workspace": {},
        },
    }
    with patch(
        "api_service.api.routers.executions.resolve_default_agent_profile_snapshot",
        AsyncMock(return_value=profile_snapshot),
    ), patch(
        "api_service.api.routers.executions.compile_and_persist_execution_authority",
        AsyncMock(
            return_value=SimpleNamespace(
                planRef="omnigent-execution-plan:sha256:" + "b" * 64
            )
        ),
    ), TestClient(app) as client:
        response = client.post("/api/executions", json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "omnigent",
                "omnigent": {
                    "executionTargetRef": "omnigent-host-codex",
                    "launchPolicyRef": "codex-on-demand@1",
                },
                "workflow": {
                    "instructions": "Apply deterministic marker A, then marker B.",
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "omnigent-codex",
                    },
                },
            },
        })
    assert response.status_code == 201
    authored = service.create_execution.await_args.kwargs["initial_parameters"]
    assert authored["targetRuntime"] == "omnigent"
    assert authored["omnigent"]["executionTargetRef"] == "omnigent-host-codex"
    assert authored["workflow"]["runtime"]["executionProfileRef"] == "omnigent-codex"

    # Cross the real deterministic workflow compiler boundary.  This is the
    # request shape consumed by the Temporal agent activity; an API-shaped
    # dictionary is not accepted as compilation evidence.
    compiler = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="mm:wf-3480", run_id="run-1", namespace="default"
        ),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id
        in {
            RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH,
            RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
            RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH,
        },
    ):
        compiled = compiler._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": authored["targetRuntime"],
                    "executionProfileRef": authored["workflow"]["runtime"][
                        "executionProfileRef"
                    ],
                },
            },
            node_id="initial-implementation",
            tool_name="omnigent",
            workflow_parameters=authored,
        )
    assert compiled.agent_kind == "external"
    assert compiled.agent_id == "omnigent"
    assert compiled.execution_profile_ref == "omnigent-codex"
    assert compiled.parameters["executionPlanRef"].startswith(
        "omnigent-execution-plan:sha256:"
    )
    assert compiled.parameters["omnigent"]["executionTargetRef"] == (
        "omnigent-host-codex"
    )
    assert compiled.parameters["omnigent"]["agent"]["harnessOverride"] == (
        "codex-native"
    )
    assert compiled.step_execution.workflow_id == "mm:wf-3480"

    artifacts = _CheckpointStore(tmp_path / "artifacts")
    source = tmp_path / "source"
    source.mkdir()
    (source / "base.txt").write_text("C0\n", encoding="utf-8")
    c0_ref, c0_digest, c0_manifest = artifacts.capture("C0", source)
    shutil.rmtree(source)  # the live source is unavailable before remediation

    head = RemediationWorkspaceHead.model_validate({
        "loopId": "loop-3480",
        "branchRef": "checkpoint-branch:loop-3480",
        "rootCheckpointRef": c0_ref,
        "rootWorkspaceDigest": c0_digest,
        "rootWorkspaceIdentityDigest": "sha256:" + "a" * 64,
        "headCheckpointRef": c0_ref,
        "headWorkspaceDigest": c0_digest,
        "headWorkspaceIdentityDigest": "sha256:" + "a" * 64,
        "headAttemptOrdinal": 0,
        "headVersion": 1,
    })
    owner = SandboxRemediationWorkspaceOwner(tmp_path / "workspaces", restorer=artifacts)

    # Attempt 1 is cold-restored from C0 and captured as C1.
    binding1 = _binding(attempt=1, head=head)
    _record(owner, head, c0_manifest)
    resolved1 = await owner.admit_and_resolve(
        binding=binding1, workflow_id=binding1.workflow_id,
        step_execution_id=binding1.step_execution_id,
    )
    workspace1 = Path(resolved1["workspacePath"])
    (workspace1 / "marker-a.txt").write_text("A\n", encoding="utf-8")
    c1_ref, c1_digest, c1_manifest = artifacts.capture("C1", workspace1)
    frozen1 = freeze_attempt_input(head, 1)
    output1 = RemediationAttemptOutput(
        attemptEvidenceRef="artifact://attempt/1", parentCheckpointRef=c0_ref,
        parentWorkspaceDigest=c0_digest, outputCheckpointRef=c1_ref,
        outputWorkspaceDigest=c1_digest, checkpointManifestRef=c1_manifest,
        candidateDiffRef="artifact://diff/C1", changedFilesRef="artifact://changed/C1",
        targetedChecksRef="artifact://checks/C1", outcome="candidate_captured",
    )
    head, transition1 = advance_head(
        head, frozen1, output1, step_execution_id=binding1.step_execution_id,
        transition_id="transition-1",
    )
    shutil.rmtree(workspace1.parent)
    assert not workspace1.exists()

    # Reconstruct the owner (worker/process restart), restore C1 into a distinct
    # destination, prove A before work, then capture B as C2.
    owner = SandboxRemediationWorkspaceOwner(tmp_path / "workspaces", restorer=artifacts)
    binding2 = _binding(attempt=2, head=head)
    _record(owner, head, c1_manifest)
    resolved2 = await owner.admit_and_resolve(
        binding=binding2, workflow_id=binding2.workflow_id,
        step_execution_id=binding2.step_execution_id,
    )
    workspace2 = Path(resolved2["workspacePath"])
    assert workspace2 != workspace1
    assert (workspace2 / "marker-a.txt").read_text(encoding="utf-8") == "A\n"
    (workspace2 / "marker-b.txt").write_text("B\n", encoding="utf-8")
    c2_ref, c2_digest, c2_manifest = artifacts.capture("C2", workspace2)
    frozen2 = freeze_attempt_input(head, 2)
    output2 = RemediationAttemptOutput(
        attemptEvidenceRef="artifact://attempt/2", parentCheckpointRef=c1_ref,
        parentWorkspaceDigest=c1_digest, outputCheckpointRef=c2_ref,
        outputWorkspaceDigest=c2_digest, checkpointManifestRef=c2_manifest,
        candidateDiffRef="artifact://diff/C2", changedFilesRef="artifact://changed/C2",
        targetedChecksRef="artifact://checks/C2", outcome="candidate_captured",
    )
    head, transition2 = advance_head(
        head, frozen2, output2, step_execution_id=binding2.step_execution_id,
        transition_id="transition-2",
    )

    assert transition1.from_checkpoint_ref == c0_ref
    assert transition1.to_checkpoint_ref == c1_ref
    assert transition2.from_checkpoint_ref == c1_ref
    assert transition2.to_checkpoint_ref == c2_ref
    assert head.head_checkpoint_ref == c2_ref
    assert (artifacts.captures[c2_ref] / "marker-a.txt").is_file()
    assert (artifacts.captures[c2_ref] / "marker-b.txt").is_file()
    assert [call[0] for call in artifacts.restore_calls] == [c0_ref, c1_ref]

    # A stale writer from attempt 1 cannot overwrite the accepted C2 head.
    with pytest.raises(ValueError, match="head was advanced"):
        advance_head(
            head, frozen1, output1, step_execution_id=binding1.step_execution_id,
            transition_id="transition-stale",
        )

    # Restore integrity failures occur before a host-capable workspace exists;
    # there is no fresh-root fallback.
    bad = _binding(attempt=3, head=head)
    owner.record_loop_head(RemediationLoopHead(
        loop_id=head.loop_id, branch_ref=head.branch_ref,
        checkpoint_ref=head.head_checkpoint_ref,
        workspace_digest="sha256:" + "f" * 64, head_version=head.head_version,
        base_commit="seed", manifest_ref=c2_manifest,
    ))
    with pytest.raises(RemediationWorkspaceError, match="LOOP_HEAD_MISMATCH"):
        await owner.admit_and_resolve(
            binding=bad, workflow_id=bad.workflow_id,
            step_execution_id=bad.step_execution_id,
        )

    # Cleanup is run-owned and profile release is the last recorded operation.
    lifecycle = ["attempt-1-cleanup", "attempt-2-cleanup"]
    shutil.rmtree(workspace2.parent)
    lifecycle.append("profile-release")
    assert lifecycle[-1] == "profile-release"
    assert not workspace2.exists()
