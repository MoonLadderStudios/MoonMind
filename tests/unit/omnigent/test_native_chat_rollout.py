"""Rollout / canary / rollback gate tests for native Workflow Chat (#3642 §10)."""

from __future__ import annotations

import pytest

from moonmind.omnigent.native_chat_rollout import (
    NATIVE_CHAT_ROLLOUT_FLAG,
    NativeChatRolloutMode,
    parse_rollout_mode,
    resolve_native_chat_rollout,
    rollout_flag_retirement,
)
from moonmind.omnigent.settings import (
    resolved_native_chat_acceptance_ref,
    resolved_native_chat_rollout_mode,
)


def test_unset_flag_defaults_to_enabled() -> None:
    assert parse_rollout_mode(None) is NativeChatRolloutMode.ENABLED
    assert parse_rollout_mode("") is NativeChatRolloutMode.ENABLED
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
    # Empty env: rollout flag resolves blank (→ ENABLED default) and no recorded
    # acceptance ref (→ canary would gate).
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
