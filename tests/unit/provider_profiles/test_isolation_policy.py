"""Conformance tests for Provider Profile launch-safety isolation (#3821)."""

from __future__ import annotations

import pytest

from moonmind.provider_profiles.isolation_policy import (
    MAX_ISOLATION_KEYS,
    IsolationPolicyError,
    classify_existing_policy,
    derive_isolation_policy,
    merge_enrollment_policy,
    reconciliation_action,
    resolve_launch_clear_env_keys,
    validate_expert_override_keys,
    validate_isolation_key_shape,
)


def test_codex_oauth_derives_normalized_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="oauth",
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
    )
    assert policy is not None
    assert list(policy.keys) == [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]
    assert policy.editable is False


def test_codex_api_key_derives_normalized_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="api_key",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
    )
    assert policy is not None
    assert "MINIMAX_API_KEY" in policy.keys
    assert "OPENAI_API_KEY" not in policy.keys


def test_claude_oauth_derives_normalized_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="claude_code",
        provider_id="anthropic",
        authentication_method="oauth",
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
    )
    assert policy is not None
    assert set(policy.keys) == {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    }


def test_claude_api_key_derives_normalized_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="claude_code",
        provider_id="anthropic",
        authentication_method="api_key",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
    )
    assert policy is not None
    assert "ANTHROPIC_AUTH_TOKEN" in policy.keys
    assert "ANTHROPIC_API_KEY" not in policy.keys


def test_alternate_provider_materialization_derives_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openrouter",
        authentication_method="api_key",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
    )
    assert policy is not None
    assert "OPENROUTER_API_KEY" in policy.keys
    assert "OPENAI_API_KEY" in policy.keys


def test_credential_free_profile_derives_empty_policy() -> None:
    policy = derive_isolation_policy(
        runtime_id="opencode",
        provider_id="opencode",
        authentication_method="none",
        credential_source="none",
        runtime_materialization_mode="composite",
    )
    assert policy is not None
    assert list(policy.keys) == []


def test_malformed_override_rejected() -> None:
    policy = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="api_key",
    )
    assert policy is not None
    with pytest.raises(IsolationPolicyError):
        validate_expert_override_keys(["bad-key!"], policy=policy)
    with pytest.raises(IsolationPolicyError):
        validate_expert_override_keys(
            ["OPENAI_BASE_URL", "OPENAI_BASE_URL"], policy=policy
        )
    with pytest.raises(IsolationPolicyError):
        validate_expert_override_keys(["PATH"], policy=policy)
    with pytest.raises(IsolationPolicyError):
        validate_expert_override_keys(["DEFINITELY_UNKNOWN_KEY_XYZ"], policy=policy)
    # Override dropping a required strategy key is incompatible.
    with pytest.raises(IsolationPolicyError):
        validate_expert_override_keys(["OPENAI_BASE_URL"], policy=policy)
    with pytest.raises(IsolationPolicyError):
        validate_isolation_key_shape([f"KEY_{i}" for i in range(40)])


def test_stale_persisted_policy_fails_closed_at_launch() -> None:
    with pytest.raises(IsolationPolicyError):
        resolve_launch_clear_env_keys(
            {
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "credential_source": "oauth_volume",
                "runtime_materialization_mode": "oauth_home",
                "clear_env_keys": ["OPENAI_API_KEY"],
            }
        )


