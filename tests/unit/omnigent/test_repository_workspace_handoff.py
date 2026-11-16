"""Controller saves replace a repository source without changing its authority."""

from copy import deepcopy

import pytest

from moonmind.omnigent import workspace_sources as source
from moonmind.workflows.executions.repository_contract import authored_repository_access
from moonmind.workflows.temporal.workflows import agent_run as child_module
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.temporal.test_dispatch_pool_context import request, result

TARGET = {
    "provider": "git",
    "connectionRef": "selected-connection",
    "repository": {"name": "owner/repo"},
    "branch": {"name": "selected-base"},
}


def workspace(*, nested=False, anonymous=False):
    return {
        "workspaceSource": {
            "kind": "repository",
            **(
                {"repositoryTarget": deepcopy(TARGET)}
                if nested
                else {
                    "repository": "owner/repo",
                    "branch": "selected-base",
                    "connectionRef": "selected-connection",
                }
            ),
            **({"accessMode": "anonymous", "connectionRef": ""} if anonymous else {}),
        },
        "targetBranch": "accepted-work-branch",
    }


@pytest.mark.parametrize(
    "nested", [False, True], ids=["source-fields", "nested-target"]
)
def test_controller_checkpoint_keeps_repository_authority(nested):
    original = workspace(nested=nested)
    untouched = deepcopy(original)
    converted = source.replace_repository_workspace_source(
        original, "artifact://verified-save"
    )
    assert original == untouched
    compiled = source.compile_workspace_source(converted)
    assert (compiled.kind, compiled.repository_ref, compiled.repository_branch) == (
        "checkpoint",
        "owner/repo",
        "selected-base",
    )
    assert compiled.checkpoint_ref == "artifact://verified-save"
    assert authored_repository_access({"workspaceSpec": converted}) == (
        "selected-connection",
        False,
    )
    assert converted["targetBranch"] == "accepted-work-branch"
    if nested:
        assert converted["repositoryTarget"] == TARGET


def test_anonymous_repository_remains_anonymous_after_controller_save():
    converted = source.replace_repository_workspace_source(
        workspace(anonymous=True), "artifact://verified-save"
    )
    assert authored_repository_access({"workspaceSpec": converted}) == ("", True)
    assert source.compile_workspace_source(converted).repository_ref == "owner/repo"


@pytest.mark.parametrize(
    "addition",
    [
        {"workspaceCheckpointRestoreRef": "artifact://authored-save"},
        {"workspaceArtifactRef": "artifact://authored-artifact"},
        {"repositoryTarget": {**TARGET, "connectionRef": "other-connection"}},
        {"repository": "other/repo"},
        {"startingBranch": "different-base"},
    ],
    ids=[
        "authored-checkpoint",
        "authored-artifact",
        "connection-conflict",
        "repository-conflict",
        "branch-conflict",
    ],
)
def test_controller_save_never_erases_authored_conflicts(addition):
    original = {**workspace(nested=True), **addition}
    untouched = deepcopy(original)
    with pytest.raises(ValueError):
        source.replace_repository_workspace_source(original, "artifact://verified-save")
    assert original == untouched


@pytest.mark.parametrize(
    "enabled", [False, True], ids=["retained-history", "current-history"]
)
def test_parent_context_repository_handoff_keeps_recorded_shapes(monkeypatch, enabled):
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch: (
            enabled
            if patch == "run-canonical-repository-workspace-handoff-v1"
            else True
        ),
    )
    parent = run_module.MoonMindUserWorkflow()
    parent._record_shared_runtime_context(request(), result())
    successor = request("second", "reuse_session_same_epoch")
    successor.workspace_spec = workspace(nested=True)
    bound = parent._bind_shared_runtime_context(successor)
    assert bound.canonical_repository_workspace_handoff is enabled
    assert (
        "canonicalRepositoryWorkspaceHandoff" in bound.model_dump(by_alias=True)
    ) is enabled
    if enabled:
        assert (
            source.compile_workspace_source(bound.workspace_spec).kind == "checkpoint"
        )
        assert authored_repository_access({"workspaceSpec": bound.workspace_spec}) == (
            "selected-connection",
            False,
        )
    else:
        assert (
            bound.workspace_spec["workspaceSource"]
            == successor.workspace_spec["workspaceSource"]
        )


