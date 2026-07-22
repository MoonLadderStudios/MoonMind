from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from moonmind.schemas.workspace_locator_models import (
    WORKSPACE_LOCATOR_ADAPTER,
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WorkspaceLocatorResolutionError,
)
from moonmind.schemas.temporal_models import WorkspaceCheckpointCaptureInput
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
    daemon_visible_workspace_path,
    resolve_managed_workspace_locator,
    resolve_sandbox_workspace_locator,
)


@pytest.mark.parametrize(
    ("payload", "model_type"),
    [
        ({"kind": "sandbox", "workspaceId": "ws-1"}, SandboxWorkspaceLocator),
        (
            {"kind": "managed_runtime", "runtimeId": "codex", "agentRunId": "run-1"},
            ManagedWorkspaceLocator,
        ),
        ({"kind": "external_state", "artifactRef": "artifact:1"}, ExternalStateLocator),
    ],
)
def test_workspace_locator_discriminator(payload, model_type):
    locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(payload)
    assert isinstance(locator, model_type)
    serialized = locator.model_dump(by_alias=True, mode="json")
    assert "/work/" not in str(serialized)


@pytest.mark.parametrize("relative_path", ["../repo", "/repo", "repo/../secret", ""])
def test_workspace_locator_rejects_unsafe_relative_path(relative_path):
    with pytest.raises(ValidationError):
        SandboxWorkspaceLocator(workspaceId="ws-1", relativePath=relative_path)


@pytest.mark.parametrize(
    "relative_path", ["%2e%2e/repo", "%252e%252e/repo", "repo%2f..%2fsecret"]
)
def test_workspace_locator_rejects_encoded_traversal(relative_path):
    with pytest.raises(ValidationError, match="percent-encoding"):
        SandboxWorkspaceLocator(workspaceId="ws-1", relativePath=relative_path)


def test_workspace_locator_rejects_unknown_discriminator():
    with pytest.raises(ValidationError):
        WORKSPACE_LOCATOR_ADAPTER.validate_python({"kind": "host", "path": "/tmp"})


def test_sandbox_locator_resolves_default_repo_subpath(tmp_path):
    repo = tmp_path / "temporal_sandbox" / "ws-1" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(workspace_root=tmp_path)

    resolved = activities._resolve_sandbox_locator(
        SandboxWorkspaceLocator(workspaceId="ws-1"), must_exist=True
    )

    assert resolved == repo.resolve()


def test_sandbox_locator_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "temporal_sandbox" / "ws-1"
    outside = tmp_path / "outside"
    workspace.mkdir(parents=True)
    outside.mkdir()
    (workspace / "repo").symlink_to(outside, target_is_directory=True)
    activities = TemporalSandboxActivities(workspace_root=tmp_path)

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        activities._resolve_sandbox_locator(
            SandboxWorkspaceLocator(workspaceId="ws-1"), must_exist=True
        )

    assert exc.value.code == "WORKSPACE_AUTHORITY_MISMATCH"


def test_owner_side_sandbox_resolution_rejects_cross_run_and_symlink(tmp_path):
    locator = SandboxWorkspaceLocator(workspaceId="owned")
    repo = tmp_path / "temporal_sandbox" / "owned" / "repo"
    repo.mkdir(parents=True)
    assert resolve_sandbox_workspace_locator(
        locator, workspace_root=tmp_path, expected_workspace_id="owned"
    ) == repo.resolve()

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_sandbox_workspace_locator(
            locator, workspace_root=tmp_path, expected_workspace_id="other"
        )
    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_sandbox_owner_record_is_durable_and_idempotent(tmp_path):
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = SandboxWorkspaceRecord(
        workspace_id="owned",
        workflow_id="workflow-1",
        step_execution_id="step-1",
        relative_path="repo",
    )

    store.ensure(record)
    store.ensure(record)

    assert store.load("owned") == record


