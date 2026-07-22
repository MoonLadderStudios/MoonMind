from pathlib import Path


CONTRACT = Path(__file__).resolve().parents[3] / "docs/Omnigent/CodexCreateToHostContract.md"


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
    assert text.count('"schemaVersion": "omnigent-create-host/v1"') >= 5


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
