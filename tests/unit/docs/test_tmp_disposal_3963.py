"""MoonLadderStudios/MoonMind#3963 disposal guardrails for ``docs/tmp/``.

Locks the disposal executed under #3963 ("Dispose of completed temporary
plans after preserving active dependencies and durable evidence"):

* the 29 completed/run-local files removed by this issue stay removed;
* the 8 retained files keep an owning issue or delete/archive trigger;
* no live source, tool, test-helper, or canonical-doc consumer references a
  removed input (frozen review evidence ``docs/DocsReview.md`` and the active
  revalidation record are the only allowed historical mentions);
* the stale-plan checkers stay advisory-only and keep ``docs/tmp/``
  out of the canonical scope.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_tool(name: str):
    path = REPO_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ARCH = _load_tool("check_documentation_architecture")
LINKS = _load_tool("check_documentation_links")

# Files deleted under #3963: obsolete narrative whose durable content (if any)
# already lives in the canonical owner, tests, or git history. Dispositions
# are recorded in the #3963 commit message; git history is the archive.
DELETED_BASENAMES = (
    # Self-superseded / one-time evidence (durable guardrails now in tests
    # and the standard; conformance test skips when these are absent).
    "DocumentationArchitectureMigrationPlan.md",
    "MoonSpecDocsArchitectureConformanceReview.md",
    "MoonSpecDocsFirstAlignmentPlan.md",
    # Run-local submission memos (workflow/run identity belongs in
    # Temporal/artifacts, not git docs).
    "JiraOrchestrateMM821Submission.md",
    "JiraOrchestrateMM822Submission.md",
    "JiraOrchestrateMM824Submission.md",
    "JiraOrchestrateMM829Submission.md",
    "JiraOrchestrateMM832Submission.md",
    # Run-local intake/submission handoff JSON (future handoffs belong in
    # workflow artifacts, not git).
    "jira-orchestrate-mm-1103-handoff.json",
    "jira-orchestrate-mm-1113-handoff.json",
    "jira-orchestrate-mm-1114-handoff.json",
    "jira-orchestrate-mm-1115-handoff.json",
    "jira-orchestrate-mm-1116-handoff.json",
    "jira-orchestrate-mm-1117-handoff.json",
    "jira-orchestrate-mm-1118-handoff.json",
    "jira-orchestrate-mm-1121-handoff.json",
    "jira-orchestrate-mm-820-handoff.json",
    "jira-orchestrate-mm-829-handoff.json",
    "jira-orchestrate-mm-832-handoff.json",
    # Completed plans whose behavior is owned by canonical docs/tests:
    # MM-1003 (workflows-workspace.test.tsx), MM-1101 (ccee0a3ae on main),
    # ManagedRuntimeCleanup (enabled by default, #3212), Omnigent harvesting
    # (OmnigentAdapter.md), required capabilities (RequiredCapabilities.md),
    # Settings destinations (dashboard-app.tsx + settings tests), Progress
    # sort/filter (workflow-list.tsx), segmented control (MM-1138 CSS),
    # Unreal DinD path (conflicts with the API-owned container boundary).
    "MM-1003FinalAcceptanceSweep.md",
    "MM-1101PublishRepair.md",
    "ManagedRuntimeCleanupRollout.md",
    "OmnigentCheckpointingCompatibilityPlan.md",
    "SkillRequiredCapabilitiesHardeningPlan.md",
    "SettingsConfigurationPagesMigration.md",
    "WorkflowListProgressSortFilterRollout.md",
    "WorkspaceRailAndSegmentedControlUnificationPlan.md",
    "UnrealRuntimeProofProvisioning.md",
    # Deprecated historical design superseded by Observability/LiveLogs.md.
    "historical/LiveWorkflowManagement.md",
)

# Retained files and the marker proving an owning issue or delete/archive
# trigger. The slice-0 fixtures are executable test input guarded by
# test_repository_access_slice0_reconciliation.py instead of prose.
KEPT_MARKERS = {
    "docs/tmp/MoonMindRoadmapExecutionTracker.md": ("disposable",),
    "docs/tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md": (
        "Delete/Archive Trigger",
    ),
    "docs/tmp/DocsReviewRevalidationMM3966.md": ("Delete/Archive Trigger",),
    "docs/tmp/MM3518HistoricalTemporalCompatibilityStatus.md": (
        "delete this handoff once #3518",
    ),
    "docs/tmp/RunStatusMemoUpsertCutover.md": ("Delete/Archive Trigger",),
    "docs/tmp/OmnigentBridgeRollout.md": ("deleted or archived",),
    "docs/tmp/ProviderProfileTierSettingsRollout.md": ("deleted or archived",),
}

# Frozen/active review records may mention removed files as disposition
# history; they are not live consumers.
FROZEN_MENTIONS = {
    "docs/DocsReview.md",
    "docs/tmp/DocsReviewRevalidationMM3966.md",
    "tests/unit/docs/test_documentation_architecture_conformance.py",
}

# Scopes whose references would make a deleted file a live input again.
LIVE_SCOPES = ("moonmind/", "api_service/", "frontend/src/", "tools/")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def test_deleted_tmp_files_stay_removed() -> None:
    for rel in DELETED_BASENAMES:
        assert not (REPO_ROOT / "docs" / "tmp" / rel).exists(), rel


def test_kept_tmp_files_carry_ownership_or_trigger() -> None:
    for rel, markers in KEPT_MARKERS.items():
        path = REPO_ROOT / rel
        assert path.exists(), rel
        text = path.read_text(encoding="utf-8")
        assert any(marker in text for marker in markers), (rel, markers)
    fixtures = REPO_ROOT / "docs" / "tmp" / "repository-access-slice0-fixtures.yaml"
    assert fixtures.exists()


def test_no_live_consumer_references_deleted_tmp_files() -> None:
    offenders: list[str] = []
    for tracked in _tracked_files():
        if tracked in FROZEN_MENTIONS:
            continue
        if tracked == "tests/unit/docs/test_tmp_disposal_3963.py":
            continue
        if not (
            tracked.startswith(LIVE_SCOPES)
            or (tracked.startswith("docs/") and tracked.endswith(".md"))
            or (tracked.startswith("tests/") and tracked.endswith(".py"))
        ):
            continue
        if tracked.startswith("docs/tmp/"):
            continue
        try:
            text = (REPO_ROOT / tracked).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for basename in DELETED_BASENAMES:
            name = basename.rsplit("/", 1)[-1]
            if name in text:
                offenders.append(f"{tracked} -> {name}")
    assert not offenders, f"live consumers reference deleted tmp inputs: {offenders}"


def test_tmp_stays_outside_canonical_scope_and_advisory() -> None:
    assert not ARCH.is_canonical_doc("docs/tmp/SomePlan.md")
    assert not ARCH.is_canonical_doc("docs/tmp/historical/Old.md")
    assert ARCH.SEVERITY_ADVISORY == "advisory"
    assert LINKS.SEVERITY_ADVISORY == "advisory"
    assert "docs/tmp/historical" in LINKS.FROZEN_EVIDENCE_DIRS
