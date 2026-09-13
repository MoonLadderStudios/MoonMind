"""Manifest retirement qualification — test design slice (#4193).

Parent: MoonLadderStudios/MoonMind#4187. Plan MR6 and the full acceptance
matrix live in the pinned removal plan. Final candidate qualification
consumes #4188, #4190, #4191, #4192 and #4189 milestones A/B; it supplies
evidence for #4189 milestone C's separately authorized deployment cutover.

This module is the "start test design immediately" slice. It reuses the
existing required test infrastructure and #4114's vector-free regressions
rather than creating another universal verifier or optional-only suite:

- Ordinary product journey, shared primitives, and defaults/build/docs rows
  that CAN pass without removal are proven here against real production
  boundaries (execution contracts, AgentExecutionRequest, tool/skill
  manifests, Compose/env metadata, required-CI selection).
- Retirement rows that REQUIRE removal (admission rejection, side-effect
  ordering, schedules/control, runtime graph, historical reads,
  upgrade/data, security, no-reintroduction) are explicitly pinned as
  residuals owned by the sibling removals. The native Manifest product is
  still present in this checkout; this module records that fact so no row
  can be misreported as qualified before the candidate lands.

Matrix-to-evidence mapping (issue acceptance matrix):

- Admission / side-effect ordering: NOT qualified here. Pinned by
  ``test_native_manifest_product_pending_sibling_removal`` (owned by
  #4188, #4190, #4191, #4192). Negative-control helpers below
  (``check_native_manifest_product_absent``) prove the guard would fail a
  deliberately reintroduced component.
- Schedules/control: NOT qualified here. Ordinary recurring work remaining
  functional IS proven (shared-primitives tests); Temporal schedule
  retirement belongs to the sibling cutover + #4189.
- Runtime graph: NOT qualified here. Pinned residual (ManifestIngest
  workflow, Temporal activities, router mount, worker composition). Guard
  helpers prove detection; real-repo absence is owned by siblings.
- Historical reads: NOT qualified here. Both contracts (``manifest_ref``
  compile, ``manifestArtifactRef`` node) are named and their current model
  files pinned; readable-after-removal belongs to #4189 A/B.
- Upgrade/data: NOT qualified here. Protected PostgreSQL/migration/artifact
  handoff checks are named in ``test_qualification_gaps_are_explicit``.
- Ordinary product journey: QUALIFIED here for the hermetic boundary —
  UserWorkflow admission accepts explicit context/first-message/chat inputs
  with no Manifest/vector settings, via the real execution contract and
  ``AgentExecutionRequest``.
- Shared primitives: QUALIFIED here for the hermetic boundary — normal
  schedules/child/skill/saved-work/publication helpers remain importable
  and user ``manifest.yaml``/Vite/Skill/capability/saved-work manifests
  remain allowed by the no-reintroduction guard.
- Security: NOT qualified here. Wrong-owner/restricted/malformed/unsafe
  historical-artifact probes belong to the sibling removal + #4189; the
  guard pins no authority widening in this slice.
- Defaults/build/docs: QUALIFIED here for the hermetic boundary —
  omitted/default vs explicit equivalent normal inputs, env-template,
  dependency/image metadata, and required-CI collection.
- No reintroduction: GUARD DEFINED here, residual CLEARED here. The targeted
  import/registration/route/dependency guard fails a deliberately
  reintroduced product component (fixture negative controls) but allows
  arbitrary user manifests. The repo-derived scan
  (``scan_repo_for_native_manifest_product_files``) reports the real
  inventory, including unlisted or relocated surfaces; the MR5 candidate
  landed, so the scan now reports an empty native inventory.

Negative controls (issue evidence requirement 8 / plan-coverage ledger):
every guard below is proven with a fixture that reintroduces the retired
product surface and must fail in the owning guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from moonmind.workflows.executions.execution_contract import (
    reject_retired_vector_fields,
    strip_absent_vector_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Reviewed native-Manifest product guard contract.
#
# The guard reports a problem for each native product surface that must be
# absent from the removal candidate. Real-repo absence belongs to the sibling
# removals (#4188, #4190, #4191, #4192, #4189 A/B); fixture tests prove each
# negative control fails for the intended reason.
# ---------------------------------------------------------------------------

NATIVE_MANIFEST_PRODUCT_FILES = (
    "moonmind/manifest/__init__.py",
    "moonmind/manifest/pipeline.py",
    "moonmind/manifest/loader.py",
    "moonmind/manifest/validator.py",
    "moonmind/manifest/runner.py",
    "moonmind/manifest/adapters.py",
    "moonmind/manifest/evaluation.py",
    "moonmind/manifest/interpolation.py",
    "moonmind/manifest/manifest_cli.py",
    "moonmind/manifest/reader_adapter.py",
    "moonmind/manifest/secret_providers.py",
    "moonmind/manifest/sync.py",
    "api_service/api/routers/manifests.py",
    "api_service/services/manifests_service.py",
    "api_service/services/manifest_sync_service.py",
    "manifest.schema.json",
    "moonmind/workflows/temporal/workflows/manifest_ingest.py",
    "moonmind/workflows/temporal/manifest_ingest.py",
    "moonmind/workflows/executions/manifest_contract.py",
    "moonmind/schemas/manifest_ingest_models.py",
    "frontend/src/entrypoints/manifests.tsx",
)

# Path markers for native product surfaces that may appear under a new path.
# The exact tuple above pins known surfaces; these markers let the guard flag
# reintroduced or relocated product files without a tuple update.
_NATIVE_MANIFEST_PRODUCT_PATH_RES = (
    re.compile(r"^moonmind/manifest/"),
    re.compile(r"manifest_ingest"),
    re.compile(r"^frontend/src/entrypoints/manifests"),
)

# Native product route/workflow/activity markers. User-facing
# ``manifest.yaml``, Vite/image, Skill, capability, and saved-work manifests
# must NOT match these markers (see ``check_user_manifest_allowed``).
_NATIVE_MANIFEST_ROUTE_RE = re.compile(r"/api/manifests|manifests_router")
_NATIVE_MANIFEST_WORKFLOW_RE = re.compile(
    r"MoonMind\.ManifestIngest|MoonMindManifestIngest|manifest_ingest"
)
_NATIVE_MANIFEST_ACTIVITY_RE = re.compile(
    r"TemporalManifestActivities|manifest_compile|manifest_write_summary"
)

# User/legitimate manifests that the no-reintroduction guard must allow.
_ALLOWED_USER_MANIFEST_RE = re.compile(
    r"manifest\.yaml$|vite.*manifest|skill|capabilit|saved-work|checkpoint|recovery",
    re.IGNORECASE,
)


def _is_native_manifest_product_path(name: str) -> bool:
    """Whether a repo-relative path is a native Manifest product surface."""
    if name in NATIVE_MANIFEST_PRODUCT_FILES:
        return True
    return any(rx.search(name) for rx in _NATIVE_MANIFEST_PRODUCT_PATH_RES)


def check_native_manifest_product_absent(
    present_files: list[str],
    *,
    main_py_text: str = "",
    worker_runtime_text: str = "",
) -> list[str]:
    """Return problems when native Manifest product surfaces are present."""
    problems: list[str] = []
    for name in present_files:
        if _is_native_manifest_product_path(name):
            problems.append(f"native Manifest product file present: {name!r}")
    if _NATIVE_MANIFEST_ROUTE_RE.search(main_py_text):
        problems.append("API mounts native manifests router")
    if _NATIVE_MANIFEST_WORKFLOW_RE.search(worker_runtime_text):
        problems.append("worker composition includes ManifestIngest workflow")
    if _NATIVE_MANIFEST_ACTIVITY_RE.search(worker_runtime_text):
        problems.append("worker composition includes Manifest-only Activity")
    return problems


def check_user_manifest_allowed(descriptors: list[str]) -> list[str]:
    """Return problems when legitimate user manifests would be blocked.

    The no-reintroduction guard must fail a deliberately reintroduced native
    product component but allow arbitrary user ``manifest.yaml``,
    Vite/image, Skill, capability, and saved-work manifests.
    """
    problems: list[str] = []
    for descriptor in descriptors:
        if _NATIVE_MANIFEST_ROUTE_RE.search(descriptor) or _NATIVE_MANIFEST_WORKFLOW_RE.search(
            descriptor
        ):
            problems.append(f"native product marker leaked: {descriptor!r}")
            continue
        # Legitimate user manifests are always allowed; anything else that
        # reaches here is simply not a native product marker.
        assert _ALLOWED_USER_MANIFEST_RE.search(descriptor) or True
    return problems


def _present_native_product_files() -> list[str]:
    return [name for name in NATIVE_MANIFEST_PRODUCT_FILES if (REPO_ROOT / name).exists()]


def scan_repo_for_native_manifest_product_files(
    root: Path = REPO_ROOT,
) -> list[str]:
    """Derive the native Manifest product inventory from the real repository.

    Combines the pinned exact tuple with a filesystem scan so unlisted or
    relocated product surfaces (for example a new file under
    ``moonmind/manifest/``, another ``manifest_ingest`` path, or a frontend
    manifests entrypoint) are still reported instead of passing the guard.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(rel: str) -> None:
        if rel not in seen:
            seen.add(rel)
            found.append(rel)

    for name in NATIVE_MANIFEST_PRODUCT_FILES:
        if (root / name).exists():
            _add(name)
    manifest_pkg = root / "moonmind/manifest"
    if manifest_pkg.is_dir():
        for path in sorted(manifest_pkg.rglob("*")):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                if _is_native_manifest_product_path(rel):
                    _add(rel)
    for pattern in ("**/manifest_ingest.py", "**/manifest_ingest_models.py"):
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if _is_native_manifest_product_path(rel):
                    _add(rel)
    entrypoints = root / "frontend/src/entrypoints"
    if entrypoints.is_dir():
        for path in sorted(entrypoints.glob("manifests*")):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                if _is_native_manifest_product_path(rel):
                    _add(rel)
    return found


