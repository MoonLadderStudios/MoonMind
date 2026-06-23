from __future__ import annotations

from moonmind.workflows.temporal.managed_session_errors import (
    is_managed_session_locator_mismatch_error,
)


def test_managed_session_locator_mismatch_matches_active_session_marker() -> None:
    assert is_managed_session_locator_mismatch_error(
        RuntimeError("sessionEpoch does not match the active managed session")
    )


def test_managed_session_locator_mismatch_matches_durable_record_marker() -> None:
    assert is_managed_session_locator_mismatch_error(
        RuntimeError("threadId does not match the durable managed session record")
    )


def test_managed_session_locator_mismatch_walks_exception_cause_chain() -> None:
    cause = RuntimeError("containerId does not match the active managed session")
    wrapper = RuntimeError("activity failed")
    wrapper.__cause__ = cause

    assert is_managed_session_locator_mismatch_error(wrapper)
