"""Contract tests for the canonical authentication entrypoint (MoonMind#4130).

Pins the shipped-behavior claims in docs/Security/AuthenticationSystem.md so
future implementation PRs (#4120-#4129) update the doc and this test together.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "Security" / "AuthenticationSystem.md"
SETTINGS = REPO_ROOT / "moonmind" / "config" / "settings.py"
ENV_TEMPLATE = REPO_ROOT / ".env-template"
COMPOSE = REPO_ROOT / "docker-compose.yaml"
CHAT_PANEL_DOC = REPO_ROOT / "docs" / "UI" / "WorkflowChatPanel.md"
MCP_DOC = REPO_ROOT / "docs" / "ExternalAgents" / "ModelContextProtocol.md"
ARTIFACT_DOC = (
    REPO_ROOT / "docs" / "Temporal" / "WorkflowArtifactSystemDesign.md"
)
COMBINED_DOC = (
    REPO_ROOT
    / "docs"
    / "Omnigent"
    / "CombinedStackValidationAndRollback.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_auth_doc_exists_with_owner_and_metadata() -> None:
    assert DOC.exists()
    text = _read(DOC)
    assert "**Document Class:** Canonical declarative" in text
    assert "**Owners:** MoonMind Engineering" in text
    assert "**Authority:**" in text
    assert "KeycloakRemovalPlan.md" in text


def test_canonical_doc_states_shipped_modes_truthfully() -> None:
    text = _read(DOC)
    # Shipped default and legacy opt-in.
    assert "`disabled` (default)" in text
    assert "`keycloak`" in text
    # Unsupported internal literals are called out, not advertised.
    assert "`default`" in text
    assert "`google`" in text
    assert "not supported selectors" in text
    # Proposed modes are explicitly marked unshipped.
    assert "not shipped" in text
    assert "Do not configure them" in text


def test_canonical_doc_does_not_advertise_new_default_as_shipped() -> None:
    text = _read(DOC)
    assert "are **not shipped**" in text
    for forbidden in (
        "accounts` is the default",
        "accounts` (default)",
        "`header` mode is available",
        "configure `AUTH_PROVIDER=accounts`",
    ):
        assert forbidden not in text


def test_canonical_doc_distinguishes_credential_boundaries() -> None:
    text = _read(DOC)
    for required in (
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN",
        "OMNIGENT_AUTH_*",
        "OMNIGENT_HOST_AUTH_*",
        "Provider Profile",
        "JWT_SECRET_KEY",
        "same-origin",
    ):
        assert required in text


def test_canonical_doc_shipped_claims_match_settings_help() -> None:
    settings_text = _read(SETTINGS)
    assert "'disabled' (default local single-user)" in settings_text
    assert "'keycloak' (legacy opt-in, pending removal)" in settings_text
    assert "'accounts'/'oidc'/'header' are not shipped" in settings_text


def test_reconciled_docs_point_at_canonical_owner() -> None:
    assert "AuthenticationSystem.md" in _read(MCP_DOC)
    assert "AuthenticationSystem.md" in _read(ARTIFACT_DOC)
    assert "AuthenticationSystem.md" in _read(COMBINED_DOC)
    combined = _read(COMBINED_DOC)
    assert "not for MoonMind's control-plane `AUTH_PROVIDER`" in combined


def test_combined_stack_doc_separates_runtime_from_control_plane_auth() -> None:
    combined = _read(COMBINED_DOC)
    assert "OMNIGENT_AUTH_*" in combined or "Omnigent runtime server" in combined
    assert "control-plane" in combined


def test_canonical_doc_states_shipped_transport_and_error_behavior() -> None:
    text = _read(DOC)
    assert "Authorization: Bearer" in text
    assert "3600" in text
    assert "no cookie/CSRF" in text
    assert "Invalid DEFAULT_USER_ID" in text
    assert "Default user not found" in text
    assert "get_current_user_optional" in text


def test_env_template_documents_control_plane_selector_truthfully() -> None:
    text = _read(ENV_TEMPLATE)
    assert "AUTH_PROVIDER" in text
    assert "docs/Security/AuthenticationSystem.md" in text
    assert "`disabled`" in text
    assert "`keycloak`" in text
    assert "not shipped" in text
    assert "OMNIGENT_AUTH_*" in text


def test_compose_selector_comments_match_shipped_modes() -> None:
    text = _read(COMPOSE)
    assert "local, google, or disabled" not in text
    assert "or keycloak (legacy opt-in, pending removal)" in text


def test_workflow_chat_panel_points_at_canonical_auth_owner() -> None:
    assert "AuthenticationSystem.md" in _read(CHAT_PANEL_DOC)
