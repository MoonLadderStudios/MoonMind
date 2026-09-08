import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


CONTRACT = Path(__file__).resolve().parents[3] / "docs/Omnigent/CodexCreateToHostContract.md"
ADAPTER = Path(__file__).resolve().parents[3] / "docs/Omnigent/OmnigentAdapter.md"


def _json_example(text: str, section: str) -> dict[str, object]:
    # Section numbers identify wire examples; their explanatory titles and
    # spacing are free to change.
    match = re.search(
        rf"^###\s+{re.escape(section)}\s+[^\n]*\n\s*```json\s*\n(.*?)\n```",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_identity_and_versioned_wire_contract_are_pinned() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert "MoonLadderStudios/MoonMind#3449" in text
    assert "agentKind = external" in text
    assert "agentId   = omnigent" in text
    assert "harness   = codex-native" in text
    assert '"agentKind": "external"' in text
    assert '"agentId": "omnigent"' in text
    assert '"harnessOverride": "codex-native"' in text
    assert "There is deliberately no `session.hostId`" in text
    assert text.count('"schemaVersion": "omnigent-create-host/v1"') >= 4


def test_agent_execution_request_example_matches_canonical_model() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    payload = _json_example(text, "4.3")

    request = AgentExecutionRequest.model_validate(payload)

    assert request.agent_id == "omnigent"
    assert request.correlation_id == "workflow:run_01:step_01"
    assert request.workspace_spec["workspaceLocator"]["workspaceId"] == "ws_01"


def test_launch_snapshot_and_terminal_authority_are_pinned() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    snapshot = _json_example(text, "4.4")
    detail = _json_example(text, "4.5")

    assert snapshot["executionProfileRef"] == "provider-profile:codex-primary:v7"
    assert snapshot["credentialGeneration"] == 7
    terminal = detail["terminal"]
    assert terminal["primaryStatus"] == "completed"
    assert terminal["cleanup"] == {"status": "completed", "janitorRequired": False}
    assert terminal["profileLease"] == {"releaseStatus": "released"}
    assert "never replace or obscure `primaryStatus`" in text


def test_manual_host_id_is_rejected_by_both_canonical_contracts() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    adapter = ADAPTER.read_text(encoding="utf-8")

    assert "caller-authored `session.hostId`" in contract
    assert "caller-provided `session.hostId` is always rejected" in adapter


def test_explicit_selection_is_fail_closed_without_substitution() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    invariant = (
        "An explicit Omnigent selection never silently runs through direct Codex, "
        "another Provider Profile, another host mode, an arbitrary static host, or "
        "a broader network/mount policy."
    )
    assert invariant in text
    for code in (
        "OMNIGENT_RUNTIME_UNSUPPORTED",
        "OMNIGENT_PROFILE_UNAVAILABLE",
        "OMNIGENT_LAUNCH_POLICY_INVALID",
        "OMNIGENT_WORKSPACE_RESOLUTION_FAILED",
        "OMNIGENT_HOST_LAUNCH_FAILED",
        "OMNIGENT_HOST_REGISTRATION_TIMEOUT",
        "OMNIGENT_BRIDGE_AUTHORIZATION_FAILED",
        "OMNIGENT_FIRST_MESSAGE_AMBIGUOUS",
        "OMNIGENT_CLEANUP_FAILED",
        "OMNIGENT_EVIDENCE_PUBLICATION_FAILED",
    ):
        assert code in text


@pytest.mark.parametrize(
    "heading",
    [
        "### 4.3 AgentExecutionRequest",
        "### 4.3 Request sent to the selected agent\n",
        "###\t4.3   Request example   \n\n",
    ],
    ids=["original-title", "reworded-title", "changed-spacing"],
)
def test_example_heading_can_be_reworded_without_changing_request(heading: str) -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    rewritten, count = re.subn(
        r"^###[ \t]+4\.3[ \t]+[^\n]*$", heading, text, flags=re.MULTILINE
    )
    assert count == 1, "the request example must have one section-4.3 heading"
    payload = _json_example(rewritten, "4.3")
    assert payload == _json_example(text, "4.3")
    request = AgentExecutionRequest.model_validate(payload)
    assert request.agent_id == "omnigent"


@pytest.mark.parametrize("mutation", ["invalid-kind", "missing-agent", "invalid-json"])
def test_incorrect_executable_request_example_is_rejected(mutation: str) -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    payload = _json_example(text, "4.3")
    if mutation == "invalid-kind":
        payload["agentKind"] = "unsupported-kind"
    elif mutation == "missing-agent":
        del payload["agentId"]
    else:
        with pytest.raises(json.JSONDecodeError):
            _json_example("### 4.3 Any title\n\n```json\n{broken}\n```", "4.3")
        return
    with pytest.raises(ValidationError):
        AgentExecutionRequest.model_validate(payload)
