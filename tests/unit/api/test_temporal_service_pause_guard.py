"""Unit tests for API guard: workflow submission blocked when system paused."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

pytestmark = [pytest.mark.asyncio]

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    return session

@pytest.fixture
def mock_client_adapter():
    return AsyncMock()

class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


async def test_check_system_paused_returns_false_when_pause_state_missing(
    mock_session, mock_client_adapter
):
    svc = TemporalExecutionService(mock_session, client_adapter=mock_client_adapter)
    mock_session.execute = AsyncMock(return_value=_ScalarResult(None))

    result = await svc.check_system_paused()

    assert result is False


async def test_check_system_paused_reads_persisted_pause_state(
    mock_session, mock_client_adapter
):
    svc = TemporalExecutionService(mock_session, client_adapter=mock_client_adapter)
    mock_session.execute = AsyncMock(
        return_value=_ScalarResult({"workersPaused": True, "mode": "drain"})
    )

    result = await svc.check_system_paused()

    assert result is True

# ---- API Guard on create_execution ----

async def test_create_execution_blocked_when_paused(mock_session, mock_client_adapter):
    """create_execution should raise TemporalExecutionValidationError when system is paused."""
    svc = TemporalExecutionService(mock_session, client_adapter=mock_client_adapter)

    with patch.object(svc, "check_system_paused", return_value=True):
        with pytest.raises(TemporalExecutionValidationError, match="System is paused"):
            await svc.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id="test-owner",
                title="Test Run",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {"instructions": "Test workflow fixture."}
                },
                idempotency_key=None,
            )

async def test_create_execution_allowed_when_not_paused(mock_session, mock_client_adapter):
    """create_execution should NOT raise the pause error when system is not paused."""
    svc = TemporalExecutionService(mock_session, client_adapter=mock_client_adapter)

    with patch.object(svc, "check_system_paused", return_value=False):
        try:
            await svc.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id="test-owner",
                title="Test Run",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {"instructions": "Test workflow fixture."}
                },
                idempotency_key=None,
            )
        except TemporalExecutionValidationError as exc:
            # The pause guard specifically should NOT fire
            assert "System is paused" not in str(exc)
        except Exception:
            # Other errors (e.g., DB missing, type parsing) are expected
            pass
