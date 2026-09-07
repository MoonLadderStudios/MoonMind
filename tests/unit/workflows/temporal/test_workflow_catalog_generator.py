"""Drift and composition tests for the mechanical Temporal catalog (#3959).

Every test exercises the real production boundary — the workflow registry,
the pinned Temporal SDK, actual worker construction, the Activity catalog and
its runtime bindings — never a second hand-written expected-name list or a
docs-only name search.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from temporalio import workflow

from tools.generate_temporal_catalog import (
    GENERATED_MARKER,
    collect_activities,
    collect_search_attributes,
    collect_workflows,
    document_anchors,
    generate,
    render_reference,
)
from moonmind.workflows.temporal import workflow_registry
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    TemporalActivityCatalogError,
    TemporalActivityDefinition,
    TemporalActivityRetries,
    TemporalActivityTimeouts,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    validate_activity_catalog_runtime_bindings,
)
from moonmind.workflows.temporal.scheduled_start import (
    MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE,
)
from moonmind.workflows.temporal.workflow_registry import (
    WorkflowRegistration,
    WorkflowRegistrationError,
    raw_workflow_registrations,
    validate_workflow_registrations,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
GENERATED_REF = (
    REPO_ROOT / "docs" / "Temporal" / "WorkflowTypeCatalogGenerated.md"
)
CANONICAL_DOC = (
    REPO_ROOT / "docs" / "Temporal" / "WorkflowTypeCatalogAndLifecycle.md"
)


@workflow.defn(name="MoonMind.CatalogGeneratorTestOnly")
class _CatalogGeneratorTestOnlyWorkflow:
    """Intentional test/replay-only class; never a production registration."""

    @workflow.run
    async def run(self) -> None:
        """Test-only entry point; never executed."""


@workflow.defn(name="MoonMind.CatalogGeneratorDuplicate")
class _CatalogGeneratorDuplicateWorkflowA:
    @workflow.run
    async def run(self) -> None:
        """Test-only entry point; never executed."""


@workflow.defn(name="MoonMind.CatalogGeneratorDuplicate")
class _CatalogGeneratorDuplicateWorkflowB:
    @workflow.run
    async def run(self) -> None:
        """Test-only entry point; never executed."""


def _test_only_registration() -> WorkflowRegistration:
    return WorkflowRegistration(
        __name__,
        "_CatalogGeneratorTestOnlyWorkflow",
        "excluded",
    )


def test_checked_in_reference_matches_production_registries():
    """Drift gate: the generated reference must equal fresh generation."""

    assert GENERATED_REF.read_text(encoding="utf-8") == generate()


def test_generation_is_byte_stable_offline():
    assert generate() == generate()


def test_test_only_registration_appears_after_regeneration():
    """A test-only registration enumerates; production worker sets do not."""

    from tools.generate_temporal_catalog import (
        collect_workflow_fleet_handlers,
        collect_workflow_queues,
    )

    registrations = (*raw_workflow_registrations(), _test_only_registration())
    rows = collect_workflows(registrations=registrations)
    assert "MoonMind.CatalogGeneratorTestOnly" in {
        row.temporal_type for row in rows
    }

    activities = collect_activities()
    current, historical = collect_workflow_fleet_handlers()
    required, optional = collect_search_attributes()
    start_queue, poll_queues = collect_workflow_queues()
    rendered = render_reference(
        rows,
        activities,
        current,
        historical,
        required,
        optional,
        start_queue,
        poll_queues,
    )
    assert "MoonMind.CatalogGeneratorTestOnly" in rendered
    # The production reference never carries the test-only entry.
    assert "MoonMind.CatalogGeneratorTestOnly" not in generate()


def test_duplicate_temporal_name_fails_before_dictionary_reduction():
    """Raw registrations with one Temporal name fail instead of key-merging."""

    module = __name__
    registrations = (
        WorkflowRegistration(
            module, "_CatalogGeneratorDuplicateWorkflowA", "operator"
        ),
        WorkflowRegistration(
            module, "_CatalogGeneratorDuplicateWorkflowB", "operator"
        ),
    )
    with pytest.raises(WorkflowRegistrationError, match="Duplicate Temporal"):
        validate_workflow_registrations(registrations)


def test_missing_owner_metadata_and_scope_fail_actionably():
    with pytest.raises(WorkflowRegistrationError, match="owner metadata"):
        validate_workflow_registrations(
            (WorkflowRegistration("", "_CatalogGeneratorTestOnlyWorkflow", "operator"),)
        )
    with pytest.raises(WorkflowRegistrationError, match="projection scope"):
        validate_workflow_registrations(
            (
                WorkflowRegistration(
                    __name__, "_CatalogGeneratorTestOnlyWorkflow", "bogus"  # type: ignore[arg-type]
                ),
            )
        )
    with pytest.raises(WorkflowRegistrationError, match="cannot be resolved"):
        validate_workflow_registrations(
            (
                WorkflowRegistration(
                    "moonmind.workflows.temporal.workflows.does_not_exist",
                    "NoSuchWorkflow",
                    "operator",
                ),
            )
        )


def test_generated_types_agree_with_actual_worker_construction():
    """Reference, worker classes, and worker listing agree via the pinned SDK."""

    from moonmind.workflows.temporal.workers import (
        list_registered_workflow_types_for_settings,
    )
    from tools.generate_temporal_catalog import default_temporal_settings

    rows = collect_workflows()
    generated = {row.temporal_type for row in rows}
    constructed = {
        workflow._Definition.must_from_class(cls).name
        for cls in workflow_registry.workflow_fleet_workflow_classes()
    }
    listed = set(
        list_registered_workflow_types_for_settings(default_temporal_settings())  # type: ignore[arg-type]
    )
    assert generated == constructed == listed
    # Never silently omit unfamiliar, version-looking, or non-user types.
    assert "MoonMind.PublicationRecoveryV1" in generated
    assert "MoonMind.ContainerJob" in generated
    assert "MoonMind.OmnigentOAuthHostJanitor" in generated


def test_stale_cached_maps_do_not_hide_new_registrations(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cache isolation: a test that registers a type stays visible."""

    before = workflow_registry.workflow_projection_scopes()
    assert "MoonMind.CatalogGeneratorTestOnly" not in before

    monkeypatch.setattr(
        workflow_registry,
        "STATIC_WORKFLOW_REGISTRATIONS",
        (*workflow_registry.STATIC_WORKFLOW_REGISTRATIONS, _test_only_registration()),
    )
    try:
        rows = collect_workflows()
        assert "MoonMind.CatalogGeneratorTestOnly" in {
            row.temporal_type for row in rows
        }
        assert "MoonMind.CatalogGeneratorTestOnly" in (
            workflow_registry.workflow_projection_scopes()
        )
    finally:
        from tools.generate_temporal_catalog import clear_registry_caches

        clear_registry_caches()


