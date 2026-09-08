"""Semantic/boundary verification for issue #3960.

Reconciles Temporal documentation claims with actual handlers, routing, and
verified behavior. Unlike exact-prose assertions, these tests derive
expectations from the production code boundary (handler definitions, the
activity catalog, the fleet registry, task-queue constants) and then require
the reader-facing docs to agree with that derived state.
"""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from temporalio import activity, workflow

from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import (
    validate_activity_catalog_runtime_bindings,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tools.generate_temporal_catalog import default_temporal_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs" / "Temporal"
TYPE_CATALOG = DOCS / "WorkflowTypeCatalogAndLifecycle.md"
PAUSE_SYSTEM = DOCS / "WorkerPauseSystem.md"
TOPOLOGY = DOCS / "ActivityCatalogAndWorkerTopology.md"
ARCHITECTURE = DOCS / "TemporalArchitecture.md"
SIGNALS = DOCS / "TemporalSignalsSystem.md"

RUN_WF = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "workflows" / "run.py"
CLIENT = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "client.py"
CATALOG = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "activity_catalog.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(source: str) -> str:
    """Collapse whitespace so assertions are not line-break sensitive."""
    return re.sub(r"\s+", " ", source)


def test_pause_resume_are_updates_not_signals_on_all_types() -> None:
    for cls in (MoonMindUserWorkflow, MoonMindAgentRun, MoonMindManifestIngestWorkflow):
        definition = workflow._Definition.must_from_class(cls)
        assert {"Pause", "Resume"} <= definition.updates.keys()
        assert not {"Pause", "Resume"} & definition.signals.keys()

    doc = _normalized(_read(TYPE_CATALOG))
    assert "### Signal: `Pause` / `Resume`" not in doc
    assert "Update: `Pause` / `Resume`" in doc
    for workflow_type in (
        "MoonMind.UserWorkflow",
        "MoonMind.AgentRun",
        "MoonMind.ManifestIngest",
    ):
        assert workflow_type in doc


def test_only_user_workflow_exposes_control_state_query() -> None:
    definition = workflow._Definition.must_from_class(MoonMindUserWorkflow)
    assert "control_state" in definition.queries
    for cls in (MoonMindAgentRun, MoonMindManifestIngestWorkflow):
        assert "control_state" not in workflow._Definition.must_from_class(cls).queries

    doc = _normalized(_read(TYPE_CATALOG))
    assert "control_state" in doc
    assert "Accepted" in doc  # acceptance vs safe-point distinction present


def test_pause_resume_validation_varies_by_workflow_type() -> None:
    manifest = workflow._Definition.must_from_class(MoonMindManifestIngestWorkflow)
    agent = workflow._Definition.must_from_class(MoonMindAgentRun)
    for name in ("Pause", "Resume"):
        assert manifest.updates[name].validator is None
        assert agent.updates[name].validator is not None

    doc = _normalized(_read(TYPE_CATALOG))
    assert "validation varying by workflow type" in doc
    assert "defines none and always accepts" in doc
    # The old conflation must be gone: ACCEPTED stage vs handler completion.
    assert "Accepted means the flag flipped" not in doc
    assert "Temporal `ACCEPTED` only means" in doc


def test_system_fan_out_targets_only_user_workflow_type() -> None:
    client = _read(CLIENT)
    assert 'WorkflowType="{RENAMED_USER_WORKFLOW_TYPE}"' in client
    assert "server Batch Operations" not in client

    pause_doc = _normalized(_read(PAUSE_SYSTEM))
    assert "Update acceptance only establishes `accepted`" in pause_doc
    assert "#3953" in pause_doc
    # No advertised guarantee: acceptance must never be upgraded, and the
    # fan-out limits are stated honestly (no server Batch Operations).
    assert "server Batch Operations are not used" in pause_doc
    assert "Update acceptance never proves a safe paused state" in pause_doc


def test_skill_operations_match_live_catalog_and_bindings() -> None:
    catalog = build_default_activity_catalog(default_temporal_settings())
    validate_activity_catalog_runtime_bindings(catalog)
    expected = {
        definition.activity_type
        for definition in catalog.activities
        if definition.activity_type.startswith("agent_skill.")
    }
    assert "agent_skill.resolve" in expected

    doc = _normalized(_read(TOPOLOGY))
    for op in expected:
        assert op in doc, f"docs must list actually registered operation {op}"
    assert "not yet a core live catalog family" not in doc
    # Executable tool contracts vs portable instruction bundles stay distinct.
    assert "must not reimplement Skill semantics" in doc


def test_only_resolve_has_production_workflow_caller() -> None:
    run_src = _read(RUN_WF)
    assert '"agent_skill.resolve"' in run_src
    for op in (
        "agent_skill.query_on_demand",
        "agent_skill.request_on_demand",
        "agent_skill.build_prompt_index",
        "agent_skill.materialize",
    ):
        assert f'"{op}"' not in run_src

    doc = _normalized(_read(TOPOLOGY))
    assert "only `resolve` has a production workflow caller" in doc


def test_workflow_fleet_helpers_match_registry_boundary() -> None:
    from moonmind.workflows.temporal.workflow_registry import (
        workflow_fleet_activity_handlers,
    )

    handlers = workflow_fleet_activity_handlers()
    assert handlers
    handler_names = {activity._Definition.must_from_callable(h).name for h in handlers}

    for doc_path in (TOPOLOGY, ARCHITECTURE):
        doc = _normalized(_read(doc_path))
        for activity_type in handler_names:
            assert activity_type in doc, f"{doc_path.name} must list {activity_type}"
        assert "#3949" in doc


def test_queue_inventory_matches_client_scope() -> None:
    from moonmind.workflows.temporal.client import _MOONMIND_TASK_QUEUES

    queues = _MOONMIND_TASK_QUEUES
    assert queues
    assert "mm.workflow.merge_automation" in queues

    catalog_src = _read(CATALOG)
    assert "def get_workflow_poll_task_queues" in catalog_src
    settings_src = (
        REPO_ROOT / "moonmind" / "config" / "settings.py"
    ).read_text(encoding="utf-8")
    assert "mm.workflow.user.v2" in settings_src

    doc = _normalized(_read(TOPOLOGY))
    for queue in queues:
        assert queue in doc, f"docs must list production queue {queue}"
    assert "mm.workflow.merge_automation" in doc
    # Production poll topology is wider than the drain/fan-out tuple: the
    # default start queue must be listed alongside the replay queue.
    assert "mm.workflow.user.v2" in doc
    assert "get_workflow_poll_task_queues()" in doc
    # Queue name vs worker vs process vs container vs profile stay distinct.
    assert "multiple replicas can serve one queue" in doc


def test_materialization_non_mutation_gap_disclosed() -> None:
    doc = _normalized(_read(TOPOLOGY))
    assert "must not mutate checked-in source trees in place" in doc
    assert "_project_builtin_support_directory" in doc


def test_canonical_docs_carry_no_issue_dispositions() -> None:
    for doc_path in (TOPOLOGY, TYPE_CATALOG, PAUSE_SYSTEM, ARCHITECTURE, SIGNALS):
        assert "Disposition (issue #3960" not in _read(doc_path), (
            f"{doc_path.name} must not carry issue-history disposition narratives"
        )


def test_finding_cross_links_present() -> None:
    combined = _normalized(
        "\n".join(
            [_read(TYPE_CATALOG), _read(PAUSE_SYSTEM), _read(TOPOLOGY), _read(ARCHITECTURE)]
        )
    )
    for ref in ("#3959", "#3953", "#3949"):
        assert ref in combined, f"docs must cross-link {ref}"


@pytest.mark.parametrize("mutation", ["missing-update", "extra-query", "missing-validator"])
def test_sdk_contract_checks_detect_registration_violations(monkeypatch, mutation):
    """These mutations would evade a source-token check with stale decorators."""
    cls = MoonMindAgentRun
    definition = workflow._Definition.must_from_class(cls)
    if mutation == "missing-update":
        updates = dict(definition.updates)
        del updates["Pause"]
        changed = replace(definition, updates=updates)
        check = test_pause_resume_are_updates_not_signals_on_all_types
    elif mutation == "extra-query":
        user_definition = workflow._Definition.must_from_class(MoonMindUserWorkflow)
        query = user_definition.queries["control_state"]
        changed = replace(definition, queries={**definition.queries, "control_state": query})
        check = test_only_user_workflow_exposes_control_state_query
    else:
        updates = dict(definition.updates)
        updates["Pause"] = replace(updates["Pause"], validator=None)
        changed = replace(definition, updates=updates)
        check = test_pause_resume_validation_varies_by_workflow_type
    monkeypatch.setattr(cls, "__temporal_workflow_definition", changed)
    with pytest.raises(AssertionError):
        check()


@pytest.mark.parametrize(
    ("check", "missing_reference"),
    [
        (test_skill_operations_match_live_catalog_and_bindings, "agent_skill.resolve"),
        (
            test_workflow_fleet_helpers_match_registry_boundary,
            "integration.get_activity_route",
        ),
        (test_queue_inventory_matches_client_scope, "mm.workflow.merge_automation"),
    ],
)
def test_registry_document_checks_detect_omitted_references(
    monkeypatch, check, missing_reference
):
    original_read = _read
    original = original_read(TOPOLOGY)
    assert missing_reference in original
    changed = original.replace(missing_reference, "")

    def mutated_read(path: Path) -> str:
        if path == TOPOLOGY:
            return changed
        return original_read(path)

    monkeypatch.setattr(f"{__name__}._read", mutated_read)
    with pytest.raises(AssertionError):
        check()
