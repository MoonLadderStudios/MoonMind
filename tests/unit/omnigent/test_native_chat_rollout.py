"""Rollout / canary / rollback gate tests for native Workflow Chat (#3642 §10)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moonmind.omnigent.native_chat_rollout import (
    NATIVE_CHAT_ROLLOUT_FLAG,
    NativeChatRolloutMode,
    parse_rollout_mode,
    resolve_native_chat_rollout,
    resolve_native_chat_rollout_with_retirement,
    rollout_flag_is_retired,
    rollout_flag_retirement,
)
from moonmind.omnigent.settings import (
    resolved_native_chat_acceptance_ref,
    resolved_native_chat_rollout_mode,
    resolved_native_chat_rollout_retire_after,
)


def test_unset_flag_defaults_to_canary() -> None:
    assert parse_rollout_mode(None) is NativeChatRolloutMode.CANARY
    assert parse_rollout_mode("") is NativeChatRolloutMode.CANARY
    assert parse_rollout_mode("  ENABLED ") is NativeChatRolloutMode.ENABLED


def test_unknown_mode_fails_closed_to_read_only() -> None:
    # An explicitly set but unrecognized posture never enables interactive Chat.
    assert parse_rollout_mode("garbage") is NativeChatRolloutMode.READ_ONLY


def test_enabled_serves_interactive_regardless_of_recorded_evidence() -> None:
    decision = resolve_native_chat_rollout(
        mode=NativeChatRolloutMode.ENABLED, acceptance_recorded=False
    )
    assert decision.interactive is True
    assert decision.serve_native_ui is True
    assert decision.read_only_fallback is False
    assert decision.telemetry_readiness() == "ready"


def test_disabled_makes_native_chat_unavailable() -> None:
    decision = resolve_native_chat_rollout(
        mode="disabled", acceptance_recorded=True
    )
    assert decision.interactive is False
    assert decision.serve_native_ui is False
    assert decision.read_only_fallback is False
    assert decision.telemetry_readiness() == "unavailable"


def test_read_only_rolls_back_to_diagnostics() -> None:
    decision = resolve_native_chat_rollout(
        mode="read_only", acceptance_recorded=True
    )
    assert decision.interactive is False
    assert decision.serve_native_ui is False
    assert decision.read_only_fallback is True
    assert decision.telemetry_readiness() == "degraded"
    assert decision.reason == "rolled_back_read_only"


def test_canary_requires_recorded_acceptance_evidence() -> None:
    gated = resolve_native_chat_rollout(mode="canary", acceptance_recorded=False)
    assert gated.interactive is False
    assert gated.serve_native_ui is False
    assert gated.read_only_fallback is True
    assert gated.reason == "canary_awaiting_acceptance_evidence"

    admitted = resolve_native_chat_rollout(mode="canary", acceptance_recorded=True)
    assert admitted.interactive is True
    assert admitted.serve_native_ui is True
    assert admitted.reason == "canary_admitted"


def test_settings_defaults_are_gated_off_until_configured() -> None:
    # Empty env: rollout flag resolves blank (→ CANARY default) and no
    # recorded acceptance ref, so interactive Chat remains gated.
    assert resolved_native_chat_rollout_mode(env={}) == ""
    assert resolved_native_chat_acceptance_ref(env={}) == ""
    assert (
        resolved_native_chat_rollout_mode(env={NATIVE_CHAT_ROLLOUT_FLAG: "canary"})
        == "canary"
    )
    assert (
        resolved_native_chat_acceptance_ref(
            env={"OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF": "artifact://report@sha256:x"}
        )
        == "artifact://report@sha256:x"
    )


def test_canary_end_to_end_through_settings() -> None:
    env = {
        NATIVE_CHAT_ROLLOUT_FLAG: "canary",
        "OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF": "artifact://report",
    }
    decision = resolve_native_chat_rollout(
        mode=resolved_native_chat_rollout_mode(env=env),
        acceptance_recorded=bool(resolved_native_chat_acceptance_ref(env=env)),
    )
    assert decision.interactive is True


def test_rollout_flag_is_documented_as_temporary() -> None:
    retirement = rollout_flag_retirement()
    assert retirement.flag == NATIVE_CHAT_ROLLOUT_FLAG
    assert retirement.temporary is True
    assert retirement.steady_state_mode is NativeChatRolloutMode.ENABLED
    assert "acceptance evidence" in retirement.retire_when


def test_rollout_flag_retires_only_after_evidence_and_fallback_window() -> None:
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    deadline = "2026-08-10T11:00:00Z"
    assert rollout_flag_is_retired(
        acceptance_recorded=True, retire_after=deadline, now=now
    )
    assert not rollout_flag_is_retired(
        acceptance_recorded=False, retire_after=deadline, now=now
    )
    assert not rollout_flag_is_retired(
        acceptance_recorded=True,
        retire_after="2026-08-10T13:00:00Z",
        now=now,
    )
    assert not rollout_flag_is_retired(
        acceptance_recorded=True, retire_after="not-a-date", now=now
    )


def test_retired_flag_cannot_roll_canonical_path_back() -> None:
    decision = resolve_native_chat_rollout_with_retirement(
        mode="disabled",
        acceptance_recorded=True,
        retire_after="2026-08-10T11:00:00Z",
        now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
    )
    assert decision.mode is NativeChatRolloutMode.ENABLED
    assert decision.interactive is True
    assert decision.reason == "temporary_rollout_flag_retired"


def test_retirement_deadline_setting_is_explicit_and_blank_by_default() -> None:
    assert resolved_native_chat_rollout_retire_after(env={}) == ""
    assert resolved_native_chat_rollout_retire_after(
        env={"OMNIGENT_NATIVE_CHAT_ROLLOUT_RETIRE_AFTER": "2026-08-10T11:00:00Z"}
    ) == "2026-08-10T11:00:00Z"