def test_sandbox_owner_record_rejects_cross_step_retry(tmp_path):
    store = SandboxWorkspaceRecordStore(tmp_path)
    store.ensure(
        SandboxWorkspaceRecord(
            workspace_id="owned",
            workflow_id="workflow-1",
            step_execution_id="step-1",
            relative_path="repo",
        )
    )

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        store.ensure(
            SandboxWorkspaceRecord(
                workspace_id="owned",
                workflow_id="workflow-1",
                step_execution_id="step-2",
                relative_path="repo",
            )
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_sandbox_resolution_rejects_mismatched_owner_record(tmp_path):
    locator = SandboxWorkspaceLocator(workspaceId="owned")
    record = SandboxWorkspaceRecord(
        workspace_id="owned",
        workflow_id="other-workflow",
        step_execution_id="step-1",
        relative_path="repo",
    )

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_sandbox_workspace_locator(
            locator,
            workspace_root=tmp_path,
            expected_workspace_id="owned",
            owner_record=record,
            expected_workflow_id="workflow-1",
            expected_step_execution_id="step-1",
            must_exist=False,
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_daemon_visible_workspace_translation_is_deployment_owned(tmp_path, monkeypatch):
    worker_root = tmp_path / "worker"
    workspace = worker_root / "temporal_sandbox" / "owned" / "repo"
    workspace.mkdir(parents=True)
    daemon_root = tmp_path / "daemon"
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(worker_root))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", str(daemon_root))

    assert daemon_visible_workspace_path(workspace) == (
        daemon_root / "temporal_sandbox" / "owned" / "repo"
    )


def test_external_locator_satisfies_external_checkpoint_input():
    model = WorkspaceCheckpointCaptureInput.model_validate(
        {
            "identity": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "step-1",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "kind": "external_state_ref",
            "workspaceLocator": {
                "kind": "external_state",
                "artifactRef": "artifact:state-1",
            },
            "artifactNamespace": "checkpoints/step-1",
            "idempotencyKey": "checkpoint-1",
        }
    )
    assert isinstance(model.workspace_locator, ExternalStateLocator)


def test_managed_locator_requires_current_identity_and_store_record(tmp_path):
    workspace = tmp_path / "workspaces" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    record = SimpleNamespace(
        run_id="run-1", runtime_id="codex", workspace_path=str(workspace)
    )
    store = SimpleNamespace(
        store_root=tmp_path / "managed_runs", load=lambda run_id: record
    )
    locator = ManagedWorkspaceLocator(
        runtimeId="codex", agentRunId="run-1", relativePath="repo"
    )

    assert resolve_managed_workspace_locator(
        locator,
        store=store,
        current_agent_run_id="run-1",
        current_runtime_id="codex",
    ) == workspace.resolve()

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_managed_workspace_locator(
            locator,
            store=store,
            current_agent_run_id="other-run",
            current_runtime_id="codex",
        )
    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_managed_locator_rejects_current_runtime_mismatch(tmp_path):
    workspace = tmp_path / "workspaces" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    store = SimpleNamespace(
        store_root=tmp_path / "managed_runs",
        load=lambda run_id: SimpleNamespace(
            run_id=run_id, runtime_id="codex", workspace_path=str(workspace)
        ),
    )
    locator = ManagedWorkspaceLocator(runtimeId="codex", agentRunId="run-1")

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_managed_workspace_locator(
            locator,
            store=store,
            current_agent_run_id="run-1",
            current_runtime_id="claude_code",
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_managed_locator_rejects_store_record_runtime_mismatch(tmp_path):
    workspace = tmp_path / "workspaces" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    store = SimpleNamespace(
        store_root=tmp_path / "managed_runs",
        load=lambda run_id: SimpleNamespace(
            run_id=run_id, runtime_id="claude_code", workspace_path=str(workspace)
        ),
    )
    locator = ManagedWorkspaceLocator(runtimeId="codex", agentRunId="run-1")

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_managed_workspace_locator(
            locator,
            store=store,
            current_agent_run_id="run-1",
            current_runtime_id="codex",
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_managed_locator_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "repo").symlink_to(outside, target_is_directory=True)
    store = SimpleNamespace(
        store_root=tmp_path / "managed_runs",
        load=lambda run_id: SimpleNamespace(
            run_id=run_id, runtime_id="codex", workspace_path=str(workspace)
        ),
    )
    locator = ManagedWorkspaceLocator(
        runtimeId="codex", agentRunId="run-1", relativePath="repo"
    )

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_managed_workspace_locator(
            locator,
            store=store,
            current_agent_run_id="run-1",
            current_runtime_id="codex",
        )

    assert exc.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
