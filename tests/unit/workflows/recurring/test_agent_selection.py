"""Re-authoring a saved schedule target with a new agent selection."""

from __future__ import annotations

import copy

import pytest

from moonmind.workflows.recurring.agent_selection import (
    ScheduleAgentSelectionError,
    replace_schedule_agent_selection,
    saved_provider_profile_ref,
)

_SNAPSHOT = {"profileId": "omnigent-claude", "providerProfileRef": "claude-work"}
_PLAN = {"planRef": "omnigent-execution-plan:sha256:" + "b" * 64}


def _admitted_target() -> dict:
    """An Omnigent schedule as Create saves it: Tier 2 resolved and compiled."""

    return {
        "workflowType": "MoonMind.UserWorkflow",
        "agentProfile": {"profileId": "omnigent-claude", "version": 3},
        "agentProfileSnapshot": dict(_SNAPSHOT),
        "omnigentAuthorityArtifactRefs": ["art_task", "art_plan"],
        "runtimeProviderTarget": {"targetId": "omnigent-claude@1"},
        "initialParameters": {
            "repository": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
            "targetRuntime": "omnigent",
            "runtimeCutover": {"runtimeId": "omnigent", "selectionSource": "authored"},
            "profileId": "claude-work",
            "model": "claude-opus-5-5",
            "modelSource": "requested_tier",
            "modelTierResolution": {
                "effectiveTier": 2,
                "effortSource": "requested_tier",
            },
            "effort": "high",
            "modelTier": 2,
            "agentProfile": {"profileId": "omnigent-claude", "version": 3},
            "agentProfileSnapshot": dict(_SNAPSHOT),
            "omnigent": {
                "executionTargetRef": "omnigent-claude@1",
                "launchPolicyRef": "claude-on-demand@1",
                "agent": {"label": "nightly"},
            },
            "omnigentExecutionPlan": dict(_PLAN),
            "omnigentStepExecutionPlans": {"review": {"omnigentExecutionPlan": {}}},
            "resolvedSkillsetRef": "art_skills",
            "workflow": {
                "instructions": "Review open pull requests.",
                "runtime": {
                    "mode": "omnigent",
                    "modelTier": 2,
                    "profileId": "claude-work",
                    "providerProfile": "claude-work",
                    "providerProfileRef": "claude-work",
                    "profileSelector": {"providerId": "anthropic"},
                    "executionConfiguration": {"profileId": "omnigent-claude"},
                    "resolvedModel": "claude-opus-5-5",
                    "resolvedEffort": "high",
                    "modelTierResolution": {"effectiveTier": 2},
                    "tierPreview": {"profileId": "claude-work", "profileVersion": 4},
                    "parameters": {"temperature": 0},
                },
                "steps": [
                    {
                        "id": "review",
                        "instructions": "Review.",
                        "runtime": {
                            "mode": "omnigent",
                            "effort": "max",
                            "resolvedModel": "claude-opus-5-5",
                            "resolvedEffort": "max",
                            "modelSource": "requested_tier",
                            "modelTierResolution": {"effectiveTier": 2},
                            "inheritedProfileId": "claude-work",
                        },
                    },
                    {
                        "id": "summarize",
                        "instructions": "Summarize.",
                        "runtime": {
                            "mode": "codex_cli",
                            "profileId": "codex-work",
                            "providerProfileRef": "codex-work",
                            "model": "gpt-5",
                            "requestedModel": "gpt-5",
                            "modelSource": "task_override",
                        },
                    },
                    {"id": "notify", "instructions": "Notify."},
                ],
            },
        },
    }


