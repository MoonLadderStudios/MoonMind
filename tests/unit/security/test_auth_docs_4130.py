"""Docs reconciliation regression tests for #4130 (parent #4116).

Guards the K5 documentation-reconciliation core: MCP/artifact/stack docs must
describe the post-removal selector-based auth model (not the pre-removal
two-state model), README/.env-template must separate the operator journeys,
the canonical contract must own target behavior with an honest
supported-vs-blocked matrix and real tooling linkage, and the temporary removal
plan must not be deleted prematurely.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

MCP_DOC = REPO_ROOT / "docs/ExternalAgents/ModelContextProtocol.md"
ARTIFACT_DOC = REPO_ROOT / "docs/Temporal/WorkflowArtifactSystemDesign.md"
DOCKER_BACKEND_DOC = REPO_ROOT / "docs/ManagedAgents/DockerBackendService.md"
COMBINED_STACK_DOC = REPO_ROOT / "docs/Omnigent/CombinedStackValidationAndRollback.md"
CANONICAL_DOC = REPO_ROOT / "docs/Security/AuthenticationContracts.md"
README = REPO_ROOT / "README.md"
ENV_TEMPLATE = REPO_ROOT / ".env-template"
REMOVAL_PLAN = REPO_ROOT / "docs/tmp/KeycloakRemovalPlan.md"
PROPOSAL_DOC = REPO_ROOT / "docs/Proposals/OmnigentAuthenticationDesign.md"
MIGRATE_CLI = REPO_ROOT / "api_service/scripts/migrate_identities.py"
MAIN_CLI = REPO_ROOT / "moonmind/cli.py"
API_MAIN = REPO_ROOT / "api_service/main.py"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mcp_doc_uses_selector_model_not_two_state():
    text = _read(MCP_DOC)
    assert "When `AUTH_PROVIDER` is enabled" not in text
    assert "When authentication is disabled" not in text
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert mode in text
    assert "get_current_user()" in text
    assert "auth_conflict" in text
    assert "same-origin" in text
    assert "AuthenticationContracts" in text
    assert "worker_token_deprecated" in text


def test_artifact_doc_binds_all_modes_with_machine_table():
    text = _read(ARTIFACT_DOC)
    for mode in ("`accounts`", "`oidc`", "`header`", "`disabled`"):
        assert mode in text
    assert "MOONMIND_TRUSTED_INGRESS" in text
    assert "CSRF" in text
    assert "auth_required" in text
    assert "auth_invalid" in text
    assert "worker_token_deprecated" in text
    assert "same-origin" in text
    assert "AuthenticationContracts" in text


def test_docker_backend_doc_distinguishes_user_and_machine_credentials():
    text = _read(DOCKER_BACKEND_DOC)
    assert "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN" in text
    assert "AuthenticationContracts" in text
    assert "never become browser credentials" in text or "never accepted as a MoonMind browser" in text


def test_combined_stack_doc_separates_app_login_from_host_credentials():
    text = _read(COMBINED_STACK_DOC)
    assert "Authentication Contracts" in text or "AuthenticationContracts" in text
    assert "OMNIGENT_AUTH_*" in text
    assert "keycloak" in text.lower()


def test_readme_separates_operator_journeys():
    text = _read(README)
    assert "protected operator setup" in text
    assert "MOONMIND_AUTH_MIGRATION_DECISION" in text
    assert "Retired selectors" in text
    assert "source credentials" in text
    assert "model eligibility" in text or "Model" in text
    assert "Authentication Contracts" in text


def test_env_template_points_at_canonical_contract():
    text = _read(ENV_TEMPLATE)
    assert "AUTH_PROVIDER" in text
    assert "docs/Security/AuthenticationContracts.md" in text
    assert "keycloak/default/google" in text


def test_canonical_doc_owns_matrix_tooling_and_evidence():
    text = _read(CANONICAL_DOC)
    assert "Status:** Draft" in text or "Status: Draft" in text
    assert "single owner" in text
    assert "keycloak_cutover_rehearsal" in text
    assert "#4128" in text
    assert "never whole shared" in text.lower() or "not an auth rollback" in text
    assert "never a supported path" in text or "Rejected" in text
    assert "Status: Proposed" in text or "Status:` Proposed" in text or "Proposed" in text
    # Must not claim shipped end-to-end journeys ahead of qualification.
    assert "not verified operator procedure" in text or "contract text" in text


def test_removal_plan_retained_until_execution_complete():
    assert REMOVAL_PLAN.exists()
    text = _read(REMOVAL_PLAN)
    assert "Status: Proposed" in text


def test_proposal_keycloak_references_are_classified():
    # docs/Proposals/OmnigentAuthenticationDesign.md is active proposal
    # guidance, so its pre-removal Keycloak references must carry an
    # explicit retirement classification, not read as live setup claims.
    text = _read(PROPOSAL_DOC)
    assert "Candidate design only" in text
    assert "optional Keycloak profile" not in text
    assert "#4129" in text
    assert "AuthenticationContracts" in text


def test_examples_contain_no_active_keycloak_setup():
    # Active examples must not claim retired Keycloak paths still work.
    hits = []
    for path in sorted(EXAMPLES_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if "keycloak" in content.lower():
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_cli_help_and_migration_example_are_classified():
    # moonmind/cli.py (user-facing CLI help source) must not advertise
    # retired Keycloak paths or realm URLs. A classified rejection mention
    # (retired/retired-selector language pointing at the canonical
    # contract, as in .env-template) is allowed — not a forced
    # zero-string policy — but unclassified live claims are not.
    cli = _read(MAIN_CLI)
    assert "realms/" not in cli
    if "keycloak" in cli.lower():
        assert "rejected" in cli.lower() or "retired" in cli.lower()
        assert "AuthenticationContracts" in cli
    # The K3 migration command keeps a historical source-provider key; it
    # must be classified as migration input, not an active selector.
    migrate = _read(MIGRATE_CLI)
    assert "not an active" in migrate
    assert "AUTH_PROVIDER" in migrate


def test_cli_help_documents_auth_provider_modes():
    # Positive help-vs-implementation match (#4130 req-5 residual): the
    # user-facing CLI help source must name the AUTH_PROVIDER modes and the
    # machine-vs-browser credential distinction, not merely avoid stale
    # Keycloak claims.
    cli = _read(MAIN_CLI)
    assert "AUTH_PROVIDER" in cli
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert mode in cli
    assert "AuthenticationContracts" in cli
    assert "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN" in cli
    assert "worker_token_deprecated" in cli


def test_openapi_help_documents_auth_provider_modes_and_errors():
    # Positive help-vs-implementation match (#4130 req-5 residual): the
    # generated OpenAPI help source (FastAPI info description exported by
    # tools/export_openapi.py) must name the AUTH_PROVIDER modes, the
    # documented error contract, and the canonical contract owner — with
    # the Draft/#4128 qualification disclaimer, never a shipped claim.
    text = _read(API_MAIN)
    assert "AUTH_PROVIDER" in text
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert mode in text
    assert "auth_required" in text
    assert "auth_invalid" in text
    assert "auth_conflict" in text
    assert "worker_token_deprecated" in text
    assert "AuthenticationContracts" in text
    assert "keycloak/default/google" in text
    assert "#4128" in text


def test_api_composition_mounts_no_legacy_login_routes():
    # No /api/v1/auth/* application-login or /auth/jwt/* issuance route may
    # be mounted after #4129 removed the bundled Keycloak integration.
    text = _read(API_MAIN)
    assert 'prefix="/api/v1/auth"' not in text
    assert "auth/jwt" not in text
