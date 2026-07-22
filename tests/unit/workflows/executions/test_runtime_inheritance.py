"""Unit tests for child-agent runtime inheritance resolution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
    ExecutionPrincipal,
    InheritedRuntime,
    RuntimeInheritanceError,
    apply_inherited_runtime_to_payload,
    extract_inheritance_directive,
    has_explicit_child_runtime,
    resolve_child_runtime_inheritance,
)

# ---------------------------------------------------------------------------
# extract_inheritance_directive / has_explicit_child_runtime
# ---------------------------------------------------------------------------


def test_extract_inheritance_directive_top_level_caller() -> None:
    payload = {"runtimeInheritance": "caller", "task": {}}
    directive, parent_id = extract_inheritance_directive(payload)
    assert directive == "caller"
    assert parent_id is None


def test_extract_inheritance_directive_runtime_inherit_form() -> None:
    payload = {"task": {"runtime": {"inherit": "parent"}}}
    directive, parent_id = extract_inheritance_directive(payload)
    assert directive == "parent"
    assert parent_id is None


def test_extract_inheritance_directive_parent_workflow_id() -> None:
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:abc",
        "task": {},
    }
    directive, parent_id = extract_inheritance_directive(payload)
    assert directive == "parent"
    assert parent_id == "mm:abc"


def test_extract_inheritance_directive_rejects_unknown_value() -> None:
    with pytest.raises(RuntimeInheritanceError):
        extract_inheritance_directive({"runtimeInheritance": "self"})


def test_extract_inheritance_directive_rejects_conflicting_values() -> None:
    payload = {
        "runtimeInheritance": "caller",
        "task": {"runtime": {"inherit": "parent"}},
    }
    with pytest.raises(RuntimeInheritanceError):
        extract_inheritance_directive(payload)


def test_has_explicit_child_runtime_targetRuntime() -> None:
    assert has_explicit_child_runtime({"targetRuntime": "codex_cli", "task": {}})


def test_has_explicit_child_runtime_task_runtime_mode() -> None:
    assert has_explicit_child_runtime({"task": {"runtime": {"mode": "codex_cli"}}})


def test_has_explicit_child_runtime_profile_id() -> None:
    assert has_explicit_child_runtime({"task": {"profileId": "p-1"}})


def test_has_explicit_child_runtime_provider_profile_ref() -> None:
    assert has_explicit_child_runtime(
        {"task": {"runtime": {"providerProfileRef": "child-profile"}}}
    )


def test_has_explicit_child_runtime_false_when_only_inheritance() -> None:
    assert not has_explicit_child_runtime({"runtimeInheritance": "caller", "task": {}})


# ---------------------------------------------------------------------------
# resolve_child_runtime_inheritance
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    async def describe_execution(self, workflow_id: str, **_kwargs: Any) -> Any:
        if workflow_id not in self._records:
            raise LookupError(workflow_id)
        return self._records[workflow_id]


def _parent_record(
    *,
    workflow_id: str = "mm:parent",
    owner_id: str | None = "user-1",
    target_runtime: str | None = "codex_cli",
    model: str | None = "gpt-5.4",
    effort: str | None = "high",
    profile_id: str | None = "codex_default",
    execution_profile_ref: str | None = "codex_default",
    omnigent: dict[str, Any] | None = None,
) -> SimpleNamespace:
    parameters: dict[str, Any] = {}
    if target_runtime is not None:
        parameters["targetRuntime"] = target_runtime
    if model is not None:
        parameters["model"] = model
    if effort is not None:
        parameters["effort"] = effort
    if profile_id is not None:
        parameters["profileId"] = profile_id
    workflow_runtime: dict[str, Any] = {}
    if target_runtime is not None:
        workflow_runtime["mode"] = target_runtime
    if execution_profile_ref is not None:
        workflow_runtime["executionProfileRef"] = execution_profile_ref
    if workflow_runtime:
        parameters["workflow"] = {"runtime": workflow_runtime}
    if omnigent is not None:
        parameters["omnigent"] = omnigent
    return SimpleNamespace(
        workflow_id=workflow_id,
        owner_id=owner_id,
        parameters=parameters,
        memo={},
        search_attributes={},
    )


@pytest.mark.asyncio
async def test_caller_inheritance_copies_parent_runtime() -> None:
    parent = _parent_record()
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(
        user_id="user-1",
        workflow_id="mm:parent",
        scopes=frozenset({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}),
    )
    payload = {"runtimeInheritance": "caller", "task": {}}

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is not None
    assert inherited.target_runtime == "codex_cli"
    assert inherited.model == "gpt-5.4"
    assert inherited.effort == "high"
    assert inherited.execution_profile_ref == "codex_default"
    assert inherited.profile_id == "codex_default"
    assert inherited.source_workflow_id == "mm:parent"


@pytest.mark.asyncio
async def test_directive_runs_inheritance_alongside_explicit_runtime_fields() -> None:
    """An explicit targetRuntime must not block inheritance for other fields.

    When a caller sends ``runtimeInheritance="caller"`` together with a
    partial explicit runtime (e.g. ``targetRuntime`` only), inheritance still
    runs so that missing fields like ``model`` and ``effort`` are copied
    from the parent.  ``apply_inherited_runtime_to_payload`` preserves the
    caller's explicit fields.
    """

    service = _FakeService({"mm:parent": _parent_record()})
    principal = ExecutionPrincipal(
        user_id="user-1",
        workflow_id="mm:parent",
        scopes=frozenset({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}),
    )
    payload = {
        "runtimeInheritance": "caller",
        "targetRuntime": "claude_code",
        "task": {"runtime": {"mode": "claude_code"}},
    }

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is not None
    # Parent's model/effort/profile are surfaced regardless of the
    # caller's explicit targetRuntime override.
    assert inherited.model == "gpt-5.4"
    assert inherited.effort == "high"
    assert inherited.execution_profile_ref == "codex_default"


@pytest.mark.asyncio
async def test_caller_inheritance_rejected_for_non_task_principal() -> None:
    service = _FakeService({})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {"runtimeInheritance": "caller", "task": {}}

    with pytest.raises(RuntimeInheritanceError) as excinfo:
        await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=payload["task"],
            principal=principal,
            service=service,
        )
    assert excinfo.value.code == "runtime_inheritance_requires_workflow_principal"


@pytest.mark.asyncio
async def test_caller_inheritance_requires_scopes() -> None:
    service = _FakeService({"mm:parent": _parent_record()})
    principal = ExecutionPrincipal(
        user_id="user-1",
        workflow_id="mm:parent",
        scopes=frozenset(),
    )
    payload = {"runtimeInheritance": "caller", "task": {}}

    with pytest.raises(RuntimeInheritanceError) as excinfo:
        await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=payload["task"],
            principal=principal,
            service=service,
        )
    assert excinfo.value.code == "runtime_inheritance_forbidden"


@pytest.mark.asyncio
async def test_parent_inheritance_requires_parent_workflow_id() -> None:
    service = _FakeService({})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {"runtimeInheritance": "parent", "task": {}}

    with pytest.raises(RuntimeInheritanceError) as excinfo:
        await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=payload["task"],
            principal=principal,
            service=service,
        )
    assert excinfo.value.code == "runtime_inheritance_requires_parent"


@pytest.mark.asyncio
async def test_parent_inheritance_rejected_when_user_does_not_own_parent() -> None:
    parent = _parent_record(owner_id="someone-else")
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:parent",
        "task": {},
    }

    with pytest.raises(RuntimeInheritanceError) as excinfo:
        await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=payload["task"],
            principal=principal,
            service=service,
        )
    assert excinfo.value.code == "runtime_inheritance_forbidden"


@pytest.mark.asyncio
async def test_parent_inheritance_succeeds_when_user_owns_parent() -> None:
    parent = _parent_record(owner_id="user-1", target_runtime="claude_code", model="claude-opus-4-7", effort="medium", profile_id=None, execution_profile_ref="claude_profile")
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:parent",
        "task": {},
    }

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is not None
    assert inherited.target_runtime == "claude_code"
    assert inherited.model == "claude-opus-4-7"
    assert inherited.effort == "medium"
    assert inherited.execution_profile_ref == "claude_profile"


@pytest.mark.asyncio
async def test_parent_inheritance_superuser_skips_ownership_check() -> None:
    parent = _parent_record(owner_id="other-user")
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(user_id="admin", is_superuser=True)
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:parent",
        "task": {},
    }

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is not None
    assert inherited.target_runtime == "codex_cli"


@pytest.mark.asyncio
async def test_no_directive_returns_none() -> None:
    service = _FakeService({})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {"task": {}}

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is None


@pytest.mark.asyncio
async def test_parent_inheritance_normalizes_alias_runtime_id() -> None:
    parent = _parent_record(target_runtime="codex")  # legacy alias
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:parent",
        "task": {},
    }

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )

    assert inherited is not None
    assert inherited.target_runtime == "codex_cli"


@pytest.mark.asyncio
async def test_parent_inheritance_returns_404_style_error_when_parent_missing() -> None:
    service = _FakeService({})
    principal = ExecutionPrincipal(user_id="user-1")
    payload = {
        "runtimeInheritance": "parent",
        "parentWorkflowId": "mm:missing",
        "task": {},
    }

    with pytest.raises(RuntimeInheritanceError) as excinfo:
        await resolve_child_runtime_inheritance(
            request_payload=payload,
            task_payload=payload["task"],
            principal=principal,
            service=service,
        )
    assert excinfo.value.code == "parent_execution_not_found"


# ---------------------------------------------------------------------------
# apply_inherited_runtime_to_payload
# ---------------------------------------------------------------------------


def test_apply_inherited_runtime_writes_target_and_task_runtime_fields() -> None:
    payload: dict[str, Any] = {"task": {}}
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        model="gpt-5.4",
        effort="high",
        profile_id="codex_default",
        execution_profile_ref="codex_default",
        source_workflow_id="mm:parent",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert payload["targetRuntime"] == "codex_cli"
    runtime = task_payload["runtime"]
    assert runtime["mode"] == "codex_cli"
    assert runtime["model"] == "gpt-5.4"
    assert runtime["effort"] == "high"
    assert runtime["executionProfileRef"] == "codex_default"
    assert runtime["profileId"] == "codex_default"


@pytest.mark.asyncio
async def test_github_3453_child_inherits_complete_omnigent_selection() -> None:
    selection = {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "agent": {"harnessOverride": "codex-native"},
    }
    parent = _parent_record(
        target_runtime="omnigent",
        profile_id=None,
        execution_profile_ref="codex-oauth-profile",
        omnigent=selection,
    )
    service = _FakeService({"mm:parent": parent})
    principal = ExecutionPrincipal(
        user_id="user-1",
        workflow_id="mm:parent",
        scopes=frozenset({SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}),
    )
    payload: dict[str, Any] = {"runtimeInheritance": "caller", "task": {}}

    inherited = await resolve_child_runtime_inheritance(
        request_payload=payload,
        task_payload=payload["task"],
        principal=principal,
        service=service,
    )
    assert inherited is not None
    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=payload["task"],
        inherited=inherited,
    )

    assert payload["targetRuntime"] == "omnigent"
    assert payload["task"]["runtime"] == {
        "mode": "omnigent",
        "model": "gpt-5.4",
        "effort": "high",
        "executionProfileRef": "codex-oauth-profile",
        "omnigent": selection,
    }


def test_github_3453_explicit_child_omnigent_selection_wins_inheritance() -> None:
    inherited = InheritedRuntime(
        target_runtime="omnigent",
        execution_profile_ref="parent-profile",
        omnigent={
            "executionTargetRef": "parent-target",
            "launchPolicyRef": "parent-policy",
        },
    )
    child_selection = {
        "executionTargetRef": "child-target",
        "launchPolicyRef": "child-policy",
    }
    payload: dict[str, Any] = {
        "targetRuntime": "omnigent",
        "task": {
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": "child-profile",
                "omnigent": child_selection,
            }
        },
    }

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=payload["task"],
        inherited=inherited,
    )

    assert payload["task"]["runtime"]["executionProfileRef"] == "child-profile"
    assert payload["task"]["runtime"]["omnigent"] == child_selection


def test_apply_inherited_runtime_does_not_overwrite_existing_runtime_fields() -> None:
    payload: dict[str, Any] = {"task": {"runtime": {"model": "explicit-model"}}}
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        model="gpt-5.4",
        effort="high",
        execution_profile_ref="codex_default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    # Existing explicit model survives.
    assert task_payload["runtime"]["model"] == "explicit-model"
    # New fields are filled in.
    assert task_payload["runtime"]["effort"] == "high"
    assert task_payload["runtime"]["executionProfileRef"] == "codex_default"


def test_apply_inherited_runtime_preserves_explicit_target_runtime() -> None:
    """An explicit ``targetRuntime`` must survive inheritance."""

    payload: dict[str, Any] = {
        "targetRuntime": "claude_code",
        "task": {"runtime": {"mode": "claude_code"}},
    }
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        model="gpt-5.4",
        effort="high",
        execution_profile_ref="codex_default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert payload["targetRuntime"] == "claude_code"
    assert task_payload["runtime"]["mode"] == "claude_code"
    # The other gaps still fill in from the parent.
    assert task_payload["runtime"]["model"] == "gpt-5.4"
    assert task_payload["runtime"]["effort"] == "high"
    assert task_payload["runtime"]["executionProfileRef"] == "codex_default"


def test_apply_inherited_runtime_skips_execution_profile_ref_when_explicit_profile_id() -> None:
    """Explicit child profileId must not be silently overridden by the parent ref.

    Downstream selection prefers ``executionProfileRef`` over
    ``profileId``/``providerProfile``, so backfilling the parent's ref
    when the child supplied an explicit profile selector would route the
    child to the wrong provider profile.
    """

    payload: dict[str, Any] = {"task": {"runtime": {"profileId": "child-explicit"}}}
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        model="gpt-5.4",
        profile_id="parent-default",
        execution_profile_ref="parent-default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert task_payload["runtime"]["profileId"] == "child-explicit"
    assert "executionProfileRef" not in task_payload["runtime"]


def test_apply_inherited_runtime_preserves_explicit_provider_profile_ref() -> None:
    payload: dict[str, Any] = {
        "task": {"runtime": {"providerProfileRef": "child-explicit"}}
    }
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        profile_id="parent-default",
        execution_profile_ref="parent-default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert task_payload["runtime"]["providerProfileRef"] == "child-explicit"
    assert "executionProfileRef" not in task_payload["runtime"]
    assert "profileId" not in task_payload["runtime"]


def test_apply_inherited_runtime_skips_profile_backfill_when_child_sets_execution_profile_ref() -> None:
    """Explicit child executionProfileRef counts as a profile selector.

    Backfilling the parent's profileId on top of the child's
    executionProfileRef would leave the merged runtime block carrying a
    stale parent profileId — confusing for consumers that read it, even
    though downstream selection prefers executionProfileRef.
    """

    payload: dict[str, Any] = {
        "task": {"runtime": {"executionProfileRef": "child-explicit"}}
    }
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        profile_id="parent-default",
        execution_profile_ref="parent-default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert task_payload["runtime"]["executionProfileRef"] == "child-explicit"
    assert "profileId" not in task_payload["runtime"]


def test_apply_inherited_runtime_skips_execution_profile_ref_for_top_level_provider_profile() -> None:
    payload: dict[str, Any] = {"providerProfile": "child-explicit", "task": {}}
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="codex_cli",
        profile_id="parent-default",
        execution_profile_ref="parent-default",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    runtime = task_payload.get("runtime", {})
    assert "executionProfileRef" not in runtime
    assert "profileId" not in runtime


def test_apply_inherited_runtime_fills_model_when_only_runtime_mode_is_explicit() -> None:
    """Regression: parent sonnet-4-6 must carry over when child sets only mode.

    Mirrors the batch-pr-resolver scenario where the request stamps
    ``targetRuntime`` / ``task.runtime.mode`` as a fallback but leaves
    ``model`` empty because the parent's task_context.json lacked it.
    Inheritance should still fill in the missing model from the parent's
    resolved parameters.
    """

    payload: dict[str, Any] = {
        "targetRuntime": "claude_code",
        "task": {"runtime": {"mode": "claude_code"}},
    }
    task_payload = payload["task"]
    inherited = InheritedRuntime(
        target_runtime="claude_code",
        model="sonnet-4-6",
    )

    apply_inherited_runtime_to_payload(
        payload=payload,
        task_payload=task_payload,
        inherited=inherited,
    )

    assert task_payload["runtime"]["model"] == "sonnet-4-6"
