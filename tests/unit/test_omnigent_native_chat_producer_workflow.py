from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/omnigent-native-chat-producer.yml")


def _steps() -> list[dict]:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return payload["jobs"]["observe"]["steps"]


def test_producer_runs_repository_owned_browser_controller() -> None:
    steps = _steps()
    observe = next(step for step in steps if step.get("name") == "Observe the native Chat product journey")
    assert "node tools/run_omnigent_native_chat_journey.mjs" in observe["run"]
    assert "MOONMIND_OMNIGENT_NATIVE_CHAT_WORKFLOW_ID" in observe["env"]
    assert "MOONMIND_OMNIGENT_UPSTREAM_ORIGIN" in observe["env"]


def test_protected_observation_is_durable_and_cannot_publish_on_failure() -> None:
    steps = _steps()
    upload = next(step for step in steps if step.get("name") == "Upload independently resolvable observation")
    assert "if" not in upload
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 90
