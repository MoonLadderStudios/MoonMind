"""Parent-produced saved work reaches a distinct verifier with its admitted input.

Git capture, canonical checkpoint publication, artifact access and cold restore
use their production owners. The Workflow/Activity transport is in process;
this exercises no provider, GitHub, Docker or live Temporal service.
"""

from __future__ import annotations

# Pytest resolves the imported storage fixture by dependency name.
# ruff: noqa: F811
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.schemas.temporal_models import STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ExecutionRef,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.artifact_access import access_for_test
from tests.integration.omnigent.test_codex_durable_skill_journey import (  # noqa: F401
    session_factory,
)
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo
from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
    _configure_workflow_runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True,
    ).strip()


@pytest.mark.parametrize(("legacy_publication", "altered_identity"), [
    (False, None), (False, "task-input"), (False, "workflow"), (True, None),
], ids=["new-publication", "foreign-input", "foreign-workflow", "legacy-publication"])
async def test_implementation_checkpoint_cold_restores_into_distinct_verifier(
    session_factory, monkeypatch, tmp_path, legacy_publication, altered_identity,
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow, "patched",
        lambda marker: not legacy_publication or not marker.startswith(
            run_module.RUN_OMNIGENT_STEP_INPUT_PROVENANCE_PATCH + ":"
        ),
    )
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    owner = run_module.MoonMindUserWorkflow()
    owner._input_ref = "artifact://authored/workflow-input"
    owner._plan_ref = "artifact://authored/workflow-plan"
    owner._initialize_step_ledger(
        ordered_nodes=[{"id": "implement"}, {"id": "verify"}],
        dependency_map={"implement": [], "verify": ["implement"]},
        updated_at=run_module.workflow.now(),
    )
    owner._mark_step_running(
        "implement", updated_at=run_module.workflow.now(), summary="Implement",
    )
    workflow_id = run_module.workflow.info().workflow_id
    run_id = run_module.workflow.info().run_id
    link = ExecutionRef(
        namespace="moonmind", workflow_id=workflow_id, run_id=run_id,
        link_type="output.checkpoint",
    )
    monkeypatch.setattr(activity_runtime, "_checkpoint_activity_execution_link", lambda: link)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=workflow_id, workflow_run_id=run_id,
    )

    async with session_factory() as session:
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))

        async def publish(payload: dict, content_type="application/json"):
            body = json.dumps(payload, sort_keys=True).encode()
            record, _ = await artifacts.create(
                principal="system", content_type=content_type, link=link,
            )
            await artifacts.write_complete(
                artifact_id=record.artifact_id, principal="system", payload=body,
                content_type=content_type,
            )
            await session.commit()
            return f"artifact://{record.artifact_id}", "sha256:" + hashlib.sha256(body).hexdigest()

        input_ref, input_digest = await publish({"instructions": "Implement then verify."})
        plan_ref, plan_digest = await publish({"taskInputSnapshotRef": input_ref})
        authored_input_ref, _ = await publish({"instructions": "Authored workflow input."})
        authored_plan_ref, _ = await publish({"taskInputSnapshotRef": authored_input_ref})
        owner._input_ref, owner._plan_ref = authored_input_ref, authored_plan_ref
        binding = {
            "taskInputSnapshotRef": input_ref,
            "taskInputSnapshotDigest": input_digest,
            "planRef": "omnigent-execution-plan:" + plan_digest,
            "planArtifactRef": plan_ref,
            "planDigest": plan_digest,
        }
        parameters = {"omnigentExecutionPlan": binding}
        implementation = owner._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "omnigent"}}, node_id="implement",
            tool_name="omnigent", workflow_parameters=parameters,
        )
        identity = implementation.step_execution
        checkpoint_identity = identity.model_dump(
            by_alias=True,
            include={"workflow_id", "run_id", "logical_step_id", "execution_ordinal"},
        )
        source_id = hashlib.sha256(
            f"{workflow_id}:{identity.step_execution_id}".encode()
        ).hexdigest()[:24]
        root = tmp_path / "worker"
        source, _ = _repo(root / "temporal_sandbox" / source_id / "repo")
        source.joinpath("deleted.txt").write_text("Delete this tracked file.\n")
        _git(source, "add", "deleted.txt")
        _git(source, "commit", "-m", "Baseline with tracked deletion")
        source.joinpath("accepted.txt").write_text("Completed implementation commit.\n")
        _git(source, "add", "accepted.txt")
        _git(source, "commit", "-m", "Completed implementation")
        head = _git(source, "rev-parse", "HEAD")
        source.joinpath("tracked.txt").write_text("Staged implementation work.\n")
        source.joinpath("deleted.txt").unlink()
        _git(source, "add", "-A")
        source.joinpath("tracked.txt").write_text("Unstaged implementation work.\n")
        source.joinpath("untracked.bin").write_bytes(b"\x00\xff\x01")
        locator = {"kind": "sandbox", "workspaceId": source_id, "relativePath": "repo"}
        SandboxWorkspaceRecordStore(root).ensure(SandboxWorkspaceRecord(
            workspace_id=source_id, workflow_id=workflow_id,
            step_execution_id=identity.step_execution_id, relative_path="repo",
        ))
        sandbox = activity_runtime.TemporalSandboxActivities(
            workspace_root=root, artifact_service=artifacts,
        )
        checkpoints = activity_runtime.TemporalCheckpointActivities(artifact_service=artifacts)
        capture = await environment.run(sandbox.workspace_capture_checkpoint, {
            "identity": checkpoint_identity, "boundary": "after_execution",
            "kind": "worktree_archive", "workspaceLocator": locator,
            "artifactNamespace": "checkpoint", "idempotencyKey": "implementation-save",
            "includeUntracked": True, "baseCommit": head,
        })
        saved = await environment.run(checkpoints.step_checkpoint_create, {
            "identity": checkpoint_identity, "boundary": "after_execution",
            "taskInputSnapshotRef": input_ref, "planRef": plan_ref, "planDigest": plan_digest,
            "workspace": capture["workspace"], "createdAt": run_module.workflow.now().isoformat(),
            "idempotencyKey": "implementation-after-execution",
        })

        async def read(activity, payload, **kwargs):
            assert activity == "artifact.read"
            _, body = await artifacts.read(
                artifact_id=payload.artifact_ref.removeprefix("artifact:").removeprefix("//"),
                access=access_for_test(f"workflow:{workflow_id}"),
            )
            return body

        activity_calls = []

        async def execute(activity, payload, **kwargs):
            activity_calls.append(activity)
            if activity == "workspace.capture_checkpoint":
                return await environment.run(sandbox.workspace_capture_checkpoint, payload)
            assert activity == "step_checkpoint.create"
            return await environment.run(checkpoints.step_checkpoint_create, payload)

        monkeypatch.setattr(run_module, "execute_typed_activity", read)
        monkeypatch.setattr(run_module.workflow, "execute_activity", execute)
        result = AgentRunResult(summary="Implementation complete", metadata={
            "workPreserved": True,
            "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "a" * 64,
            "savedWorkspaceCheckpoint": {**capture["workspace"], "checkpointRef": saved["checkpointRef"]},
        })
        assert await owner._adopt_shared_child_saved_checkpoint("implement", implementation, result)
        owner._step_workspace_capture_inputs["implement"] = {
            "captureAuthority": "moonmind_sandbox", "kind": "worktree_archive",
            "workspaceLocator": locator, "baseCommit": head,
        }
        # A later parent projection is unrelated to the completed attempt's
        # immutable input/plan binding and cannot relabel its next boundary.
        owner._input_ref, _ = await publish({"instructions": "Later workflow projection."})
        owner._plan_ref, _ = await publish({"taskInputSnapshotRef": owner._input_ref})
        assert not await owner._record_prepublication_checkpoint(
            "implement", publish_mode="pr", updated_at=run_module.workflow.now(),
        )
        publication_ref = owner._step_checkpoint_refs["implement"]
        _, body = await artifacts.read(
            artifact_id=publication_ref.removeprefix("artifact:").removeprefix("//"),
            access=access_for_test(f"workflow:{workflow_id}"),
        )
        descriptor = json.loads(body)
        with pytest.raises(TemporalArtifactAuthorizationError):
            await artifacts.read(
                artifact_id=publication_ref.removeprefix("artifact:").removeprefix("//"),
                access=ArtifactAccessContext.for_execution("foreign-workflow"),
            )
        assert descriptor["boundary"] == "before_publication"
        context = implementation.parameters["metadata"]["moonmind"]["executionContext"]
        if legacy_publication:
            assert context["taskInputSnapshotRef"] == authored_input_ref
            assert context["planRef"] == authored_plan_ref
            assert context.get("planDigest") is None
        else:
            for field, expected in (("taskInputSnapshotRef", input_ref), ("planRef", plan_ref), ("planDigest", plan_digest)):
                assert context[field] == expected
                assert descriptor[field] == expected
        if legacy_publication:
            assert descriptor["taskInputSnapshotRef"] == owner._input_ref
        assert descriptor["workspace"]["workspaceDigest"] == capture["workspace"]["workspaceDigest"]
        assert activity_calls == ["workspace.capture_checkpoint", "step_checkpoint.create"]
        if altered_identity:
            if altered_identity == "task-input":
                descriptor["taskInputSnapshotRef"] = owner._input_ref
            else:
                descriptor["source"]["workflowId"] = "foreign-workflow"
                descriptor["checkpointId"] = descriptor["checkpointId"].replace(
                    workflow_id, "foreign-workflow", 1,
                )
            publication_ref, _ = await publish(descriptor, STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE)
        shutil.rmtree(source)

        monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
        owner._step_ledger_row_for("implement")["status"] = "completed"
        owner._mark_step_running("verify", updated_at=run_module.workflow.now(), summary="Verify")
        verifier = owner._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "omnigent"}}, node_id="verify",
            tool_name="omnigent", workflow_parameters=parameters,
        )
        verifier_id = hashlib.sha256(
            f"{workflow_id}:{verifier.step_execution.step_execution_id}".encode()
        ).hexdigest()[:24]
        request = verifier.model_dump(mode="json", by_alias=True, exclude_none=True)
        request["inputRefs"] = []
        request["workspaceSpec"] = {
            "workspaceCheckpointRestoreRef": publication_ref,
            "workspaceLocator": {"kind": "sandbox", "workspaceId": verifier_id, "relativePath": "repo"},
        }
        verifier = AgentExecutionRequest.model_validate(request)
        owner._remediation_loop_spec = object()
        owner._remediation_loop_state = SimpleNamespace(
            phase=run_module.RemediationLoopPhase.INITIAL_VERIFICATION_PENDING,
        )
        owner._remediation_loop_runtime = {"mode": "omnigent"}
        selected_ref = owner._initial_omnigent_verification_checkpoint_restore_ref(
            node={"id": "verify", "tool": {"name": "omnigent"}, "inputs": {
                "runtime": {"mode": "omnigent"}, "selectedSkill": "moonspec-verify",
            }},
            request=verifier,
        )
        assert selected_ref == (saved["checkpointRef"] if legacy_publication else publication_ref)
        verifier.workspace_spec["workspaceCheckpointRestoreRef"] = selected_ref
        destination = root / "temporal_sandbox" / verifier_id / "repo"

        async def no_transport(*args, **kwargs):
            raise AssertionError("self-contained saved work must not clone or launch a provider")

        materializer = OmnigentWorkspaceMaterializer(
            command_runner=no_transport, workspace_root=root, artifact_service=artifacts,
        )
        plan = SimpleNamespace(planRef=binding["planRef"], payload=SimpleNamespace(
            authority=SimpleNamespace(taskInputSnapshotRef=input_ref), resolvedTools={},
        ))
        if altered_identity:
            with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_SOURCE_IDENTITY_MISMATCH"):
                await materializer.materialize(verifier, plan=plan, runtime_uid=os.getuid(), runtime_gid=os.getgid())
            assert not destination.exists()
            return
        attachment = await materializer.materialize(
            verifier, plan=plan, runtime_uid=os.getuid(), runtime_gid=os.getgid(),
        )
        assert Path(attachment["sourceRef"]) == destination
        assert _git(destination, "rev-parse", "HEAD") == head
        assert _git(destination, "show", ":tracked.txt") == "Staged implementation work."
        assert destination.joinpath("tracked.txt").read_text() == "Unstaged implementation work.\n"
        assert destination.joinpath("accepted.txt").read_text() == "Completed implementation commit.\n"
        assert destination.joinpath("untracked.bin").read_bytes() == b"\x00\xff\x01"
        assert not destination.joinpath("deleted.txt").exists()
        assert _git(destination, "diff", "--cached", "--name-only").splitlines() == ["deleted.txt", "tracked.txt"]
        assert not source.exists()
