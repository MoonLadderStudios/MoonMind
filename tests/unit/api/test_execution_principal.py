"""Tests for ExecutionPrincipal resolution from request headers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_service.api.execution_principal import resolve_execution_principal
from moonmind.workflows.tasks.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
)


class _FakeService:
    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    async def describe_execution(self, workflow_id: str, **_kwargs: Any) -> Any:
        if workflow_id not in self._records:
            raise LookupError(workflow_id)
        return self._records[workflow_id]


def _user(*, user_id: str | None = "user-1", is_superuser: bool = False) -> Any:
    return SimpleNamespace(id=user_id, is_superuser=is_superuser)


@pytest.mark.asyncio
async def test_resolve_principal_without_task_headers_has_no_task_identity() -> None:
    principal = await resolve_execution_principal(
        user=_user(),
        service=_FakeService({}),
    )
    assert principal.user_id == "user-1"
    assert principal.workflow_id is None
    assert principal.scopes == frozenset()


@pytest.mark.asyncio
async def test_resolve_principal_with_verified_workflow_grants_scopes() -> None:
    record = SimpleNamespace(owner_id="user-1")
    service = _FakeService({"mm:parent": record})

    principal = await resolve_execution_principal(
        user=_user(),
        service=service,
        workflow_id_header="mm:parent",
        run_id_header="run-1",
        task_run_id_header="task-run-1",
    )

    assert principal.workflow_id == "mm:parent"
    assert principal.run_id == "run-1"
    assert principal.task_run_id == "task-run-1"
    assert SCOPE_CREATE_CHILD in principal.scopes
    assert SCOPE_INHERIT_RUNTIME in principal.scopes


@pytest.mark.asyncio
async def test_resolve_principal_drops_unverified_workflow_id() -> None:
    record = SimpleNamespace(owner_id="someone-else")
    service = _FakeService({"mm:parent": record})

    principal = await resolve_execution_principal(
        user=_user(),
        service=service,
        workflow_id_header="mm:parent",
    )

    assert principal.workflow_id is None
    assert principal.scopes == frozenset()


@pytest.mark.asyncio
async def test_resolve_principal_drops_workflow_id_when_describe_fails() -> None:
    service = _FakeService({})

    principal = await resolve_execution_principal(
        user=_user(),
        service=service,
        workflow_id_header="mm:does-not-exist",
    )

    assert principal.workflow_id is None
    assert principal.scopes == frozenset()


@pytest.mark.asyncio
async def test_resolve_principal_superuser_skips_ownership_check() -> None:
    record = SimpleNamespace(owner_id="someone-else")
    service = _FakeService({"mm:parent": record})

    principal = await resolve_execution_principal(
        user=_user(user_id="admin", is_superuser=True),
        service=service,
        workflow_id_header="mm:parent",
    )

    assert principal.workflow_id == "mm:parent"
    assert principal.is_superuser is True
    assert SCOPE_CREATE_CHILD in principal.scopes
    assert SCOPE_INHERIT_RUNTIME in principal.scopes