@pytest.mark.parametrize(
    "enabled", [False, True], ids=["retained-history", "current-history"]
)
def test_cooldown_repository_save_preserves_selected_source(monkeypatch, enabled):
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    child = child_module.MoonMindAgentRun()
    child._execution_budget = None
    monkeypatch.setattr(
        child,
        "_workflow_patch_enabled",
        lambda patch: (
            enabled
            if patch == "agent-run-canonical-repository-workspace-handoff-v1"
            else True
        ),
    )
    selected = request()
    selected.workspace_spec = workspace(nested=True)
    selected.canonical_repository_workspace_handoff = True
    child.final_result = AgentRunResult(
        metadata={
            "savedWorkspaceCheckpoint": {"archiveRef": "artifact://verified-save"}
        }
    )
    bound = child._pool_cooldown_retry_request(selected)
    if enabled:
        assert (
            source.compile_workspace_source(bound.workspace_spec).kind == "checkpoint"
        )
        assert bound.workspace_spec["repositoryTarget"] == TARGET
    else:
        assert (
            bound.workspace_spec["workspaceSource"]
            == selected.workspace_spec["workspaceSource"]
        )


def test_repository_projection_does_not_read_environment_clock_or_files(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("workflow projection reached nondeterministic authority")

    monkeypatch.setattr(source.os, "getenv", forbidden)
    monkeypatch.setattr(source.time, "time", forbidden)
    monkeypatch.setattr(source.hmac, "new", forbidden)
    converted = source.replace_repository_workspace_source(
        workspace(nested=True), "artifact://verified-save"
    )
    assert converted["workspaceCheckpointRestoreRef"] == "artifact://verified-save"


@pytest.mark.parametrize(
    "enabled", [False, True], ids=["retained-history", "current-history"]
)
def test_trusted_step_retry_replaces_only_controller_repository_source(
    monkeypatch, enabled
):
    from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
        _configure_workflow_runtime,
    )

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch: (
            enabled
            if patch == "run-canonical-repository-workspace-handoff-v1"
            else True
        ),
    )
    parent = run_module.MoonMindUserWorkflow()
    built = parent._build_agent_execution_request(
        node_inputs={
            "instructions": "Preserve accepted work.",
            "runtime": {"mode": "codex_cli"},
        },
        node_id="work",
        tool_name="codex_cli",
        workflow_parameters={"workspaceSpec": workspace(nested=True)},
        trusted_remediation_checkpoint_restore_ref="artifact://verified-save",
    )
    if enabled:
        assert (
            source.compile_workspace_source(built.workspace_spec).kind == "checkpoint"
        )
        assert authored_repository_access({"workspaceSpec": built.workspace_spec}) == (
            "selected-connection",
            False,
        )
    else:
        assert built.workspace_spec["workspaceSource"]["kind"] == "repository"


def test_authored_capability_is_replaced_only_by_current_parent(monkeypatch):
    from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
        _configure_workflow_runtime,
    )

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
    parent = run_module.MoonMindUserWorkflow()
    built = parent._build_agent_execution_request(
        node_inputs={
            "instructions": "Keep the selected task.",
            "runtime": {
                "mode": "codex_cli",
                "parameters": {
                    "metadata": {
                        "moonmind": {
                            "canonicalRepositoryWorkspaceHandoff": True,
                        }
                    }
                },
            },
        },
        node_id="work",
        tool_name="codex_cli",
        workflow_parameters={},
    )
    assert (
        built.parameters["metadata"]["moonmind"]["canonicalRepositoryWorkspaceHandoff"]
        is True
    )
    assert not built.canonical_repository_workspace_handoff
    assert "canonicalRepositoryWorkspaceHandoff" not in built.model_dump(by_alias=True)


def test_repeated_controller_replacements_keep_saved_branch_authority(monkeypatch):
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
    parent = run_module.MoonMindUserWorkflow()
    parent._record_shared_runtime_context(request(), result())
    successor = request("second", "reuse_session_same_epoch")
    successor.workspace_spec = workspace(nested=True)
    handed = parent._bind_shared_runtime_context(successor)
    child = child_module.MoonMindAgentRun()
    child._execution_budget = None
    monkeypatch.setattr(child, "_workflow_patch_enabled", lambda _patch: True)
    child.final_result = AgentRunResult(
        metadata={"savedWorkspaceCheckpoint": {"archiveRef": "artifact://newer-save"}}
    )
    retried = child._pool_cooldown_retry_request(handed)
    compiled = source.compile_workspace_source(retried.workspace_spec)
    assert (compiled.kind, compiled.checkpoint_ref, compiled.repository_branch) == (
        "checkpoint",
        "artifact://newer-save",
        "selected-base",
    )
    assert retried.workspace_spec["repositoryTarget"] == TARGET
    assert (
        handed.workspace_spec["workspaceCheckpointRestoreRef"]
        == "artifact://verified-workspace"
    )