def test_activity_routes_come_from_catalog_and_bindings_agree():
    """Catalog routes plus per-fleet skill aliases validate as one composition."""

    from tools.generate_temporal_catalog import (
        _SKILL_EXECUTION_ALIASES,
        default_temporal_settings,
    )

    cfg = default_temporal_settings()
    catalog = build_default_activity_catalog(cfg)  # type: ignore[arg-type]
    validate_activity_catalog_runtime_bindings(catalog)
    rows = collect_activities()
    row_keys = {
        (row.activity_type, row.fleet, row.task_queue) for row in rows
    }
    catalog_keys = {
        (definition.activity_type, definition.fleet)
        for definition in catalog.activities
    }
    expected_aliases = {
        (alias, fleet.fleet, fleet.task_queues[0])
        for alias in _SKILL_EXECUTION_ALIASES
        for fleet in catalog.fleets
        if fleet.fleet != "workflow" and (alias, fleet.fleet) not in catalog_keys
    }
    assert row_keys == {
        (
            definition.activity_type,
            definition.fleet,
            definition.task_queue,
        )
        for definition in catalog.activities
    } | expected_aliases
    assert len(rows) == len({(row.activity_type, row.fleet) for row in rows})
    # The aliases are real worker registrations (bound by
    # build_activity_bindings whenever skill activities are present), not
    # catalog definitions: every non-workflow fleet carries both.
    deployment_queue = next(
        fleet.task_queues[0] for fleet in catalog.fleets if fleet.fleet == "deployment"
    )
    assert ("mm.tool.execute", "deployment", deployment_queue) in row_keys
    assert ("mm.skill.execute", "deployment", deployment_queue) in row_keys


