"""Rollout / canary / rollback gate tests for native Workflow Chat (#3642 §10)."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.native_chat_rollout import (
    NATIVE_CHAT_ROLLOUT_FLAG,
    NativeChatRolloutMode,
    ValidatedNativeChatAcceptance,
    current_native_chat_rollout_decision,
    load_native_chat_acceptance_report,
    native_chat_deployment_identity,
    parse_rollout_mode,
    resolve_native_chat_rollout,
    rollout_flag_retirement,
)
from moonmind.omnigent.settings import (
    resolved_native_chat_acceptance_ref,
    resolved_native_chat_rollout_mode,
)
from tests.unit.omnigent.test_native_chat_acceptance import _source


def test_unset_flag_defaults_to_canary() -> None:
    assert parse_rollout_mode(None) is NativeChatRolloutMode.CANARY
    assert parse_rollout_mode("") is NativeChatRolloutMode.CANARY
    assert parse_rollout_mode("  ENABLED ") is NativeChatRolloutMode.ENABLED


def test_unknown_mode_fails_closed_to_read_only() -> None:
    # An explicitly set but unrecognized posture never enables interactive Chat.
    assert parse_rollout_mode("garbage") is NativeChatRolloutMode.READ_ONLY


def _validated() -> ValidatedNativeChatAcceptance:
    return ValidatedNativeChatAcceptance(ref="file:///report", sha256="a" * 64, report={})


def test_enabled_requires_validated_evidence() -> None:
    gated = resolve_native_chat_rollout(mode=NativeChatRolloutMode.ENABLED, acceptance=None)
    assert gated.interactive is False
    decision = resolve_native_chat_rollout(mode=NativeChatRolloutMode.ENABLED, acceptance=_validated())
    assert decision.interactive is True
    assert decision.serve_native_ui is True
    assert decision.read_only_fallback is False
    assert decision.telemetry_readiness() == "ready"


def test_disabled_makes_native_chat_unavailable() -> None:
    decision = resolve_native_chat_rollout(
        mode="disabled", acceptance=_validated()
    )
    assert decision.interactive is False
    assert decision.serve_native_ui is False
    assert decision.read_only_fallback is False
    assert decision.telemetry_readiness() == "unavailable"


def test_read_only_rolls_back_to_diagnostics() -> None:
    decision = resolve_native_chat_rollout(
        mode="read_only", acceptance=_validated()
    )
    assert decision.interactive is False
    assert decision.serve_native_ui is False
    assert decision.read_only_fallback is True
    assert decision.telemetry_readiness() == "degraded"
    assert decision.reason == "rolled_back_read_only"


def test_canary_requires_recorded_acceptance_evidence() -> None:
    gated = resolve_native_chat_rollout(mode="canary", acceptance=None)
    assert gated.interactive is False
    assert gated.serve_native_ui is False
    assert gated.read_only_fallback is True
    assert gated.reason == "canary_awaiting_acceptance_evidence"

    admitted = resolve_native_chat_rollout(mode="canary", acceptance=_validated())
    assert admitted.interactive is True
    assert admitted.serve_native_ui is True
    assert admitted.reason == "canary_admitted"


def test_settings_defaults_are_gated_off_until_configured() -> None:
    # Empty env: rollout flag resolves blank (→ CANARY default) and no report.
    assert resolved_native_chat_rollout_mode(env={}) == ""
    assert resolved_native_chat_acceptance_ref(env={}) == ""
    assert (
        resolved_native_chat_rollout_mode(env={NATIVE_CHAT_ROLLOUT_FLAG: "canary"})
        == "canary"
    )
    assert (
        resolved_native_chat_acceptance_ref(
            env={"OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF": "file:///report#sha256=" + "a" * 64}
        )
        == "file:///report#sha256=" + "a" * 64
    )


def _identity_env() -> dict[str, str]:
    digest = "a" * 64
    return {
        "MOONMIND_BUILD_COMMIT": "abc123",
        "OMNIGENT_IMAGE_REF": f"server@sha256:{digest}",
        "OMNIGENT_NATIVE_UI_IMAGE_REF": f"ui@sha256:{digest}",
        "OMNIGENT_HOST_IMAGE_REF": f"host@sha256:{digest}",
    }


def _published_report(identity: dict) -> dict:
    source = _source()
    identities = {
        **identity,
        "moonmindBuild": "build",
        "hostArchitecture": "linux/amd64",
    }
    source["identities"] = copy.deepcopy(identities)
    for item in source["evidenceObjects"].values():
        item["identities"] = copy.deepcopy(identities)
    from moonmind.omnigent.native_chat_acceptance import (
        build_native_chat_acceptance_report,
    )

    return build_native_chat_acceptance_report(
        source,
        expected_commit=str(identity["moonmindCommit"]),
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )


def _write_report(tmp_path: Path, report: dict) -> str:
    path = tmp_path / "report.json"
    raw = (json.dumps(report, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return path.as_uri() + "#sha256=" + hashlib.sha256(raw).hexdigest()


def test_canary_end_to_end_resolves_current_report(tmp_path: Path) -> None:
    env = _identity_env()
    identity = native_chat_deployment_identity(env=env)
    assert identity is not None
    env[NATIVE_CHAT_ROLLOUT_FLAG] = "canary"
    env["OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF"] = _write_report(
        tmp_path, _published_report(identity)
    )
    decision = current_native_chat_rollout_decision(
        env=env, now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    )
    assert decision.interactive is True


def test_nonempty_dangling_or_mismatched_ref_never_admits(tmp_path: Path) -> None:
    env = _identity_env()
    env["OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF"] = "artifact://arbitrary-nonempty"
    assert current_native_chat_rollout_decision(env=env).interactive is False

    identity = native_chat_deployment_identity(env=env)
    assert identity is not None
    report = _published_report(identity)
    report["identities"]["moonmindCommit"] = "other"
    env["OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF"] = _write_report(tmp_path, report)
    decision = current_native_chat_rollout_decision(
        env=env, now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    )
    assert decision.interactive is False
    assert decision.reason == "acceptance_evidence_invalid_or_stale"


def test_shallow_self_asserted_report_never_admits(tmp_path: Path) -> None:
    env = _identity_env()
    identity = native_chat_deployment_identity(env=env)
    assert identity is not None
    report = _published_report(identity)
    report.pop("evidenceObjects")
    env["OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF"] = _write_report(tmp_path, report)

    decision = current_native_chat_rollout_decision(
        env=env, now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    )

    assert decision.interactive is False
    assert decision.reason == "acceptance_evidence_invalid_or_stale"


def test_report_loader_binds_exact_bytes(tmp_path: Path) -> None:
    identity = native_chat_deployment_identity(env=_identity_env())
    assert identity is not None
    ref = _write_report(tmp_path, _published_report(identity))
    loaded = load_native_chat_acceptance_report(
        ref,
        deployed_identity=identity,
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )
    assert loaded.sha256 in ref
    with pytest.raises(ValueError, match="digest mismatch"):
        load_native_chat_acceptance_report(
            ref.replace(loaded.sha256, "b" * 64),
            deployed_identity=identity,
            now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        )


def test_rollout_flag_is_documented_as_temporary() -> None:
    retirement = rollout_flag_retirement()
    assert retirement.flag == NATIVE_CHAT_ROLLOUT_FLAG
    assert retirement.temporary is True
    assert retirement.steady_state_mode is NativeChatRolloutMode.CANARY
    assert "acceptance evidence" in retirement.retire_when