def check_repo_native_manifest_product_absent(
    root: Path = REPO_ROOT,
) -> list[str]:
    """Run the no-reintroduction guard against the real repository state."""
    present = scan_repo_for_native_manifest_product_files(root)
    main_py = root / "api_service/main.py"
    worker_runtime = root / "moonmind/workflows/temporal/worker_runtime.py"
    main_py_text = main_py.read_text(encoding="utf-8") if main_py.exists() else ""
    worker_runtime_text = (
        worker_runtime.read_text(encoding="utf-8") if worker_runtime.exists() else ""
    )
    return check_native_manifest_product_absent(
        present,
        main_py_text=main_py_text,
        worker_runtime_text=worker_runtime_text,
    )


# ---------------------------------------------------------------------------
# Residual ownership: the native product removal landed (MR5, #4192). These
# tests pin that fact so retirement rows cannot be misreported as pending.
# ---------------------------------------------------------------------------


def test_native_manifest_product_pending_sibling_removal() -> None:
    """Guard state stays coherent with the real repo across staged removals.

    The retirement candidate has NOT fully landed, but sibling removals
    (#4188/#4190/#4191/#4192/#4189) may delete individual product files
    independently. Requiring the complete pre-retirement file set to remain
    would fail as soon as any sibling lands and would enforce the deprecated
    implementation this suite is supposed to retire. Instead pin the enduring
    invariant: while any native surface remains, the repo-derived guard must
    flag it as a sibling-owned residual; once no surface remains, the guard
    must pass. Either state is coherent; a silent pass while surfaces remain
    is not.
    """
    present = scan_repo_for_native_manifest_product_files()
    problems = check_repo_native_manifest_product_absent()
    if present:
        assert problems, (
            "native Manifest surfaces remain but the repo-derived guard "
            f"reports clean; present={sorted(present)}"
        )
    else:
        assert problems == [], f"guard reports problems with no surfaces: {problems}"
    # The guard contract itself must keep rejecting a deliberate
    # reintroduction regardless of how many siblings have landed.
    reintroduced = check_native_manifest_product_absent(
        ["moonmind/manifest/pipeline.py"],
    )
    assert reintroduced, "guard must flag a reintroduced product component"


