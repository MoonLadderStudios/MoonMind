"""Legacy GitHub credential classification (MoonLadderStudios/MoonMind#4023).

The classifier determines the *effective* legacy credential reference from
trustworthy configuration without resolving, comparing, or probing token
values, so the one migration can map proven intent and suspend only genuinely
conflicting or unreadable choices.
"""

from __future__ import annotations

import json

import pytest

from moonmind.auth.github_credentials import (
    LegacyGitHubIdentityOutcome,
    classify_legacy_github_credential,
)

_TOKEN_A = "ghp_legacyTokenValueAAAAAAAAAAAAAAAAAAAA"
_TOKEN_B = "ghp_legacyTokenValueBBBBBBBBBBBBBBBBBBBB"


def _assert_no_token_material(identity) -> None:
    rendered = json.dumps(
        identity.safe_diagnostic(affected_action="git-default operations")
    ) + identity.model_dump_json()
    assert _TOKEN_A not in rendered
    assert _TOKEN_B not in rendered


def test_absent_configuration_is_absent_with_actionable_correction() -> None:
    identity = classify_legacy_github_credential({})

    assert identity.outcome is LegacyGitHubIdentityOutcome.ABSENT
    assert identity.credential_ref is None
    assert "GITHUB_TOKEN" in (identity.correction or "")


def test_direct_env_token_maps_to_its_env_reference_not_its_value() -> None:
    identity = classify_legacy_github_credential({"GITHUB_TOKEN": _TOKEN_A})

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "env://GITHUB_TOKEN"
    assert identity.source_name == "GITHUB_TOKEN"
    assert identity.secret_ref_parts() == ("env", "GITHUB_TOKEN")
    _assert_no_token_material(identity)


def test_legacy_precedence_picks_first_configured_source_without_merging() -> None:
    # Equal values under distinct names are distinct identities: the legacy
    # precedence decides, values are never compared.
    identity = classify_legacy_github_credential(
        {
            "GH_TOKEN": _TOKEN_A,
            "WORKFLOW_GITHUB_TOKEN": _TOKEN_A,
            "GITHUB_TOKEN_SECRET_REF": "db://github-pat-main",
        }
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "env://GH_TOKEN"
    _assert_no_token_material(identity)


def test_secret_ref_env_maps_to_that_typed_reference() -> None:
    identity = classify_legacy_github_credential(
        {"WORKFLOW_GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"}
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "db://github-pat-main"
    assert identity.secret_ref_parts() == ("db", "github-pat-main")
    assert identity.source_name == "WORKFLOW_GITHUB_TOKEN_SECRET_REF"


def test_settings_file_reference_is_a_configured_source() -> None:
    identity = classify_legacy_github_credential(
        {}, settings_ref="vault://kv/moonmind/github#token"
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "vault://kv/moonmind/github#token"
    assert identity.source_name == "settings.github.github_token_secret_ref"


def test_unreadable_configured_reference_is_distinct_from_absent() -> None:
    # A configured reference that cannot be read failed closed at runtime;
    # it never fell through to a later source, so neither may the migration.
    identity = classify_legacy_github_credential(
        {
            "GITHUB_TOKEN_SECRET_REF": "not-a-reference",
            "MOONMIND_GITHUB_TOKEN_REF": "db://github-pat-main",
        }
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.UNREADABLE
    assert identity.credential_ref is None
    assert identity.source_name == "GITHUB_TOKEN_SECRET_REF"
    assert "GITHUB_TOKEN_SECRET_REF" in (identity.correction or "")


def test_blank_values_are_not_configured() -> None:
    identity = classify_legacy_github_credential(
        {"GITHUB_TOKEN": "   ", "MOONMIND_GITHUB_TOKEN_REF": "db://github-pat-main"}
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "db://github-pat-main"
    assert identity.source_name == "MOONMIND_GITHUB_TOKEN_REF"


def test_operator_setting_alone_is_a_known_explicit_reference() -> None:
    identity = classify_legacy_github_credential(
        {}, operator_setting_refs=("db://github-pat-main",)
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "db://github-pat-main"
    assert identity.source_name == "settings:integrations.github.token_ref"


def test_operator_setting_matching_runtime_source_is_the_same_identity() -> None:
    identity = classify_legacy_github_credential(
        {"GITHUB_TOKEN_SECRET_REF": "db://github-pat-main"},
        operator_setting_refs=("db://github-pat-main",),
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.PROVEN
    assert identity.credential_ref == "db://github-pat-main"


def test_runtime_source_and_different_operator_setting_conflict() -> None:
    identity = classify_legacy_github_credential(
        {"GITHUB_TOKEN": _TOKEN_A},
        operator_setting_refs=("db://github-pat-other",),
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.CONFLICTING
    assert identity.credential_ref is None
    correction = identity.correction or ""
    assert "env://GITHUB_TOKEN" in correction
    assert "db://github-pat-other" in correction
    _assert_no_token_material(identity)


def test_distinct_operator_settings_conflict_instead_of_choosing_one() -> None:
    identity = classify_legacy_github_credential(
        {},
        operator_setting_refs=("db://github-pat-a", "db://github-pat-b"),
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.CONFLICTING


def test_unreadable_operator_setting_is_unreadable() -> None:
    identity = classify_legacy_github_credential(
        {}, operator_setting_refs=("raw-token-value",)
    )

    assert identity.outcome is LegacyGitHubIdentityOutcome.UNREADABLE
    assert "raw-token-value" not in json.dumps(
        identity.safe_diagnostic(affected_action="git-default operations")
    )


@pytest.mark.parametrize(
    "outcome_env",
    [
        {},
        {"GITHUB_TOKEN": _TOKEN_A},
        {"GITHUB_TOKEN_SECRET_REF": "::bad::"},
    ],
)
def test_safe_diagnostic_names_references_and_affected_action_only(
    outcome_env,
) -> None:
    identity = classify_legacy_github_credential(outcome_env)
    diagnostic = identity.safe_diagnostic(
        affected_action="authenticated repository-connection:git-default operations"
    )

    assert diagnostic["outcome"] == identity.outcome.value
    assert diagnostic["affectedAction"].startswith("authenticated")
    assert set(diagnostic) == {
        "outcome",
        "credentialRef",
        "sourceName",
        "considered",
        "correction",
        "affectedAction",
    }
    _assert_no_token_material(identity)
