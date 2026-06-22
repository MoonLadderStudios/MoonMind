"""Per-runtime conformance/parity matrix for session-aware Live Logs.

GitHub issue: MoonLadderStudios/MoonMind#2558

Every managed runtime that *claims* session-aware Live Logs support must keep
emitting the required session/turn/reset lifecycle events. These tests bind the
declarative conformance registry
(:data:`MANAGED_SESSION_LIVE_LOGS_CONFORMANCE`) to the actual emission sites in
the runtime so a declaration cannot drift away from real behavior, and so that
adding a new session-aware runtime forces a conformance entry.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from moonmind.schemas.agent_runtime_models import (
    MANAGED_SESSION_LIVE_LOGS_CONFORMANCE,
    ObservabilityEventKind,
    REQUIRED_SESSION_AWARE_OBSERVABILITY_EVENT_KINDS,
    expected_observability_event_kinds,
    runtime_claims_session_aware_live_logs,
)
from moonmind.workflows.temporal.runtime import (
    managed_session_controller,
    managed_session_supervisor,
)

_VALID_EVENT_KINDS = frozenset(get_args(ObservabilityEventKind))

# The emission source modules for each session-aware runtime. The conformance
# test asserts that every declared event kind is actually emitted by one of
# these modules.
_RUNTIME_EMISSION_SOURCES: dict[str, tuple[object, ...]] = {
    "codex_cli": (
        managed_session_controller,
        managed_session_supervisor,
    ),
}

# Runtimes that are explicitly NOT session-aware managed-session runtimes. They
# must not appear to claim session-aware Live Logs support.
_NON_SESSION_AWARE_RUNTIMES = (
    "claude_code",
    "codex_cloud",
    "jules",
    "gemini_cli",
    "",
)


def _emission_source_text(runtime_id: str) -> str:
    modules = _RUNTIME_EMISSION_SOURCES[runtime_id]
    return "\n".join(inspect.getsource(module) for module in modules)


def _kind_is_emitted(kind: str, source_text: str) -> bool:
    if f'kind="{kind}"' in source_text:
        return True
    # stdout_chunk / stderr_chunk are emitted via an f-string over the
    # stream name (``kind=f"{stream_name}_chunk"``).
    if kind.endswith("_chunk") and 'kind=f"{stream_name}_chunk"' in source_text:
        return True
    return False


@pytest.mark.parametrize("runtime_id", sorted(MANAGED_SESSION_LIVE_LOGS_CONFORMANCE))
def test_declared_kinds_are_valid_event_kinds(runtime_id: str) -> None:
    declared = MANAGED_SESSION_LIVE_LOGS_CONFORMANCE[runtime_id]
    assert declared, f"{runtime_id} declares no observability event kinds"
    unknown = declared - _VALID_EVENT_KINDS
    assert not unknown, f"{runtime_id} declares unknown event kinds: {sorted(unknown)}"


@pytest.mark.parametrize("runtime_id", sorted(MANAGED_SESSION_LIVE_LOGS_CONFORMANCE))
def test_required_session_aware_kinds_are_declared(runtime_id: str) -> None:
    declared = MANAGED_SESSION_LIVE_LOGS_CONFORMANCE[runtime_id]
    missing = REQUIRED_SESSION_AWARE_OBSERVABILITY_EVENT_KINDS - declared
    assert not missing, (
        f"{runtime_id} claims session-aware Live Logs support but does not "
        f"declare required lifecycle kinds: {sorted(missing)}"
    )


@pytest.mark.parametrize("runtime_id", sorted(MANAGED_SESSION_LIVE_LOGS_CONFORMANCE))
def test_every_declared_kind_is_actually_emitted(runtime_id: str) -> None:
    """Bind the declaration to real emission sites in the runtime source."""
    assert runtime_id in _RUNTIME_EMISSION_SOURCES, (
        f"{runtime_id} claims session-aware support but has no registered "
        "emission source modules for conformance checking"
    )
    source_text = _emission_source_text(runtime_id)
    declared = MANAGED_SESSION_LIVE_LOGS_CONFORMANCE[runtime_id]
    not_emitted = sorted(
        kind for kind in declared if not _kind_is_emitted(kind, source_text)
    )
    assert not not_emitted, (
        f"{runtime_id} declares event kinds that are not emitted anywhere in "
        f"its runtime source: {not_emitted}"
    )


def test_codex_emits_session_resume_and_terminate_lifecycle() -> None:
    """Regression guard: Codex consistently emits resume/terminate events."""
    controller_source = inspect.getsource(managed_session_controller)
    assert 'kind="session_resumed"' in controller_source
    assert 'kind="session_terminated"' in controller_source
    # Turn lifecycle on the same controller path.
    assert 'kind="turn_started"' in controller_source
    assert 'kind="turn_completed"' in controller_source
    assert 'kind="turn_interrupted"' in controller_source


def test_codex_claims_session_aware_live_logs() -> None:
    assert runtime_claims_session_aware_live_logs("codex_cli") is True
    # Canonicalization: legacy/alias spellings resolve to codex_cli.
    assert runtime_claims_session_aware_live_logs("codex") is True
    assert expected_observability_event_kinds("codex_cli") == (
        MANAGED_SESSION_LIVE_LOGS_CONFORMANCE["codex_cli"]
    )
    assert (
        REQUIRED_SESSION_AWARE_OBSERVABILITY_EVENT_KINDS
        <= expected_observability_event_kinds("codex")
    )


@pytest.mark.parametrize("runtime_id", _NON_SESSION_AWARE_RUNTIMES)
def test_non_session_aware_runtimes_do_not_claim_support(runtime_id: str) -> None:
    assert runtime_claims_session_aware_live_logs(runtime_id) is False
    assert expected_observability_event_kinds(runtime_id) == frozenset()
