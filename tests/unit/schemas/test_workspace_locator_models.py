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
from moonmind.workflows.temporal.runtime.workspace_locators import (
    resolve_managed_workspace_locator,
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


def test_workspace_locator_rejects_unknown_discriminator():
    with pytest.raises(ValidationError):
        WORKSPACE_LOCATOR_ADAPTER.validate_python({"kind": "host", "path": "/tmp"})


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
