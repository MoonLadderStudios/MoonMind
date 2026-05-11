"""Unit tests for child-task runtime inheritance resolution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.tasks.runtime_inheritance import (
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
    task_runtime: dict[str, Any] = {}
    if target_runtime is not None:
        task_runtime["mode"] = target_runtime
    if execution_profile_ref is not None:
        task_runtime["executionProfileRef"] = execution_profile_ref
    if task_runtime:
        parameters["task"] = {"runtime": task_runtime}
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
async def test_explicit_child_runtime_short_circuits_inheritance() -> None:
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

    assert inherited is None


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
    assert excinfo.value.code == "runtime_inheritance_requires_task_principal"


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
