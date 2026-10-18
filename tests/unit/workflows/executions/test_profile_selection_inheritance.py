"""Account intent replaces inherited aliases without changing model selection."""

from types import SimpleNamespace

import pytest

from moonmind.runtime_intent import merge_runtime_selection


PROFILE_KEYS = (
    "providerProfileRef",
    "profileId",
    "providerProfile",
    "executionProfileRef",
    "execution_profile_ref",
)


@pytest.mark.parametrize("key", PROFILE_KEYS)
@pytest.mark.parametrize("child_key", PROFILE_KEYS)
def test_child_exact_account_replaces_every_parent_alias(key, child_key):
    parent = {key: "saved", "modelTier": 2, "parameters": {"temperature": 1}}
    child = {child_key: "chosen"}
    merged = merge_runtime_selection(parent, child)
    assert {k: merged[k] for k in PROFILE_KEYS if k in merged} == child
    assert merged["modelTier"] == 2
    assert merged["parameters"] == {"temperature": 1}
    assert parent[key] == "saved"


@pytest.mark.parametrize("selector", [{}, None, {"tagsAll": ["chosen"]}])
def test_only_nonempty_selector_replaces_parent_account(selector):
    parent = {**dict.fromkeys(PROFILE_KEYS, "saved"), "inheritedProfileId": "saved"}
    merged = merge_runtime_selection(parent, {"profileSelector": selector})
    assert (merged.get("profileId") is None) == bool(selector)
    assert (merged.get("inheritedProfileId") is None) == bool(selector)


@pytest.mark.parametrize("patch_enabled", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_dispatch_selector_inheritance_preserves_recorded_commands(
    monkeypatch, patch_enabled, explicit
):
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        lambda: SimpleNamespace(workflow_id="mm:selection", run_id="run-1"),
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_workflow_patch_enabled",
        staticmethod(
            lambda name: (
                patch_enabled
                if name == "run-step-profile-selection-inheritance-v1"
                else False
            )
        ),
    )
    runtime = {"mode": "codex_cli", "profileSelector": {"tagsAll": ["chosen"]}}
    if explicit:
        runtime["profileId"] = "recorded-exact"
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_inputs={"instructions": "Use selected account", "runtime": runtime},
        node_id="step",
        tool_name="codex_cli",
        workflow_parameters={"profileId": "saved"},
    )
    expected = "recorded-exact" if explicit else None if patch_enabled else "saved"
    assert request.execution_profile_ref == expected
    assert request.profile_selector.tags_all == ["chosen"]


@pytest.mark.parametrize(
    "account_intent",
    [{"profileSelector": {"tagsAll": ["chosen"]}}, {"providerProfileRef": "chosen"}],
)
def test_account_override_preserves_workflow_model_merge_outputs(account_intent):
    """Run consumes model fields/parameters; AgentRun supplies model-only children."""
    from moonmind.runtime_intent import model_selection_fields

    parent = {
        "profileId": "saved",
        "modelTier": 2,
        "parameters": {"temperature": 1, "max_tokens": 50},
    }
    child = {**account_intent, "parameters": {"max_tokens": 100}}
    merged = merge_runtime_selection(parent, child)
    legacy_merged = {
        **parent,
        **child,
        "parameters": {**parent["parameters"], **child["parameters"]},
    }
    assert model_selection_fields(merged) == model_selection_fields(legacy_merged)
    assert merged["parameters"] == legacy_merged["parameters"]
    flat_selection = model_selection_fields({**account_intent, "effort": "high"})
    assert merge_runtime_selection(parent, flat_selection) == {
        **parent,
        **flat_selection,
    }


def test_child_selector_keeps_its_own_exact_pin():
    child = {"providerProfileRef": "chosen", "profileSelector": {"tagsAll": ["chosen"]}}
    assert merge_runtime_selection({"profileId": "saved"}, child) == child