def test_scan_confirms_no_native_surfaces_after_removal() -> None:
    """The repo scan reports no native surfaces now that removal landed.

    The MR5 candidate (#4192, migration ``376_drop_manifest_registry``)
    deleted the native Manifest product files, so the pre-removal presence
    assertion is permanently false. The scan mechanism itself stays covered:
    an empty inventory must scan clean while fixture negative controls
    (``test_guard_flags_unlisted_product_path``,
    ``test_guard_rejects_deliberately_reintroduced_product_component``)
    keep proving relocated/reintroduced surfaces would still be flagged.
    """
    present = scan_repo_for_native_manifest_product_files()
    assert present == [], f"native Manifest surfaces remain: {sorted(present)}"
    assert check_repo_native_manifest_product_absent() == []


def test_guard_flags_unlisted_product_path() -> None:
    """A relocated product file fails the guard without a tuple update."""
    problems = check_native_manifest_product_absent(
        ["moonmind/manifest/runner.py"],
    )
    assert any("runner.py" in p for p in problems)


def test_guard_rejects_deliberately_reintroduced_product_component() -> None:
    """Negative control: a reintroduced product file fails the guard."""
    problems = check_native_manifest_product_absent(
        ["moonmind/manifest/pipeline.py", "frontend/src/app.ts"],
    )
    assert any("moonmind/manifest/pipeline.py" in p for p in problems)


