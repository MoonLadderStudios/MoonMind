"""Transport admission for the existing binding-scoped native chat facade."""

from time import monotonic
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocketException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from api_service.auth_providers import get_instance_access
from api_service.db.base import get_async_session
from api_service.operator_admission import require_operator
from moonmind.security.operator_admission import (
    STREAM_REVALIDATION_SECONDS,
    OperatorAdmission,
)


def _admission_request(connection: HTTPConnection) -> Request:
    # The same ingress proof applies to HTTP and WebSocket handshakes. Treat
    # upgrades as mutations for Origin validation; never read URL credentials.
    if connection.scope["type"] == "websocket":
        return Request({**connection.scope, "type": "http", "method": "POST"})
    return Request(connection.scope)


async def get_workflow_chat_access(
    connection: HTTPConnection,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> OperatorAdmission:
    """Use published conversion admission for the existing chat facade."""
    try:
        caller = await get_instance_access(_admission_request(connection), session)
        if isinstance(caller, OperatorAdmission):
            connection.state.workflow_chat_admitted_at = monotonic()
        return caller
    except HTTPException as exc:
        if connection.scope["type"] != "websocket":
            raise
        reason = exc.detail.get("code") if isinstance(exc.detail, dict) else exc.detail
        raise WebSocketException(
            code=4403 if exc.status_code == 403 else 4401,
            reason=str(reason or "auth_required"),
        ) from exc


def workflow_chat_stream_timeout(
    connection: HTTPConnection,
    caller: OperatorAdmission,
    maximum_wait: float,
) -> float:
    """Bound cached transport authority without renewing its ingress assertion."""
    if not isinstance(caller, OperatorAdmission):
        return maximum_wait
    admitted_at = getattr(connection.state, "workflow_chat_admitted_at", None)
    remaining = (
        STREAM_REVALIDATION_SECONDS - (monotonic() - admitted_at)
        if admitted_at is not None
        else 0.0
    )
    if remaining <= 0:
        raise HTTPException(status_code=401, detail={"code": "auth_required"})
    return min(maximum_wait, remaining)


async def revalidate_workflow_chat_access(
    connection: HTTPConnection,
    caller: OperatorAdmission,
) -> None:
    """Recheck live ingress authority without canceling independently admitted work."""
    if isinstance(caller, OperatorAdmission):
        workflow_chat_stream_timeout(connection, caller, 0.0)
        # This detects local configuration changes, not upstream identity
        # revocation. Only a new request can obtain a fresh ingress assertion.
        await require_operator(_admission_request(connection))


def workflow_chat_actor(caller: OperatorAdmission) -> str:
    """Audit provenance is not a synthetic person or a machine credential."""
    return "operator"
