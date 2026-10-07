"""Recorded Provider Profile projection (MoonLadderStudios/MoonMind#4640)."""

from __future__ import annotations

import json
from pathlib import Path

from moonmind.workflows.executions.provider_profile_projection import (
    PROVIDER_PROFILE_MEMO_KEY,
    build_provider_profile_projection,
    merge_resolved_provider_profile,
    provider_profile_id_token,
    provider_profile_state_token,
    provider_profile_summary_from_memo,
)

_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "frontend/src/runtime/fixtures/profile-first-authoring.json"
)


def _admitted_parameters_from_fixture() -> dict:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    selection = fixture["provider"]["execution_selection"]
    configuration = fixture["configuration"]
    version = configuration["versions"][0]
    task = fixture["request"]["payload"]["task"]
    # Admission compiles the trusted snapshot: the Provider Profile ID lives in
    # providerProfileRef while agentProfile.profileId is the execution
    # configuration id that must never be shown as the account.
    return {
        "targetRuntime": "omnigent",
        "profileId": selection["providerProfileRef"],
        "agentProfile": {
            "profileId": selection["profileId"],
            "version": selection["version"],
            "digest": selection["digest"],
        },
        "agentProfileSnapshot": {
            "profileId": selection["profileId"],
            "providerProfileRef": selection["providerProfileRef"],
            "document": version["document"],
        },
        "task": task,
    }


def test_single_recorded_profile_uses_snapshot_label_and_harness() -> None:
    params = _admitted_parameters_from_fixture()

    summary, search_value = build_provider_profile_projection(
        params, labels={"opencode-go": "OpenCode Go"}
    )

    assert summary == {
        "selectionState": "recorded",
        "profiles": [
            {"id": "opencode-go", "label": "OpenCode Go", "harness": "opencode-native"}
        ],
        "profileCount": 1,
    }
    assert "profile-opencode-native" not in json.dumps(summary)
    assert search_value.split() == [
        provider_profile_state_token("recorded"),
        provider_profile_id_token("opencode-go"),
    ]
    assert provider_profile_id_token("profile-opencode-native") not in search_value


def test_id_only_profile_keeps_stable_id_without_guessed_label() -> None:
    summary, _ = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "profileId": "acct-legacy"}, labels={}
    )

    assert summary["profiles"] == [{"id": "acct-legacy"}]
    row = provider_profile_summary_from_memo({PROVIDER_PROFILE_MEMO_KEY: summary})
    assert row["profiles"] == [{"id": "acct-legacy", "label": None, "harness": None}]


def test_step_profile_variations_are_recorded_once_each() -> None:
    params = {
        "targetRuntime": "omnigent",
        "profileId": "profile-a",
        "task": {
            "steps": [
                {"runtime": {"mode": "omnigent", "providerProfileRef": "profile-b"}},
                {"runtime": {"mode": "omnigent", "inheritedProfileId": "profile-a"}},
                {"runtime": {"mode": "omnigent", "providerProfileRef": "profile-b"}},
                {"runtime": {"mode": "omnigent", "providerProfileRef": "profile-a"}},
            ]
        },
    }

    summary, search_value = build_provider_profile_projection(
        params, labels={"profile-a": "Work", "profile-b": "Work"}
    )

    assert summary["selectionState"] == "recorded"
    assert [item["id"] for item in summary["profiles"]] == ["profile-a", "profile-b"]
    assert summary["profileCount"] == 2
    assert search_value.split().count(provider_profile_id_token("profile-b")) == 1


def test_unresolved_and_not_applicable_states_never_guess_from_runtime() -> None:
    pending, pending_value = build_provider_profile_projection(
        {"targetRuntime": "codex_cli", "model": "gpt-5", "runtime": {"mode": "codex_cli"}}
    )
    no_agent, no_agent_value = build_provider_profile_projection(
        {"task": {"tool": {"name": "repo.sync"}}}
    )

    assert pending == {"selectionState": "pending", "profiles": [], "profileCount": 0}
    assert pending_value == provider_profile_state_token("pending")
    assert no_agent == {
        "selectionState": "not_applicable",
        "profiles": [],
        "profileCount": 0,
    }
    assert no_agent_value == provider_profile_state_token("not_applicable")


def test_profiles_beyond_display_bound_stay_indexed() -> None:
    steps = [
        {"runtime": {"mode": "omnigent", "providerProfileRef": f"profile-{index}"}}
        for index in range(12)
    ]
    summary, search_value = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "task": {"steps": steps}}
    )

    assert len(summary["profiles"]) == 8
    assert summary["profileCount"] == 12
    assert provider_profile_id_token("profile-11") in search_value.split()


