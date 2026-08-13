from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/omnigent-native-chat-acceptance.yml")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_native_chat_acceptance_workflow_owns_both_evidence_lanes() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True, {}))
    assert not triggers.get("workflow_dispatch", {}).get("inputs", {}).get(
        "evidence_artifact"
    )
    jobs = workflow["jobs"]
    assert {"candidate", "deterministic", "protected-live", "publish"} <= set(jobs)

    deterministic = "\n".join(
        str(step.get("run", "")) for step in jobs["deterministic"]["steps"]
    )
    assert "test_native_chat_acceptance_journey.py" in deterministic
    assert "test_omnigent_browser_product_path_journey.py" in deterministic
    assert "test_bridge_proxy_fake_server.py" in deterministic
    assert "test_executions_linked_continuation.py" in deterministic
    assert "workflowNativeChat.browser.test.tsx" in deterministic
    assert "record_native_chat_deterministic_observations.py" in deterministic
    assert "build_native_chat_acceptance_lane.py" in deterministic
    browser_step = next(
        step
        for step in jobs["deterministic"]["steps"]
        if step["name"] == "Run real-browser native application journey"
    )
    assert browser_step["env"]["MOONMIND_BROWSER_ENGINES"] == "chromium"

    live = "\n".join(
        str(step.get("run", "")) for step in jobs["protected-live"]["steps"]
    )
    assert "run_omnigent_live_conformance.py" in live
    assert "--mode native-chat" in live
    assert jobs["protected-live"]["environment"] == "omnigent-provider-verification"


def test_publish_merges_only_successful_repo_owned_lanes() -> None:
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    assert set(publish["needs"]) == {"candidate", "deterministic", "protected-live"}
    names = [step["name"] for step in publish["steps"]]
    merge = next(
        step
        for step in publish["steps"]
        if step["name"] == "Merge lanes and build native chat acceptance report"
    )
    assert "merge_native_chat_acceptance_lanes.py" in merge["run"]
    assert '--expected-commit "${{ github.sha }}"' in merge["run"]
    assert names.index("Upload passing report") < names.index(
        "Link passing report from issue 3642"
    )