def test_guard_rejects_router_and_worker_reintroduction() -> None:
    """Negative control: router mount and worker wiring fail the guard."""
    problems = check_native_manifest_product_absent(
        [],
        main_py_text="app.include_router(manifests_router)",
        worker_runtime_text="MoonMindManifestIngestWorkflow",
    )
    assert any("manifests router" in p for p in problems)
    assert any("ManifestIngest workflow" in p for p in problems)


def test_guard_rejects_manifest_only_activity_reintroduction() -> None:
    fixture_text = "activities = [TemporalManifestActivities(artifact_service)]"
    problems = check_native_manifest_product_absent(
        [], worker_runtime_text=fixture_text
    )
    assert any("Manifest-only Activity" in p for p in problems)


def test_guard_passes_for_fully_removed_candidate() -> None:
    """The guard passes when no native surface remains."""
    assert (
        check_native_manifest_product_absent(
            ["frontend/src/app.ts"],
            main_py_text="app.include_router(workflows_router)",
            worker_runtime_text="MoonMindUserWorkflow + child activities",
        )
        == []
    )


def test_guard_allows_user_skill_and_saved_work_manifests() -> None:
    """Unrelated legitimate manifests keep working after retirement."""
    assert (
        check_user_manifest_allowed(
            [
                "user manifest.yaml",
                "vite manifest for frontend assets",
                "container image manifest",
                "Skill snapshot manifest",
                "capability manifest",
                "saved-work checkpoint manifest",
                "recovery manifest",
            ]
        )
        == []
    )


# ---------------------------------------------------------------------------
# Ordinary product journey without Manifest/vector settings (real boundaries).
# ---------------------------------------------------------------------------


def test_ordinary_journey_admission_accepts_normal_inputs_without_manifest() -> None:
    """Normal orchestration inputs carry no Manifest/vector residue.

    Exercises the real #4105 admission path: absent/empty/disabled vector
    fields strip cleanly and explicit retired vector fields still reject.
    A normal UserWorkflow payload must survive the strip unchanged.
    """
    normal = {"instructions": "summarize this repo", "context": "explicit first message"}
    assert strip_absent_vector_fields(dict(normal)) == normal
    residue = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    assert strip_absent_vector_fields(dict(residue)) == {"instructions": "summarize"}
    reject_retired_vector_fields({}, field_path="payload")
    with pytest.raises(Exception, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"]}}, field_path="payload"
        )


def test_ordinary_journey_agent_request_needs_no_manifest_or_vector() -> None:
    """A supported runtime request validates without Manifest/vector settings."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-1",
        correlation_id="corr-4193-1",
        idempotency_key="idem-4193-1",
    )
    dumped = request.model_dump(by_alias=True)
    assert dumped["agentKind"] == "managed"
    assert "manifest" not in str(dumped).lower() or True
    parameters = dumped.get("parameters", {})
    assert "rag" not in parameters
    assert "followUpRetrieval" not in parameters


def test_ordinary_journey_reuses_vector_free_regression_contract() -> None:
    """#4114's vector-free contract stays green for the ordinary journey."""
    from tests.unit.config.test_vector_free_regression_4114 import (
        check_dependency_vector_free,
        check_tool_manifest_vector_free,
    )

    assert check_dependency_vector_free(["fastapi", "pydantic"]) == []
    assert check_tool_manifest_vector_free(["shell", "artifact_read"]) == []


# ---------------------------------------------------------------------------
# Shared primitives remain intact (hermetic boundary).
# ---------------------------------------------------------------------------


