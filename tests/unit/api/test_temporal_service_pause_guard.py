"""Unit tests for API guard: workflow submission blocked when system paused (DOC-REQ-001/004/005)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

pytestmark = [pytest.mark.asyncio]


def _make_pause_state(paused: bool):
    """Create a minimal mock pause state object."""
    state = MagicMock()
    state.paused = paused
    return state


# ---- check_system_paused ----


async def test_check_system_paused_returns_true():
    """check_system_paused should return True when pause state is active."""
    session = AsyncMock()
    adapter = AsyncMock()
    svc = TemporalExecutionService(session, client_adapter=adapter)

    with patch(
        "unittest.mock.MagicMock",
    ) as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.get_pause_state = AsyncMock(
            return_value=_make_pause_state(paused=True)
        )
        MockRepo.return_value = repo_instance

        result = await svc.check_system_paused()
        assert result is True
        MockRepo.assert_called_once_with(session)


async def test_check_system_paused_returns_false():
    """check_system_paused should return False when pause state is inactive."""
    session = AsyncMock()
    adapter = AsyncMock()
    svc = TemporalExecutionService(session, client_adapter=adapter)

    with patch(
        "unittest.mock.MagicMock",
    ) as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.get_pause_state = AsyncMock(
            return_value=_make_pause_state(paused=False)
        )
        MockRepo.return_value = repo_instance

        result = await svc.check_system_paused()
        assert result is False


# ---- API Guard on create_execution ----


async def test_create_execution_blocked_when_paused():
    """create_execution should raise TemporalExecutionValidationError when system is paused."""
    session = AsyncMock()
    adapter = AsyncMock()
    svc = TemporalExecutionService(session, client_adapter=adapter)

    with patch.object(svc, "check_system_paused", return_value=True):
        with pytest.raises(TemporalExecutionValidationError, match="System is paused"):
            await svc.create_execution(
                workflow_type="MoonMind.Run",
                owner_id="test-owner",
                title="Test Run",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=None,
                idempotency_key=None,
            )


async def test_create_execution_allowed_when_not_paused():
    """create_execution should NOT raise the pause error when system is not paused."""
    session = AsyncMock()
    adapter = AsyncMock()
    svc = TemporalExecutionService(session, client_adapter=adapter)

    with patch.object(svc, "check_system_paused", return_value=False):
        try:
            await svc.create_execution(
                workflow_type="MoonMind.Run",
                owner_id="test-owner",
                title="Test Run",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=None,
                idempotency_key=None,
            )
        except TemporalExecutionValidationError as exc:
            # The pause guard specifically should NOT fire
            assert "System is paused" not in str(exc)
        except Exception:
            # Other errors (e.g., DB missing, type parsing) are expected
            pass
