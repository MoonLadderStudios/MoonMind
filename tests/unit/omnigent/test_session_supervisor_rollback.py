"""MoonLadderStudios/MoonMind#3712 supervisor rollback control tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent.session_supervisor_rollback import (
    RollbackEffect,
    SessionRollbackContext,
    parse_rollback_mode,
    resolve_rollback_effect,
    rollback_mode_from_settings,
)


def _assert_absolute_invariants(effect: RollbackEffect) -> None:
    # These hold in every mode, unconditionally.
    assert effect.direct_codex_substitution is False
    assert effect.mutates_active_session_authority is False
    assert effect.fenced_handoff_required_for_ownership_transfer is True
    assert effect.existing_session_continues_under_recorded_owner is True
    assert effect.cleanup_preserved is True
    assert effect.replay_preserved is True
    assert effect.historical_reads_preserved is True
    assert effect.diagnostic_reads_preserved is True
    assert effect.chat_binding_urls_preserved is True
    assert effect.evidence_preserved is True


@pytest.mark.parametrize(
    "mode",
    [
        "none",
        "disable_new_admission",
        "disable_new_selection",
        "chat_read_only",
        "revert_default_to_legacy",
        "complete_stop",
    ],
)
def test_absolute_invariants_hold_in_every_mode(mode: str) -> None:
    effect = resolve_rollback_effect(mode=mode)  # type: ignore[arg-type]
    _assert_absolute_invariants(effect)


def test_disable_new_admission_only_blocks_admission() -> None:
    effect = resolve_rollback_effect(mode="disable_new_admission")
    assert effect.new_supervisor_admission_allowed is False
    # Selection and chat remain independently available.
    assert effect.new_omnigent_selection_allowed is True
    assert effect.interactive_native_chat_allowed is True


def test_disable_new_selection_preserves_cleanup_and_replay() -> None:
    effect = resolve_rollback_effect(mode="disable_new_selection")
    assert effect.new_omnigent_selection_allowed is False
    assert effect.new_supervisor_admission_allowed is False
    assert effect.cleanup_preserved is True
    assert effect.replay_preserved is True


def test_chat_read_only_disables_only_interactive_chat() -> None:
    effect = resolve_rollback_effect(mode="chat_read_only")
    assert effect.interactive_native_chat_allowed is False
    assert effect.diagnostic_reads_preserved is True
    assert effect.historical_reads_preserved is True
    # Admission/selection are independent controls, unaffected.
    assert effect.new_supervisor_admission_allowed is True
    assert effect.new_omnigent_selection_allowed is True


def test_complete_stop_stops_new_work_without_direct_codex() -> None:
    effect = resolve_rollback_effect(mode="complete_stop")
    assert effect.new_supervisor_admission_allowed is False
    assert effect.new_omnigent_selection_allowed is False
    assert effect.interactive_native_chat_allowed is False
    assert effect.direct_codex_substitution is False


def test_revert_default_to_legacy_only_when_supported() -> None:
    supported = resolve_rollback_effect(
        mode="revert_default_to_legacy",
        context=SessionRollbackContext(legacyPathSupported=True),
    )
    assert supported.legacy_default_for_new_sessions is True
    assert supported.reason_code == "revert_default_to_legacy"

    unsupported = resolve_rollback_effect(
        mode="revert_default_to_legacy",
        context=SessionRollbackContext(legacyPathSupported=False),
    )
    # Fail closed rather than silently reroute anywhere.
    assert unsupported.legacy_default_for_new_sessions is False
    assert unsupported.new_omnigent_selection_allowed is False
    assert unsupported.reason_code == "legacy_path_unsupported"
    assert unsupported.direct_codex_substitution is False


def test_provider_capacity_release_requires_consumers_stopped() -> None:
    not_stopped = resolve_rollback_effect(
        mode="complete_stop",
        context=SessionRollbackContext(credentialConsumersStopped=False),
    )
    assert not_stopped.provider_capacity_release_allowed is False

    stopped = resolve_rollback_effect(
        mode="complete_stop",
        context=SessionRollbackContext(credentialConsumersStopped=True),
    )
    assert stopped.provider_capacity_release_allowed is True


def test_parse_rollback_mode_normalizes_and_fails_closed() -> None:
    assert parse_rollback_mode("disable-new-admission") == "disable_new_admission"
    assert parse_rollback_mode("") == "none"
    assert parse_rollback_mode(None) == "none"
    with pytest.raises(ValueError):
        parse_rollback_mode("route_to_codex")


def test_rollback_mode_from_settings() -> None:
    flags = SimpleNamespace(
        omnigent_session_supervisor_rollback_mode="chat_read_only"
    )
    assert rollback_mode_from_settings(flags) == "chat_read_only"