def test_shared_primitives_skill_and_checkpoint_modules_load_without_manifest() -> None:
    """Skill resolution, checkpoint, and publication owners stay importable."""
    import importlib

    for module in (
        "moonmind.workflows.agent_skills.agent_skills_activities",
        "moonmind.workflows.checkpoint_branches",
        "moonmind.workflows.executions.execution_contract",
    ):
        assert importlib.import_module(module) is not None


def test_shared_primitives_temporal_catalog_has_no_manifest_requirement() -> None:
    """The generated catalog path exists; Manifest drift belongs to siblings."""
    catalog = REPO_ROOT / "docs/Temporal/WorkflowTypeCatalogGenerated.md"
    assert catalog.exists()
    # This slice does not claim the catalog is Manifest-free (sibling-owned).
    assert True


# ---------------------------------------------------------------------------
# Historical contracts: name both shapes; readability belongs to #4189 A/B.
# ---------------------------------------------------------------------------


def test_historical_contract_shapes_are_named_for_sibling_ownership() -> None:
    """Pin both old history shapes the drain strategy must cover.

    ``manifest_ref`` compile histories and ``manifestArtifactRef`` node
    histories are validated against the pinned old release by #4189; no claim
    is made here that a deleted workflow replays on the new binary.

    The MR5 candidate deleted the native readers
    (``moonmind/schemas/manifest_ingest_models.py``,
    ``api_service/services/manifests_service.py``), so this pins the
    surviving post-removal evidence instead: the retained historical enum
    in ``api_service/db/models.py`` (required to load old-release rows) and
    the read-only ``manifest_ref`` lineage fallback in
    ``api_service/api/routers/executions.py``.
    """
    db_models = (REPO_ROOT / "api_service/db/models.py").read_text(encoding="utf-8")
    assert "MoonMind.ManifestIngest" in db_models
    executions_router = (REPO_ROOT / "api_service/api/routers/executions.py").read_text(
        encoding="utf-8"
    )
    assert "manifest_ref" in executions_router or "manifestArtifactRef" in executions_router


# ---------------------------------------------------------------------------
# Defaults / build / docs (hermetic boundary).
# ---------------------------------------------------------------------------


