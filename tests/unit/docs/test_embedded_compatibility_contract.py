from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUTH_CONTRACT = REPO_ROOT / "docs" / "Omnigent" / "EmbeddedHostAuthCompatibility.md"
BRIDGE_CONTRACT = REPO_ROOT / "docs" / "Omnigent" / "OmnigentBridge.md"
ROLLBACK_CONTRACT = (
    REPO_ROOT / "docs" / "Omnigent" / "CombinedStackValidationAndRollback.md"
)


def test_embedded_contract_names_the_pinned_protocol_and_routes() -> None:
    text = AUTH_CONTRACT.read_text(encoding="utf-8")

    for required in (
        "`omnigent.runner_tunnel.983c93c6`",
        "frame protocol major `1`",
        "`POST /v1/hosts/register`",
        "`WS /v1/hosts/{host_id}/tunnel`",
        "`WS /v1/runners/{runner_id}/tunnel`",
        "`POST /v1/hosts/{host_id}/heartbeat`",
        "`POST /v1/hosts/{host_id}/sessions/{session_id}/events`",
        "`omnigent_bridge_capability_unavailable`",
    ):
        assert required in text


def test_embedded_contract_keeps_promotion_evidence_gated() -> None:
    auth_text = AUTH_CONTRACT.read_text(encoding="utf-8")
    bridge_text = BRIDGE_CONTRACT.read_text(encoding="utf-8")
    rollback_text = ROLLBACK_CONTRACT.read_text(encoding="utf-8")

    assert "Proxy mode remains the supported production topology" in auth_text
    assert "There are no unresolved embedded compatibility contract questions" in bridge_text
    assert "previous passing compatibility identity" in rollback_text
    assert "must not rewrite the bridge mode recorded by an existing session" in rollback_text
