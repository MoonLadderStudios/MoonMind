"""Store-backed container-job workspace authorization coverage (MoonMind#3255).

These tests exercise the API-owned authorizer against the *real*
``ManagedSessionStore`` ownership record so that run/managed-session isolation
is proven through the authoritative durable record, not only against a
synthetic SourceCorrelation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api_service.services.container_job_workspace_authorizer import (
    ContainerJobWorkspaceAuthorizationError,
    ContainerJobWorkspaceAuthorizer,
    managed_session_ownership_lookup,
)
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)

OWNER = OwnerIdentity(principalId="user-1", principalType="user")


def _record(*, session_id: str, agent_run_id: str, status: str = "ready") -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId=agent_run_id,
        containerId="c1",
        threadId="t1",
        runtimeId="codex",
        imageRef="img:1",
        controlUrl="http://control",
        status=status,
        workspacePath="/w",
        sessionWorkspacePath="/w/s",
        artifactSpoolPath="/w/a",
        startedAt=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


def _submission(*, session_id: str, managed_session_id: str, agent_run_id: str | None = None):
    source = {"source": "managed_session", "managedSessionId": managed_session_id}
    if agent_run_id is not None:
        source["agentRunId"] = agent_run_id
    return ContainerJobSubmitRequest(
        idempotencyKey="k",
        source=source,
        spec={
            "image": "alpine",
            "workspaceRef": {"kind": "moonmind-session", "sessionId": session_id},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    )


def _authorizer(store: ManagedSessionStore) -> ContainerJobWorkspaceAuthorizer:
    return ContainerJobWorkspaceAuthorizer(
        managed_session_lookup=managed_session_ownership_lookup(store)
    )


@pytest.mark.asyncio
async def test_live_managed_session_record_authorizes(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    store.save(_record(session_id="run", agent_run_id="ar-1"))
    authorizer = _authorizer(store)
    # Correlated reference against a live owned record is authorized.
    await authorizer.authorize(
        owner=OWNER,
        request=_submission(session_id="run", managed_session_id="run", agent_run_id="ar-1"),
    )


@pytest.mark.asyncio
async def test_absent_managed_session_record_is_workspace_not_found(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    authorizer = _authorizer(store)
    with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
        await authorizer.authorize(
            owner=OWNER,
            request=_submission(session_id="ghost", managed_session_id="ghost"),
        )
    assert excinfo.value.code == "workspace_not_found"


@pytest.mark.asyncio
async def test_terminated_managed_session_record_is_workspace_not_found(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    store.save(_record(session_id="run", agent_run_id="ar-1", status="terminated"))
    authorizer = _authorizer(store)
    with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
        await authorizer.authorize(
            owner=OWNER,
            request=_submission(session_id="run", managed_session_id="run"),
        )
    assert excinfo.value.code == "workspace_not_found"


@pytest.mark.asyncio
async def test_cross_session_managed_reference_is_permission_denied(tmp_path) -> None:
    # A live record exists for "victim"; the authenticated source only proves
    # ownership of "attacker", so naming victim as the locator fails closed.
    store = ManagedSessionStore(tmp_path)
    store.save(_record(session_id="victim", agent_run_id="ar-victim"))
    store.save(_record(session_id="attacker", agent_run_id="ar-attacker"))
    authorizer = _authorizer(store)
    with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
        await authorizer.authorize(
            owner=OWNER,
            request=_submission(session_id="victim", managed_session_id="attacker"),
        )
    assert excinfo.value.code == "permission_denied"


@pytest.mark.asyncio
async def test_cross_agent_run_managed_reference_is_permission_denied(tmp_path) -> None:
    # Correct session id, but the record is bound to a different agent run than
    # the one the API authenticated on the source.
    store = ManagedSessionStore(tmp_path)
    store.save(_record(session_id="run", agent_run_id="ar-owner"))
    authorizer = _authorizer(store)
    with pytest.raises(ContainerJobWorkspaceAuthorizationError) as excinfo:
        await authorizer.authorize(
            owner=OWNER,
            request=_submission(
                session_id="run", managed_session_id="run", agent_run_id="ar-other"
            ),
        )
    assert excinfo.value.code == "permission_denied"
