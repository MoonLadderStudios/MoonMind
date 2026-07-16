"""Regression coverage for GitHub issue MoonLadderStudios/MoonMind#3360."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_named_docs_share_omnigent_authority_contract() -> None:
    bridge = _read("docs/Omnigent/OmnigentBridge.md")
    oauth = _read("docs/Omnigent/OmnigentHostOAuth.md")
    settings = _read("docs/Security/SettingsSystem.md")
    chat = _read("docs/UI/WorkflowChatPanel.md")
    details = _read("docs/UI/WorkflowDetailsPage.md")

    assert "stock Omnigent server" in bridge
    assert "unchanged profile-bound Omnigent host" in bridge
    assert "Provider Profile lease" in bridge
    assert "## 17. Lifecycle and error vocabulary" in bridge
    assert "`profile_resolution`" in bridge
    assert "`cleanup` / `lease_release`" in bridge
    assert "stable code plus a short" in bridge
    assert "COMPOSE_PROFILES" in oauth
    assert "docker-compose.yaml" in oauth
    assert "executionProfileRef" in settings
    assert "four separate authentication boundaries" in settings
    assert "check bridge-session evidence" in chat
    assert "Legacy direct-Codex evidence" in chat
    assert "resolves evidence bridge-first" in details
    assert "fails before an Omnigent session or stream exists" in details


def test_settings_and_ui_docs_link_to_canonical_omnigent_owners() -> None:
    for path in (
        "docs/Security/SettingsSystem.md",
        "docs/UI/WorkflowChatPanel.md",
        "docs/UI/WorkflowDetailsPage.md",
    ):
        text = _read(path)
        assert "OmnigentBridge.md" in text
        assert "OmnigentHostOAuth.md" in text
