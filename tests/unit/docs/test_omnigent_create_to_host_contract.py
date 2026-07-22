import json
import re
from pathlib import Path

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


CONTRACT = Path(__file__).resolve().parents[3] / "docs/Omnigent/CodexCreateToHostContract.md"
ADAPTER = Path(__file__).resolve().parents[3] / "docs/Omnigent/OmnigentAdapter.md"


def _json_example(text: str, heading: str) -> dict[str, object]:
    match = re.search(rf"### {re.escape(heading)}\n\n```json\n(.*?)\n```", text, re.DOTALL)
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
    payload = _json_example(text, "4.3 AgentExecutionRequest")

    request = AgentExecutionRequest.model_validate(payload)

    assert request.agent_id == "omnigent"
    assert request.correlation_id == "workflow:run_01:step_01"
    assert request.workspace_spec["workspaceLocator"]["workspaceId"] == "ws_01"


def test_launch_snapshot_and_terminal_authority_are_pinned() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    snapshot = _json_example(text, "4.4 Effective launch snapshot")
    detail = _json_example(text, "4.5 Workflow Detail projection")

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