def test_launch_boundary_revalidation_uses_derived_policy() -> None:
    effective, meta = resolve_launch_clear_env_keys(
        {
            "runtime_id": "claude_code",
            "provider_id": "anthropic",
            "authentication_method": "api_key",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "clear_env_keys": [
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_API_KEY",
                "OPENAI_API_KEY",
            ],
        }
    )
    assert effective == [
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert meta["source"] == "runtime_provider_isolation_policy"


def test_launch_boundary_missing_policy_fails_closed() -> None:
    with pytest.raises(IsolationPolicyError):
        resolve_launch_clear_env_keys(
            {
                "runtime_id": "custom_runtime",
                "provider_id": "custom_provider",
                "authentication_method": "api_key",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "api_key_env",
                "clear_env_keys": [],
            }
        )


def test_existing_profiles_reconciled_without_erasing_custom() -> None:
    derived = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="api_key",
    )
    assert derived is not None
    assert (
        classify_existing_policy(
            stored_keys=list(derived.keys), derived=derived
        )
        == "current"
    )
    # Superset with known extras is preserved as legacy custom.
    assert (
        classify_existing_policy(
            stored_keys=[*derived.keys, "OPENAI_API_KEY"], derived=derived
        )
        == "legacy_custom"
    )
    merged = merge_enrollment_policy(
        stored_keys=["OPENAI_API_KEY", "CUSTOM_LEGACY_KEY"], derived=derived
    )
    assert "OPENAI_API_KEY" in merged
    assert "CUSTOM_LEGACY_KEY" in merged
    # Incomplete values (missing required strategy keys) are unsafe, not
    # silently normalized away.
    assert (
        classify_existing_policy(stored_keys=["OPENAI_BASE_URL"], derived=derived)
        == "unsafe_unknown_incomplete"
    )
    # Regex-valid custom extras bound to user secrets are preserved as
    # legacy custom, never silently erased.
    assert (
        classify_existing_policy(
            stored_keys=[*derived.keys, "CUSTOM_ENV"], derived=derived
        )
        == "legacy_custom"
    )


def test_no_secret_values_in_policy_errors() -> None:
    try:
        resolve_launch_clear_env_keys(
            {
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "clear_env_keys": [],
            }
        )
    except IsolationPolicyError as exc:
        assert "sk-" not in str(exc)
        assert exc.as_detail()["code"] in {
            "provider_profile_isolation_missing",
            "provider_profile_isolation_stale",
        }
    else:  # pragma: no cover
        raise AssertionError("expected IsolationPolicyError")


def test_blank_stored_keys_classify_unsafe_without_raising() -> None:
    """Legacy rows with blank entries surface a repair diagnostic, not a 500."""
    derived = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="api_key",
    )
    assert derived is not None
    assert (
        classify_existing_policy(stored_keys=["OPENAI_BASE_URL", "  "], derived=derived)
        == "unsafe_unknown_incomplete"
    )
    action = reconciliation_action(stored_keys=[""], derived=derived)
    assert action["action"] == "repair_required"
    assert action["classification"] == "unsafe_unknown_incomplete"


def test_merge_enrollment_policy_rejects_over_limit() -> None:
    """Over-limit merges raise instead of silently truncating preserved keys."""
    derived = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="openai",
        authentication_method="api_key",
    )
    assert derived is not None
    stored = list(derived.keys) + [
        f"CUSTOM_KEY_{i:02d}" for i in range(MAX_ISOLATION_KEYS)
    ]
    with pytest.raises(IsolationPolicyError) as exc_info:
        merge_enrollment_policy(stored_keys=stored, derived=derived)
    assert (
        exc_info.value.as_detail()["code"]
        == "provider_profile_isolation_key_unbounded"
    )


def test_resolve_launch_ignores_legacy_auth_mode() -> None:
    """A retained legacy auth_mode never overrides canonical contract inference."""
    effective, meta = resolve_launch_clear_env_keys(
        {
            "runtime_id": "codex_cli",
            "provider_id": "openai",
            "authentication_method": "volume",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "clear_env_keys": [
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT",
                "MINIMAX_API_KEY",
            ],
        }
    )
    assert meta["strategy_id"] == "codex_cli/openai/api_key"
    assert effective == [
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]


def test_resolve_launch_conflicting_method_prefers_contract() -> None:
    """An explicit method contradicting the credential contract loses."""
    effective, meta = resolve_launch_clear_env_keys(
        {
            "runtime_id": "codex_cli",
            "provider_id": "openai",
            "authentication_method": "oauth",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "clear_env_keys": [
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT",
                "MINIMAX_API_KEY",
            ],
        }
    )
    # The api_key contract wins over the explicit "oauth": the stored OAuth
    # key set is a superset of the derived api_key policy, hence preserved
    # legacy custom under the api_key strategy — not "current" under oauth.
    assert meta["strategy_id"] == "codex_cli/openai/api_key"
    assert meta["source"] == "legacy_custom_preserved"
    assert effective == [
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
    ]