def test_a_custom_model_replaces_the_tier_and_its_compiled_results() -> None:
    saved = _admitted_target()
    untouched = copy.deepcopy(saved)

    target = replace_schedule_agent_selection(
        saved, {"model": "claude-sonnet-5-5", "effort": None}
    )

    assert saved == untouched
    parameters = target["initialParameters"]
    runtime = parameters["workflow"]["runtime"]
    assert runtime["model"] == "claude-sonnet-5-5"
    assert runtime["effort"] is None
    assert "modelTier" not in runtime
    assert runtime["parameters"] == {"temperature": 0}
    for resolved in (
        "resolvedModel",
        "resolvedEffort",
        "modelTierResolution",
        "tierPreview",
    ):
        assert resolved not in runtime
    # The previous resolution and plan would otherwise still decide the launch.
    for stale in (
        "model",
        "effort",
        "modelTier",
        "modelSource",
        "modelTierResolution",
        "omnigentExecutionPlan",
        "omnigentStepExecutionPlans",
        "resolvedSkillsetRef",
    ):
        assert stale not in parameters
    assert "omnigentAuthorityArtifactRefs" not in target
    assert "runtimeProviderTarget" not in target
    # The same account keeps its Backend, launch policy and configuration.
    assert runtime["mode"] == "omnigent"
    assert runtime["profileId"] == "claude-work"
    assert parameters["targetRuntime"] == "omnigent"
    assert parameters["agentProfileSnapshot"] == _SNAPSHOT
    assert target["agentProfileSnapshot"] == _SNAPSHOT
    assert parameters["omnigent"]["launchPolicyRef"] == "claude-on-demand@1"
    assert parameters["repository"] == "MoonLadderStudios/MoonMind"


def test_a_tier_replaces_a_custom_pair() -> None:
    saved = _admitted_target()
    parameters = saved["initialParameters"]
    parameters.update(
        model="claude-sonnet-5-5", modelSource="task_override", effort="low"
    )
    parameters.pop("modelTier")
    parameters.pop("modelTierResolution")
    runtime = parameters["workflow"]["runtime"]
    runtime.pop("modelTier")
    runtime.update(model="claude-sonnet-5-5", effort="low")

    target = replace_schedule_agent_selection(saved, {"modelTier": 1})

    runtime = target["initialParameters"]["workflow"]["runtime"]
    assert runtime["modelTier"] == 1
    assert "model" not in runtime and "effort" not in runtime
    assert "model" not in target["initialParameters"]
    assert "effort" not in target["initialParameters"]


def test_another_profile_gives_up_the_previous_accounts_authority() -> None:
    target = replace_schedule_agent_selection(
        _admitted_target(), {"profileId": "codex-work"}
    )

    parameters = target["initialParameters"]
    runtime = parameters["workflow"]["runtime"]
    assert runtime["profileId"] == "codex-work"
    assert saved_provider_profile_ref(target) == "codex-work"
    for stale in (
        "providerProfile",
        "providerProfileRef",
        "profileSelector",
        "executionConfiguration",
        "mode",
    ):
        assert stale not in runtime
    for stale in (
        "profileId",
        "targetRuntime",
        "runtimeCutover",
        "agentProfile",
        "agentProfileSnapshot",
    ):
        assert stale not in parameters
    assert "agentProfile" not in target and "agentProfileSnapshot" not in target
    # Only the authored part of the Omnigent block survives the account change.
    assert parameters["omnigent"] == {"agent": {"label": "nightly"}}
    # The saved tier request follows to the new profile; its resolution does not.
    assert runtime["modelTier"] == 2
    assert parameters["modelTier"] == 2
    assert "model" not in parameters and "effort" not in parameters


def test_reselecting_the_saved_profile_changes_nothing_account_bound() -> None:
    target = replace_schedule_agent_selection(
        _admitted_target(), {"profileId": "claude-work", "modelTier": 3}
    )

    parameters = target["initialParameters"]
    assert parameters["agentProfileSnapshot"] == _SNAPSHOT
    assert parameters["workflow"]["runtime"]["mode"] == "omnigent"
    assert parameters["workflow"]["runtime"]["modelTier"] == 3


