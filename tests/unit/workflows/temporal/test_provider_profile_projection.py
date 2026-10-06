"""Recorded Provider Profile projection (MoonLadderStudios/MoonMind#4640)."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.provider_profile_projection import (
    PROVIDER_PROFILE_SELECTION_PARAMETER,
    RecordedProviderProfile,
    build_provider_profile_selection,
    provider_profile_id_token,
    provider_profile_ids_from_search_attribute,
    provider_profile_search_attribute_value,
    provider_profile_state_token,
    summarize_recorded_provider_profiles,
)


def test_admitted_single_profile_keeps_snapshot_label_and_harness() -> None:
    selection = build_provider_profile_selection(
        profiles=[
            RecordedProviderProfile(
                profile_id="codex-work", label="OpenAI · Work", harness="codex_cli"
            )
        ],
        agent_applicable=True,
    )

    summary = summarize_recorded_provider_profiles(
        {PROVIDER_PROFILE_SELECTION_PARAMETER: selection}
    )

    assert summary.selection_state == "recorded"
    assert summary.profiles == (
        RecordedProviderProfile(
            profile_id="codex-work", label="OpenAI · Work", harness="codex_cli"
        ),
    )


def test_multiple_profiles_dedupe_by_stable_id_preserving_order() -> None:
    selection = build_provider_profile_selection(
        profiles=[
            RecordedProviderProfile(profile_id="a", label="Work"),
            RecordedProviderProfile(profile_id="b", label="Work"),
            RecordedProviderProfile(profile_id="a", label="Work"),
        ],
        agent_applicable=True,
    )

    summary = summarize_recorded_provider_profiles(
        {PROVIDER_PROFILE_SELECTION_PARAMETER: selection}
    )

    assert summary.selection_state == "recorded"
    assert [profile.profile_id for profile in summary.profiles] == ["a", "b"]
    # Equal labels never collapse distinct accounts.
    assert [profile.label for profile in summary.profiles] == ["Work", "Work"]


def test_unresolved_agent_selection_is_pending_not_a_default_guess() -> None:
    selection = build_provider_profile_selection(profiles=[], agent_applicable=True)

    summary = summarize_recorded_provider_profiles(
        {PROVIDER_PROFILE_SELECTION_PARAMETER: selection, "targetRuntime": "codex_cli"}
    )

    assert summary.selection_state == "pending"
    assert summary.profiles == ()


def test_no_agent_selection_is_not_applicable() -> None:
    selection = build_provider_profile_selection(profiles=[], agent_applicable=False)

    summary = summarize_recorded_provider_profiles(
        {PROVIDER_PROFILE_SELECTION_PARAMETER: selection}
    )

    assert summary.selection_state == "not_applicable"


def test_historical_row_with_only_ids_shows_ids_without_guessed_names() -> None:
    summary = summarize_recorded_provider_profiles(
        {
            "targetRuntime": "omnigent",
            "profileId": "provider-x",
            "agentProfileSnapshot": {
                "profileId": "exec-config-1",
                "providerProfileRef": "provider-x",
            },
            "agentProfile": {"profileId": "exec-config-1"},
            "workflow": {
                "steps": [
                    {"runtime": {"providerProfileRef": "provider-y"}},
                    {"runtime": {"inheritedProfileId": "provider-x"}},
                    {"agentProfile": {"profileId": "exec-config-2"}},
                ]
            },
        }
    )

    assert summary.selection_state == "recorded"
    assert summary.profiles == (
        RecordedProviderProfile(profile_id="provider-x"),
        RecordedProviderProfile(profile_id="provider-y"),
    )


def test_execution_configuration_profile_id_never_masquerades_as_provider_profile() -> None:
    summary = summarize_recorded_provider_profiles(
        {
            "targetRuntime": "omnigent",
            "agentProfile": {"profileId": "exec-config-1"},
            "workflow": {"steps": [{"agentProfile": {"profileId": "exec-config-2"}}]},
        }
    )

    assert summary.selection_state == "not_recorded"
    assert summary.profiles == ()


def test_historical_row_without_association_is_not_recorded_even_with_runtime() -> None:
    # Runtime, provider, model, and present defaults cannot establish a
    # missing historical association.
    summary = summarize_recorded_provider_profiles(
        {"targetRuntime": "codex_cli", "model": "gpt-5", "provider": "openai"}
    )

    assert summary.selection_state == "not_recorded"
    assert summary.profiles == ()


def test_malformed_snapshot_falls_back_to_recorded_ids() -> None:
    summary = summarize_recorded_provider_profiles(
        {
            PROVIDER_PROFILE_SELECTION_PARAMETER: {"state": "bogus", "profiles": "x"},
            "profileId": "provider-x",
        }
    )

    assert summary.selection_state == "recorded"
    assert summary.profiles == (RecordedProviderProfile(profile_id="provider-x"),)


def test_snapshot_values_are_bounded_and_secret_free() -> None:
    selection = build_provider_profile_selection(
        profiles=[
            RecordedProviderProfile(
                profile_id=f"profile-{index}", label="L" * 500, harness="codex_cli"
            )
            for index in range(40)
        ],
        agent_applicable=True,
    )

    assert set(selection) == {"state", "profiles"}
    assert len(selection["profiles"]) == 32
    assert all(set(item) <= {"id", "label", "harness"} for item in selection["profiles"])
    assert all(len(item["label"]) <= 120 for item in selection["profiles"])


@pytest.mark.parametrize(
    ("state", "token"),
    [
        ("recorded", "ppstaterecorded"),
        ("pending", "ppstatepending"),
        ("not_recorded", "ppstatenotrecorded"),
        ("not_applicable", "ppstatenotapplicable"),
    ],
)
def test_state_tokens_are_plain_lexemes(state: str, token: str) -> None:
    assert provider_profile_state_token(state) == token


def test_state_token_rejects_unknown_state() -> None:
    with pytest.raises(ValueError):
        provider_profile_state_token("unknown")


def test_search_attribute_value_round_trips_ids_and_state() -> None:
    summary = summarize_recorded_provider_profiles(
        {
            PROVIDER_PROFILE_SELECTION_PARAMETER: build_provider_profile_selection(
                profiles=[
                    RecordedProviderProfile(profile_id="OpenAI: Work/1"),
                    RecordedProviderProfile(profile_id="b"),
                ],
                agent_applicable=True,
            )
        }
    )

    value = provider_profile_search_attribute_value(summary)

    tokens = value.split(" ")
    assert "ppstaterecorded" in tokens
    assert provider_profile_id_token("OpenAI: Work/1") in tokens
    # Every token is a lowercase alphanumeric lexeme that is safe for the
    # PostgreSQL tsvector/tsquery casts and SQLite FTS tokenizers.
    assert all(token.isalnum() and token == token.lower() for token in tokens)
    assert provider_profile_ids_from_search_attribute(value) == ["OpenAI: Work/1", "b"]


def test_search_attribute_value_for_absence_state_has_no_ids() -> None:
    summary = summarize_recorded_provider_profiles({"targetRuntime": "codex_cli"})

    assert provider_profile_search_attribute_value(summary) == "ppstatenotrecorded"
    assert provider_profile_ids_from_search_attribute("ppstatenotrecorded") == []
    assert provider_profile_ids_from_search_attribute(None) == []


def test_search_attribute_value_stays_within_temporal_value_size_limit() -> None:
    summary = summarize_recorded_provider_profiles(
        {
            PROVIDER_PROFILE_SELECTION_PARAMETER: build_provider_profile_selection(
                profiles=[
                    RecordedProviderProfile(profile_id=f"{index:03d}-" + "x" * 120)
                    for index in range(32)
                ],
                agent_applicable=True,
            )
        }
    )

    value = provider_profile_search_attribute_value(summary)

    # Temporal rejects Search Attribute values over 2 KiB by default, which
    # would fail workflow start; the state token and leading IDs are kept.
    assert len(value.encode("utf-8")) <= 2000
    assert value.startswith("ppstaterecorded ")
    assert provider_profile_ids_from_search_attribute(value)[0] == "000-" + "x" * 120
