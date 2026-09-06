"""Slice 0 reconciliation guards for MoonLadderStudios/MoonMind#4004.

Documentation-only contract: the repository-access design stays Proposed,
the temporary plan records the rechecked inventory, all 42 stable claims
resolve to tracked work, and no new connection/target domain or wire change
is introduced by this issue. No runtime qualification is claimed here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN_DOC = REPO_ROOT / "docs" / "RepositoryAccessAndWorkspaceDesign.md"
PLAN_DOC = REPO_ROOT / "docs" / "tmp" / "RepositoryAccessAndWorkspaceDecouplingPlan.md"
CONTRACT_MODULE = (
    REPO_ROOT / "moonmind" / "workflows" / "executions" / "repository_contract.py"
)
PUBLISHING_DOC = REPO_ROOT / "docs" / "Workflows" / "WorkflowPublishing.md"

EXPECTED_CLAIMS = (
    "DOC-REQ-001",
    "CONTRACT-001",
    "INV-001",
    "DOC-REQ-002",
    "CONTRACT-002",
    "CONTRACT-003",
    "CONTRACT-004",
    "INV-002",
    "CONTRACT-005",
    "CONTRACT-006",
    "CONTRACT-007",
    "QUALITY-001",
    "CONTRACT-008",
    "CONTRACT-009",
    "INV-003",
    "QUALITY-002",
    "CONTRACT-010",
    "INV-004",
    "QUALITY-003",
    "INV-005",
    "CONTRACT-011",
    "INV-006",
    "CONTRACT-012",
    "QUALITY-004",
    "INV-007",
    "QUALITY-005",
    "CONTRACT-013",
    "INV-008",
    "QUALITY-006",
    "CONTRACT-014",
    "QUALITY-007",
    "DOC-REQ-003",
    "INV-009",
    "QUALITY-008",
    "DOC-REQ-004",
    "DOC-REQ-005",
    "TEST-001",
    "TEST-002",
    "TEST-003",
    "TEST-004",
    "NON-GOAL-001",
    "QUALITY-009",
)

RELATED_DOCS = (
    "docs/MoonMindArchitecture.md",
    "docs/Workflows/WorkflowArchitecture.md",
    "docs/Workflows/LoreVcsIntegrationDesign.md",
    "docs/Workflows/WorkflowPublishing.md",
    "docs/Workflows/WorkspaceLocators.md",
    "docs/Security/SecretsSystem.md",
    "docs/Security/ProviderProfiles.md",
    "docs/Omnigent/OmnigentHarnessPlatformDesign.md",
    "docs/Omnigent/OpenCodeHost.md",
    "docs/Workflows/MoonSpecDocumentModel.md",
)

INVENTORY_PATHS = (
    "moonmind/auth/github_credentials.py",
    "moonmind/workflows/executions/repository_contract.py",
    "moonmind/workflows/temporal/runtime/managed_api_key_resolve.py",
    "moonmind/workflows/temporal/runtime/launcher.py",
    "moonmind/workflows/temporal/runtime/managed_session_controller.py",
    "moonmind/workflows/temporal/runtime/checkpoint_restore.py",
    "moonmind/workflows/adapters/github_service.py",
    "moonmind/workflows/adapters/jules_client.py",
    "moonmind/workflows/temporal/story_output_tools.py",
    "moonmind/publish/service.py",
    "moonmind/omnigent/host_services/workspace.py",
    "moonmind/omnigent/host_services/github_credentials.py",
    "moonmind/omnigent/workspace_publication.py",
    "moonmind/omnigent/profile_bound_execution.py",
    "moonmind/agents/codex_worker/worker.py",
    "moonmind/omnigent/workspace_intent.py",
    "api_service/main.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_design_stays_proposed_with_reconciliation_decision() -> None:
    text = _read(DESIGN_DOC)
    assert "**Status:** Proposed" in text
    assert "MoonLadderStudios/MoonMind#4004" in text
    assert "tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md" in text
    # The design's illustrative shapes must not be mistaken for wire schemas.
    assert "not complete wire-schema definitions" in text
    # Reconciliation preserves current gates and enables no runtime path.
    assert "remains Proposed until its owning views" in text
    assert "validate terminal evidence before releasing workspace/cleanup authority" in text
    assert "enables no conflicting runtime path" in text
    assert "claims no live support" in text


def test_design_contains_all_42_stable_claims() -> None:
    text = _read(DESIGN_DOC)
    assert len(EXPECTED_CLAIMS) == 42
    for claim in EXPECTED_CLAIMS:
        assert claim in text, claim


def test_related_doc_links_resolve() -> None:
    for relative in RELATED_DOCS:
        assert (REPO_ROOT / relative).exists(), relative


def test_plan_records_rechecked_baseline_and_review_decision() -> None:
    text = _read(PLAN_DOC)
    assert "MoonLadderStudios/MoonMind#4004" in text
    assert "63bce9852ffa33e33cb0b416bc24a654b1b6f92b" in text
    assert "5da35a2ba7ad80a8778ce766445184b134631fcb" in text
    assert "source inspection only" in text or "inspection-only" in text
    assert "not deployment or integration-test evidence" in text or "No deployment" in text or "no live support" in text.lower()
    # Review decision: proposed target only, current gates authoritative.
    assert "Proposed target only" in text
    assert "enables no runtime path" in text


def test_plan_inventory_names_exact_consumer_paths() -> None:
    text = _read(PLAN_DOC)
    for relative in INVENTORY_PATHS:
        assert relative in text, relative
    # Key resolver signals are distinguished, not conflated.
    assert "resolve_github_credential" in text
    assert "resolve_github_token_for_launch" in text
    assert "resolve_default_git_credential" in text
    # Corrected lease location is recorded.
    assert "moonmind/omnigent/provider_leases.py" in text


def test_plan_resolves_all_42_claims_to_tracked_work() -> None:
    text = _read(PLAN_DOC)
    for claim in EXPECTED_CLAIMS:
        assert claim in text, claim
    # Partitioning with sibling issues is explicit.
    for reference in ("#2615", "#1090", "#2619", "#3938", "#3940", "#4003"):
        assert reference in text, reference


def test_plan_support_matrix_claims_no_live_support() -> None:
    text = _read(PLAN_DOC)
    assert "inspection only, not live support" in text
    assert "opencode-zen-free" in text
    assert "none@1" in text
    assert "capture/restore/session-reattach" in text.lower() or "capture / restore" in text.lower()


def test_no_competing_connection_or_target_domain() -> None:
    text = _read(CONTRACT_MODULE)
    assert "SourceControlConnection" not in text
    assert "RepositoryTargetV2" not in text
    assert 'provider: git | lore' in text or '"git"' in text
    assert "DEFAULT_GIT_CONNECTION_REF" in text
    assert "repository-connection:git-default" in text
    assert "LEGACY_REPOSITORY_DECODER_VERSION" in text
    assert "decode_legacy_repository_history_v1" in text
    # Single connection domain is preserved.
    assert "class RepositoryConnection" in text


def test_publish_modes_and_provider_axes_are_preserved() -> None:
    contract = _read(CONTRACT_MODULE)
    assert 'Literal["git", "lore"]' in contract or 'Literal["git",' in contract
    publishing = _read(PUBLISHING_DOC)
    for mode in ("`none`", "`branch`", "`pr`", "`auto`"):
        assert mode in publishing, mode
    # Managed auto stays agent-owned; recovery branch is not success.
    assert "agent-owned" in publishing
    assert "recovery" in publishing.lower()


def test_plan_keeps_migration_steps_under_docs_tmp() -> None:
    text = _read(PLAN_DOC)
    assert "docs/tmp/" in text or "this `docs/tmp/` plan" in text
    # The canonical design must not be reframed as a migration checklist.
    design = _read(DESIGN_DOC)
    assert "- [ ]" not in design
    # Plan records that illustrative examples are not wire schemas.
    assert "not complete wire-schema" in text or "illustrative" in text
    # No fake credential values are introduced by the reconciliation record.
    assert re.search(r"ghp_[A-Za-z0-9]+", text) is None
    assert re.search(r"github_pat_[A-Za-z0-9]+", text) is None