@pytest.mark.parametrize(
    "metadata",
    [None, "retained-value", {"moonmind": "retained-hint"}],
    ids=["absent", "non-mapping-metadata", "non-mapping-hints"],
)
def test_retained_cooldown_does_not_interpret_authored_metadata(monkeypatch, metadata):
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    child = child_module.MoonMindAgentRun()
    child._execution_budget = None
    monkeypatch.setattr(child, "_workflow_patch_enabled", lambda _patch: True)
    selected = request()
    selected.parameters = {"metadata": metadata}
    child.final_result = AgentRunResult(
        metadata={
            "savedWorkspaceCheckpoint": {"archiveRef": "artifact://verified-save"}
        }
    )
    retried = child._pool_cooldown_retry_request(selected)
    assert (
        retried.workspace_spec["workspaceCheckpointRestoreRef"]
        == "artifact://verified-save"
    )
    assert retried.parameters["metadata"] == metadata
    assert "canonicalRepositoryWorkspaceHandoff" not in retried.model_dump(
        by_alias=True
    )


@pytest.mark.parametrize("location", ["nested", "outer"])
@pytest.mark.parametrize("handoff", ["continuation", "step-retry", "cooldown"])
def test_retained_controller_save_does_not_erase_pinned_revision(
    monkeypatch, location, handoff
):
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
        _configure_workflow_runtime,
    )

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow, "patched",
        lambda patch: patch != run_module.RUN_PINNED_REPOSITORY_WORKSPACE_PATCH,
    )
    selected = request("second", "reuse_session_same_epoch")
    selected.workspace_spec = workspace(nested=location == "nested")
    target = selected.workspace_spec["workspaceSource"].get("repositoryTarget")
    if location == "outer":
        target = deepcopy(TARGET)
        selected.workspace_spec["repositoryTarget"] = target
    target["revision"] = {"kind": "git_commit", "commitSha": "a" * 40}
    original = deepcopy(selected.workspace_spec)
    parent = run_module.MoonMindUserWorkflow()
    parent._record_shared_runtime_context(request(), result())
    with pytest.raises(source.WorkspaceSourceError, match="pinned repository"):
        if handoff == "continuation":
            parent._bind_shared_runtime_context(selected)
        elif handoff == "step-retry":
            parent._build_agent_execution_request(
                node_inputs={
                    "instructions": "Preserve accepted work.",
                    "runtime": {"mode": "codex_cli"},
                },
                node_id="work",
                tool_name="codex_cli",
                workflow_parameters={"workspaceSpec": selected.workspace_spec},
                trusted_remediation_checkpoint_restore_ref="artifact://verified-save",
            )
        else:
            child = child_module.MoonMindAgentRun()
            child._execution_budget = None
            monkeypatch.setattr(child, "_workflow_patch_enabled", lambda _patch: True)
            selected.canonical_repository_workspace_handoff = True
            child.final_result = AgentRunResult(
                metadata={
                    "savedWorkspaceCheckpoint": {
                        "archiveRef": "artifact://verified-save"
                    }
                }
            )
            child._pool_cooldown_retry_request(selected)
    assert selected.workspace_spec == original


@pytest.mark.parametrize("handoff", ["continuation", "step-retry", "cooldown"])
def test_patched_controller_save_preserves_pin_as_checkpoint_provenance(monkeypatch, handoff):
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from tests.unit.workflows.temporal.workflows.test_run_step_ledger import _configure_workflow_runtime

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
    selected = request("second", "reuse_session_same_epoch")
    selected.workspace_spec = workspace(nested=True)
    selected.workspace_spec["workspaceSource"]["repositoryTarget"]["revision"] = {
        "kind": "git_commit", "commitSha": "b" * 40,
    }
    original = deepcopy(selected.workspace_spec)
    parent = run_module.MoonMindUserWorkflow()
    parent._record_shared_runtime_context(request(), result())
    if handoff == "continuation":
        built = parent._bind_shared_runtime_context(selected)
    elif handoff == "step-retry":
        built = parent._build_agent_execution_request(
            node_inputs={"instructions": "Continue saved work.", "runtime": {"mode": "codex_cli"}},
            node_id="work", tool_name="codex_cli",
            workflow_parameters={"workspaceSpec": selected.workspace_spec},
            trusted_remediation_checkpoint_restore_ref="artifact://verified-save",
        )
    else:
        child = child_module.MoonMindAgentRun()
        child._execution_budget = None
        monkeypatch.setattr(child, "_workflow_patch_enabled", lambda _patch: True)
        selected.canonical_repository_workspace_handoff = True
        selected.pinned_repository_workspace_handoff = True
        child.final_result = AgentRunResult(metadata={
            "savedWorkspaceCheckpoint": {"archiveRef": "artifact://verified-save"},
        })
        built = child._pool_cooldown_retry_request(selected)
    compiled = source.compile_workspace_source(built.workspace_spec)
    assert compiled.kind == "checkpoint"
    assert compiled.repository_commit_sha == "b" * 40
    assert compiled.checkpoint_ref == (
        "artifact://verified-workspace" if handoff == "continuation" else "artifact://verified-save"
    )
    assert selected.workspace_spec == original
    assert authored_repository_access({"workspaceSpec": built.workspace_spec}) == ("selected-connection", False)