def test_unsupported_activity_route_binding_fails():
    """An activity outside its fleet's queues fails instead of rendering."""

    with pytest.raises(TemporalActivityCatalogError):
        TemporalActivityCatalog(
            activities=(
                TemporalActivityDefinition(
                    activity_type="test.unroutable",
                    family="test",
                    capability_class="artifacts",
                    task_queue="mm.activity.llm",
                    fleet="artifacts",
                    timeouts=TemporalActivityTimeouts(30, 60),
                    retries=TemporalActivityRetries(
                        max_attempts=1, max_interval_seconds=30
                    ),
                ),
            ),
            fleets=(
                TemporalWorkerFleet(
                    fleet="artifacts",
                    task_queues=("mm.activity.artifacts",),
                    capabilities=("artifacts",),
                    privileges=("artifact_store",),
                    scaling_notes="test",
                ),
            ),
        )


def test_workflow_fleet_handlers_split_current_and_historical():
    """Checkpoint handlers stay historical-only; the live lane stays current."""

    from tools.generate_temporal_catalog import collect_workflow_fleet_handlers

    current, historical = collect_workflow_fleet_handlers()
    current_names = {name for name, _ in current}
    historical_names = {name for name, _ in historical}
    assert current_names == {
        "integration.resolve_adapter_metadata",
        "integration.get_activity_route",
        "integration.resolve_external_adapter",
        "integration.external_adapter_execution_style",
    }
    assert historical_names == {
        "checkpoint_branch.turn.mark_running",
        "checkpoint_branch.turn.persist_terminal",
        "checkpoint_branch.turn.persist_terminal_rejection",
    }
    assert not (current_names & historical_names)


def test_search_attributes_come_from_registration_owners():
    required, optional = collect_search_attributes()
    names = [name for name, _ in required]
    assert MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE in names
    for name in ("mm_state", "mm_entry", "mm_owner_id", "mm_started_at"):
        assert name in names
    optional_names = [name for name, _ in optional]
    for name in (
        "mm_title",
        "mm_has_dependencies",
        "mm_dependency_count",
        "AgentRunId",
        "RuntimeId",
        "SessionId",
        "SessionEpoch",
        "SessionStatus",
        "IsDegraded",
    ):
        assert name in optional_names


def test_generated_output_marks_itself_and_leaks_no_secrets_or_paths():
    rendered = generate()
    assert rendered.startswith(GENERATED_MARKER)
    assert "/tmp/" not in rendered
    assert os.path.expanduser("~") not in rendered or os.path.expanduser(
        "~"
    ) == "/"
    for token in ("ghp_", "github_pat_", "AIza", "AKIA", "BEGIN PRIVATE KEY"):
        assert token not in rendered


def test_canonical_doc_has_no_duplicate_inventory_or_live_name_row():
    """The handwritten catalog no longer competes with the generated view."""

    text = CANONICAL_DOC.read_text(encoding="utf-8")
    assert "- `MoonMind.UserWorkflow`\n- `MoonMind.UserWorkflow`" not in text
    assert "| `MoonMind.UserWorkflow` | Current live implementation name" not in text
    assert "Current live implementation name" not in text
    anchors = document_anchors(CANONICAL_DOC)
    assert "111-moonminduserworkflow-lifecycle" in anchors
    assert "119-moonmindmergeautomation-lifecycle" in anchors