def test_a_resolved_default_is_not_kept_as_an_authored_model() -> None:
    saved = _admitted_target()
    parameters = saved["initialParameters"]
    parameters.update(modelSource="profile_default_tier", requestedModel=None)
    parameters.pop("modelTier")
    parameters["workflow"]["runtime"].pop("modelTier")

    target = replace_schedule_agent_selection(saved, {"profileId": "codex-work"})

    assert "model" not in target["initialParameters"]
    assert "requestedModel" not in target["initialParameters"]
    assert "effort" not in target["initialParameters"]


def test_an_authored_flat_model_survives_a_profile_change() -> None:
    saved = _admitted_target()
    parameters = saved["initialParameters"]
    parameters.update(model="pinned-model", modelSource="task_override", effort="low")
    parameters.pop("modelTier")
    parameters.pop("modelTierResolution")
    parameters["workflow"]["runtime"].pop("modelTier")

    target = replace_schedule_agent_selection(saved, {"profileId": "codex-work"})

    # Admission resolves only the nested selection, so the kept pair moves there.
    parameters = target["initialParameters"]
    runtime = parameters["workflow"]["runtime"]
    assert (runtime["model"], runtime["effort"]) == ("pinned-model", "low")
    assert "model" not in parameters and "effort" not in parameters


def test_an_authored_flat_tier_survives_a_profile_change() -> None:
    saved = _admitted_target()
    saved["initialParameters"]["workflow"]["runtime"].pop("modelTier")

    target = replace_schedule_agent_selection(saved, {"profileId": "codex-work"})

    parameters = target["initialParameters"]
    assert parameters["workflow"]["runtime"]["modelTier"] == 2
    # The pair Tier 2 resolved to on the previous profile is not authored.
    assert "model" not in parameters["workflow"]["runtime"]
    assert "modelTier" not in parameters


def test_another_profile_gives_up_a_backend_named_on_the_target_envelope() -> None:
    saved = _admitted_target()
    saved.update(targetRuntime="omnigent", target_runtime="omnigent")

    target = replace_schedule_agent_selection(saved, {"profileId": "codex-work"})

    assert "targetRuntime" not in target
    assert "target_runtime" not in target


def test_a_default_backend_is_resolved_again() -> None:
    saved = _admitted_target()
    saved["initialParameters"]["runtimeCutover"]["selectionSource"] = "default"

    target = replace_schedule_agent_selection(saved, {"modelTier": 1})

    parameters = target["initialParameters"]
    assert "targetRuntime" not in parameters
    assert "runtimeCutover" not in parameters
    assert "mode" not in parameters["workflow"]["runtime"]


def test_steps_keep_their_own_selection_and_follow_the_workflow_otherwise() -> None:
    target = replace_schedule_agent_selection(
        _admitted_target(), {"profileId": "codex-work"}
    )

    review, summarize, notify = target["initialParameters"]["workflow"]["steps"]
    # A step that follows the workflow keeps only what it authored.
    assert review["runtime"] == {"effort": "max"}
    assert summarize["runtime"] == {
        "mode": "codex_cli",
        "profileId": "codex-work",
        "providerProfileRef": "codex-work",
        "model": "gpt-5",
    }
    assert notify == {"id": "notify", "instructions": "Notify."}


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ({}, "profileId or a model selection"),
        ({"model": "only-a-model"}, "both model and effort"),
        ({"modelTier": 2, "model": "x", "effort": None}, "cannot be combined"),
    ],
)
def test_an_ambiguous_selection_is_refused(selection: dict, message: str) -> None:
    with pytest.raises(ScheduleAgentSelectionError, match=message):
        replace_schedule_agent_selection(_admitted_target(), selection)


def test_a_target_without_a_workflow_is_refused() -> None:
    with pytest.raises(ScheduleAgentSelectionError, match="no workflow"):
        replace_schedule_agent_selection(
            {"workflowType": "MoonMind.UserWorkflow", "initialParameters": {}},
            {"modelTier": 1},
        )