def test_historical_memo_without_summary_is_not_recorded() -> None:
    assert provider_profile_summary_from_memo({"title": "Old run"}) == {
        "selectionState": "not_recorded",
        "profiles": [],
        "profileCount": 0,
    }
    assert provider_profile_summary_from_memo(None)["selectionState"] == "not_recorded"


def test_renamed_or_deleted_live_profile_keeps_recorded_label() -> None:
    recorded, _ = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "profileId": "acct-1"},
        labels={"acct-1": "Primary (old name)"},
    )

    # The memo is the snapshot; no live inventory lookup participates in reads.
    row = provider_profile_summary_from_memo({PROVIDER_PROFILE_MEMO_KEY: recorded})
    assert row["profiles"][0]["label"] == "Primary (old name)"


def test_tokens_are_single_lowercase_alphanumeric_terms() -> None:
    for profile_id in ("profile-a", "Work: Primary", "a'b|c&d", "ünïcode"):
        token = provider_profile_id_token(profile_id)
        assert token.isalnum() and token == token.lower()
    for state in ("recorded", "pending", "not_applicable"):
        token = provider_profile_state_token(state)
        assert token.isalnum() and token == token.lower()


def test_launch_resolved_profile_turns_pending_into_recorded() -> None:
    pending, pending_value = build_provider_profile_projection(
        {"targetRuntime": "codex_cli", "runtime": {"mode": "codex_cli"}}
    )

    merged = merge_resolved_provider_profile(
        pending, pending_value, "acct-used", label="Work"
    )

    assert merged is not None
    summary, search_value = merged
    assert summary == {
        "selectionState": "recorded",
        "profiles": [{"id": "acct-used", "label": "Work"}],
        "profileCount": 1,
    }
    assert search_value.split() == [
        provider_profile_state_token("recorded"),
        provider_profile_id_token("acct-used"),
    ]
    row = provider_profile_summary_from_memo({PROVIDER_PROFILE_MEMO_KEY: summary})
    assert row["selectionState"] == "recorded"
    assert row["profiles"][0] == {"id": "acct-used", "label": "Work", "harness": None}


def test_launch_resolution_of_already_recorded_profile_is_a_no_op() -> None:
    recorded, value = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "profileId": "acct-1"},
        labels={"acct-1": "Admitted name"},
    )

    assert (
        merge_resolved_provider_profile(recorded, value, "acct-1", label="Live name")
        is None
    )


def test_launch_resolved_profile_adds_a_distinct_recorded_association() -> None:
    recorded, value = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "profileId": "acct-1"},
        labels={"acct-1": "Admitted name"},
    )

    merged = merge_resolved_provider_profile(recorded, value, "acct-2")

    assert merged is not None
    summary, search_value = merged
    assert summary["selectionState"] == "recorded"
    assert summary["profiles"] == [
        {"id": "acct-1", "label": "Admitted name"},
        {"id": "acct-2"},
    ]
    assert summary["profileCount"] == 2
    tokens = search_value.split()
    assert tokens.count(provider_profile_state_token("recorded")) == 1
    assert provider_profile_id_token("acct-1") in tokens
    assert tokens.count(provider_profile_id_token("acct-2")) == 1
    # Folding the same launch again counts once.
    assert merge_resolved_provider_profile(summary, search_value, "acct-2") is None


def test_launch_resolution_beyond_display_bound_uses_indexed_membership() -> None:
    steps = [
        {"runtime": {"mode": "omnigent", "providerProfileRef": f"profile-{index}"}}
        for index in range(12)
    ]
    summary, value = build_provider_profile_projection(
        {"targetRuntime": "omnigent", "task": {"steps": steps}}
    )

    # profile-11 is indexed but beyond the memo bound: already recorded.
    assert merge_resolved_provider_profile(summary, value, "profile-11") is None
    merged = merge_resolved_provider_profile(summary, value, "profile-new")
    assert merged is not None
    assert len(merged[0]["profiles"]) == 8
    assert merged[0]["profileCount"] == 13
    assert provider_profile_id_token("profile-new") in merged[1].split()
    assert provider_profile_id_token("profile-11") in merged[1].split()


def test_launch_resolution_never_invents_a_missing_admission_projection() -> None:
    assert merge_resolved_provider_profile(None, None, "acct-1") is None
    assert merge_resolved_provider_profile({"title": "Old run"}, None, "acct-1") is None
    pending, value = build_provider_profile_projection({"targetRuntime": "codex_cli"})
    assert merge_resolved_provider_profile(pending, value, "  ") is None