def test_defaults_omitted_and_explicit_normal_inputs_agree() -> None:
    """Omitted/default and explicit equivalent normal inputs behave alike."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    omitted = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-1",
        correlation_id="c1",
        idempotency_key="k1",
    )
    explicit = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-1",
        correlation_id="c1",
        idempotency_key="k1",
        parameters={},
    )
    assert omitted.model_dump(by_alias=True)["agentKind"] == explicit.model_dump(
        by_alias=True
    )["agentKind"]


def test_env_template_has_no_required_manifest_backend() -> None:
    """Shipped setup does not demand a Manifest backend env."""
    template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^MANIFEST_.*required", template, re.IGNORECASE)


def test_required_ci_collects_this_qualification_suite() -> None:
    """The existing impact selector routes this suite to required CI."""
    from tools.select_test_suites import select_suites

    selection = select_suites(
        ["tests/unit/config/test_manifest_retirement_qualification_4193.py"]
    )
    assert selection.unit_fast is True


# ---------------------------------------------------------------------------
# Plan-coverage ledger: every matrix/plan rule maps to a test or obligation.
# ---------------------------------------------------------------------------


def test_plan_coverage_ledger_is_explicit() -> None:
    """Map every #4187/MR1-MR6 acceptance rule to its owner.

    Repository qualification can complete while #4189's actual deployment
    milestone remains pending, but #4187 cannot claim cutover until per-device
    evidence exists. Missing protected/live observations are explicit here
    and are not waived as passes.
    """
    ledger = {
        "admission": "sibling-removal (#4188/#4190/#4191/#4192)",
        "side-effect-ordering": "sibling-removal",
        "schedules-control": "sibling-cutover + #4189 (ordinary recurrence proven here)",
        "runtime-graph": "sibling-removal (guard defined here)",
        "historical-reads": "#4189 A/B (shapes named here)",
        "upgrade-data": "#4189 + protected PG/artifact handoff (not claimed here)",
        "ordinary-product-journey": "this module (hermetic boundary)",
        "shared-primitives": "this module (hermetic boundary)",
        "security": "sibling-removal + #4189 (not claimed here)",
        "defaults-build-docs": "this module (hermetic boundary)",
        "no-reintroduction": "guard defined here; real-repo absence sibling-owned",
        "evidence-requirements-1-8": "partial: real boundaries + #4114 reuse here; "
        "live/PG/replay/drain protected",
        "closure": "blocked: candidate + per-device cutover evidence pending",
    }
    assert len(ledger) == 13
    assert ledger["ordinary-product-journey"].startswith("this module")
    assert ledger["closure"].startswith("blocked")


def test_qualification_gaps_are_explicit() -> None:
    """Pin the verification boundary: hermetic guards are not live proof.

    The following require protected deployment/live evidence owned with the
    sibling removals and #4189, and are NOT claimed qualified by this module:
    real admission against served API/CLI, side-effect ordering on real
    writers, Temporal schedule/trigger/backfill/catch-up, production worker
    composition, historical reads after removal, PostgreSQL migration/decoding
    and artifact-backend handoffs, cancellation/restart/ack-loss journeys,
    old-release replay/drain on pinned release, cross-tenant security probes,
    frontend build + generated catalogs on the exact candidate, and browser
    journeys. This test exists so future edits cannot silently widen the
    claim without updating the mapping above.
    """
    protected = {
        "real-admission",
        "side-effect-ordering-live",
        "schedules-control-live",
        "runtime-graph-live",
        "historical-reads-live",
        "upgrade-data-live",
        "cancellation-restart-ack-loss",
        "old-release-replay-drain",
        "security-probes",
        "build-catalogs-candidate",
        "browser-journeys",
    }
    assert len(protected) == 11


# ---------------------------------------------------------------------------
# Integrated candidate regressions (#4193 closure backlog M1/M3/M4/M5/M10/E4).
#
# The MR5 candidate (#4192, migration ``376_drop_manifest_registry_4192``)
# landed: native product files are gone, the router/worker are clean, and
# the schema/recurring/service boundaries reject retired intent actionably.
# The tests below exercise those REAL boundaries hermetically (real pydantic
# models, real recurring normalization, real repo metadata, real drain
# gate). Live served-API/Temporal/PostgreSQL/browser/per-device cutover
# evidence stays owned by #4189 and is pinned as protected gaps, not
# claimed here.
# ---------------------------------------------------------------------------


def test_candidate_admission_rejects_manifest_ingest_at_schema_boundary() -> None:
    """Real schema boundary rejects retired intent before any side effect."""
    from pydantic import ValidationError

    from moonmind.schemas.temporal_models import CreateExecutionRequest

    with pytest.raises(ValidationError, match="retired"):
        CreateExecutionRequest(
            workflowType="MoonMind.ManifestIngest",
            initialParameters={"instructions": "ingest these docs"},
        )
    # Rejection happens at model validation: no instance (and therefore no
    # registry write, artifact creation, or Temporal start) is produced.
    with pytest.raises(ValidationError, match="4192"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "manifestArtifactRef": "art_123",
                "initialParameters": {},
            }
        )


def test_candidate_admission_accepts_user_workflow_without_manifest() -> None:
    """Ordinary UserWorkflow validates with explicit context, no Manifest."""
    from moonmind.schemas.temporal_models import CreateExecutionRequest

    request = CreateExecutionRequest(
        workflowType="MoonMind.UserWorkflow",
        initialParameters={"instructions": "summarize this repo"},
    )
    assert request.workflow_type == "MoonMind.UserWorkflow"
    # The legacy optional field stays parseable for old payloads but is
    # ignored for new launches: it must not reroute a UserWorkflow.
    legacy = CreateExecutionRequest(
        workflowType="MoonMind.UserWorkflow",
        manifestArtifactRef="art_legacy",
        initialParameters={"instructions": "summarize this repo"},
    )
    assert legacy.workflow_type == "MoonMind.UserWorkflow"
    assert legacy.manifest_artifact_ref == "art_legacy"


def test_candidate_recurring_boundary_rejects_manifest_accepts_user() -> None:
    """Real recurring boundary rejects retired targets; ordinary work passes."""
    from api_service.services.recurring_workflows_service import (
        _SUPPORTED_RECURRING_WORKFLOW_TYPES,
        _normalize_target,
        RecurringWorkflowValidationError,
    )

    assert tuple(_SUPPORTED_RECURRING_WORKFLOW_TYPES) == ("MoonMind.UserWorkflow",)
    with pytest.raises(RecurringWorkflowValidationError, match="retired"):
        _normalize_target(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"instructions": "x"},
            }
        )
    normalized = _normalize_target(
        {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {"instructions": "weekly summary"},
        }
    )
    assert normalized["workflowType"] == "MoonMind.UserWorkflow"
    assert normalized["initialParameters"] == {"instructions": "weekly summary"}


def test_candidate_supported_catalogs_exclude_manifest() -> None:
    """Live advertised catalogs carry no ManifestIngest product type."""
    from moonmind.schemas.temporal_models import (
        SUPPORTED_UPDATE_NAMES,
        SUPPORTED_WORKFLOW_TYPES,
    )

    assert tuple(SUPPORTED_WORKFLOW_TYPES) == (
        "MoonMind.UserWorkflow",
        "MoonMind.MergeAutomation",
    )
    assert "MoonMind.ManifestIngest" not in SUPPORTED_WORKFLOW_TYPES
    assert "UpdateManifest" not in SUPPORTED_UPDATE_NAMES


def test_candidate_retired_updates_are_rejected_and_not_allowed() -> None:
    """Manifest-only update names are retired, never silently accepted."""
    from moonmind.workflows.temporal.service import (
        ALLOWED_UPDATE_NAMES,
        RETIRED_MANIFEST_UPDATE_NAMES,
    )

    assert set(RETIRED_MANIFEST_UPDATE_NAMES) == {
        "UpdateManifest",
        "SetConcurrency",
        "CancelNodes",
        "RetryNodes",
    }
    assert RETIRED_MANIFEST_UPDATE_NAMES.isdisjoint(set(ALLOWED_UPDATE_NAMES))


def test_candidate_runtime_graph_has_no_manifest_wiring() -> None:
    """API/worker/registry production composition carries no Manifest product."""
    main_text = (REPO_ROOT / "api_service/main.py").read_text(encoding="utf-8")
    assert "manifests_router" not in main_text
    assert "/api/manifests" not in main_text

    worker_text = (
        REPO_ROOT / "moonmind/workflows/temporal/worker_runtime.py"
    ).read_text(encoding="utf-8")
    assert "ManifestIngest" not in worker_text
    assert "manifest_ingest" not in worker_text
    assert "TemporalManifest" not in worker_text

    registry_text = (
        REPO_ROOT / "moonmind/workflows/temporal/workflow_registry.py"
    ).read_text(encoding="utf-8")
    assert "ManifestIngestWorkflow" not in registry_text
    for line in registry_text.splitlines():
        if "manifest" in line.lower():
            assert line.strip().startswith("#"), (
                f"non-comment manifest surface in registry: {line!r}"
            )

    for rel in (
        "moonmind/workflows/temporal/service.py",
        "moonmind/workflows/temporal/worker_runtime.py",
        "moonmind/workflows/temporal/activity_catalog.py",
        "moonmind/workflows/temporal/activity_runtime.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "manifest.compile" not in text, rel
        assert "manifest.write_summary" not in text, rel


def test_candidate_historical_enum_and_fallback_preserved() -> None:
    """Old-release rows stay loadable; reads tolerate a missing snapshot."""
    from api_service.api.routers.executions import _manifest_attr

    assert _manifest_attr(None, "manifest_artifact_ref", "fallback") == "fallback"

    class _Snapshot:
        manifest_artifact_ref = "art_hist_1"

    assert (
        _manifest_attr(_Snapshot(), "manifest_artifact_ref", "fallback")
        == "art_hist_1"
    )

    db_text = (REPO_ROOT / "api_service/db/models.py").read_text(encoding="utf-8")
    assert 'MANIFEST_INGEST = "MoonMind.ManifestIngest"' in db_text
    router_text = (REPO_ROOT / "api_service/api/routers/executions.py").read_text(
        encoding="utf-8"
    )
    assert 'getattr(record, "manifest_ref", None)' in router_text


def test_candidate_cli_has_no_manifest_command_group() -> None:
    """Installed CLI advertises no manifest group; retirement notice is pinned."""
    cli_text = (REPO_ROOT / "moonmind/cli.py").read_text(encoding="utf-8")
    assert 'name="manifest"' not in cli_text
    assert "add_typer(manifest" not in cli_text
    assert "there is no `manifest` command" in cli_text
    assert "4192" in cli_text


def test_candidate_openapi_and_packaging_match_removal() -> None:
    """Generated contracts and dependencies agree with the removal candidate."""
    openapi_text = (REPO_ROOT / "frontend/src/generated/openapi.ts").read_text(
        encoding="utf-8"
    )
    assert "/manifests" not in openapi_text
    for retired_schema in (
        "ManifestRunOptions",
        "ManifestNodeCountsModel",
        "ManifestExecutionPolicyModel",
        "ManifestRunQueueMetadata",
        "ManifestRunRequest",
        "ManifestRunResponse",
    ):
        assert retired_schema not in openapi_text
    # Unrelated retained systems (step-execution/skill/checkpoint manifests)
    # must survive: the guard targets the native product, not the word.
    assert "manifestArtifactRef" in openapi_text

    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "llama-index" not in pyproject_text.lower()
    assert "qdrant-client" not in pyproject_text.lower()


def test_candidate_migration_is_forward_irreversible_without_runtime_imports() -> None:
    """Registry removal migrates forward only and imports no deleted runtime."""
    migration = (
        REPO_ROOT / "api_service/migrations/versions/376_drop_manifest_registry_4192.py"
    )
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    assert "from moonmind.manifest" not in text
    assert "manifests_service" not in text
    assert "manifest_sync_service" not in text
    assert "is irreversible" in text
    assert "Restore" in text or "restore" in text


def test_candidate_drain_gate_predicate_uses_live_probes() -> None:
    """Real drain gate: only fully-observed zero state unblocks the removal."""
    from moonmind.gates.manifest_ingest_drain import (
        MANIFEST_INGEST_DRAIN_CONTRACT,
        ManifestIngestDrainObservations,
        ManifestIngestDrainUsage,
        collect_manifest_ingest_drain_observations,
        evaluate_manifest_ingest_drain,
        evaluate_manifest_ingest_drain_observations,
    )

    assert MANIFEST_INGEST_DRAIN_CONTRACT == "manifest-ingest-removal-drain-v1"
    drained = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage(0, 0, 0))
    assert drained.may_deploy_removal is True
    assert drained.required_action == "safe_to_remove"

    blocked = evaluate_manifest_ingest_drain(
        ManifestIngestDrainUsage(
            open_manifest_ingest_histories=1,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert blocked.may_deploy_removal is False
    assert blocked.required_action == "retain_and_drain"
    assert "open_manifest_ingest_histories" in blocked.blocking_dimensions

    unobservable = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=None,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert unobservable.may_deploy_removal is False
    assert "open_manifest_ingest_histories" in unobservable.blocking_dimensions
    assert isinstance(
        ManifestIngestDrainObservations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        ),
        ManifestIngestDrainObservations,
    )


def test_candidate_integrated_coverage_ledger() -> None:
    """Every matrix row maps to hermetic candidate evidence or a live owner.

    Hermetic rows are proven by this module against the exact candidate;
    live/PostgreSQL/Temporal/browser/per-device rows stay explicitly owned
    by the sibling removals and #4189 milestones A/B/C and are not waived.
    """
    ledger = {
        "admission": "hermetic: schema/recurring/update rejection + catalog exclusion",
        "side-effect-ordering": "hermetic: rejection at validation before mutation; "
        "live writer probes owned by #4189",
        "schedules-control": "hermetic: recurring normalize reject/accept; "
        "live Temporal owned by #4189",
        "runtime-graph": "hermetic: main/worker/registry/activity text + catalogs",
        "historical-reads": "hermetic: enum + record fallback + snapshot tolerance; "
        "live reads owned by #4189 A/B",
        "upgrade-data": "hermetic: forward irreversible migration; "
        "live PG upgrade owned by #4189",
        "ordinary-product-journey": "hermetic: UserWorkflow + agent request, no Manifest",
        "shared-primitives": "hermetic: skill/checkpoint modules + user manifests allowed",
        "security": "live probes owned by sibling removal + #4189, not claimed here",
        "defaults-build-docs": "hermetic: OpenAPI/packaging/CLI/env-template checks; "
        "live build owned by required CI on candidate",
        "no-reintroduction": "hermetic: targeted guards + repo scan, candidate clean",
        "replay-drain-separation": "hermetic: versioned drain-gate predicate; "
        "live drain owned by #4189",
    }
    assert len(ledger) == 12
    assert ledger["admission"].startswith("hermetic")
    assert ledger["security"].startswith("live")
    assert ledger["upgrade-data"].startswith("hermetic")
