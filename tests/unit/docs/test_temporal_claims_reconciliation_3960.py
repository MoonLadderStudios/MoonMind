"""Semantic/boundary verification for issue #3960.

Reconciles Temporal documentation claims with actual handlers, routing, and
verified behavior. Unlike exact-prose assertions, these tests derive
expectations from the production code boundary (handler definitions, the
activity catalog, the fleet registry, task-queue constants) and then require
the reader-facing docs to agree with that derived state.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs" / "Temporal"
TYPE_CATALOG = DOCS / "WorkflowTypeCatalogAndLifecycle.md"
PAUSE_SYSTEM = DOCS / "WorkerPauseSystem.md"
TOPOLOGY = DOCS / "ActivityCatalogAndWorkerTopology.md"
ARCHITECTURE = DOCS / "TemporalArchitecture.md"
SIGNALS = DOCS / "TemporalSignalsSystem.md"

RUN_WF = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "workflows" / "run.py"
AGENT_RUN_WF = (
    REPO_ROOT / "moonmind" / "workflows" / "temporal" / "workflows" / "agent_run.py"
)
MANIFEST_WF = (
    REPO_ROOT
    / "moonmind"
    / "workflows"
    / "temporal"
    / "workflows"
    / "manifest_ingest.py"
)
CLIENT = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "client.py"
CATALOG = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "activity_catalog.py"
RUNTIME = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "activity_runtime.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _update_names(source: str) -> set[str]:
    """Collect @workflow.update(name=...) handler names from workflow source."""
    names: set[str] = set()
    for match in re.finditer(
        r"@workflow\.update\(\s*name\s*=\s*[\"']([^\"']+)[\"']", source
    ):
        names.add(match.group(1))
    return names


def _has_query(source: str, name: str) -> bool:
    return f'@workflow.query(name="{name}")' in source or (
        f"@workflow.query(name='{name}')" in source
    )


def _normalized(source: str) -> str:
    """Collapse whitespace so assertions are not line-break sensitive."""
    return re.sub(r"\s+", " ", source)


def test_pause_resume_are_updates_not_signals_on_all_types() -> None:
    for path in (RUN_WF, AGENT_RUN_WF, MANIFEST_WF):
        names = _update_names(_read(path))
        assert "Pause" in names, f"{path.name} must define a Pause Update"
        assert "Resume" in names, f"{path.name} must define a Resume Update"

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
    assert _has_query(_read(RUN_WF), "control_state")
    assert not _has_query(_read(AGENT_RUN_WF), "control_state")
    assert not _has_query(_read(MANIFEST_WF), "control_state")

    doc = _normalized(_read(TYPE_CATALOG))
    assert "control_state" in doc
    assert "Accepted" in doc  # acceptance vs safe-point distinction present


def test_pause_resume_validation_varies_by_workflow_type() -> None:
    manifest_src = _read(MANIFEST_WF)
    assert "@pause.validator" not in manifest_src
    assert "@resume.validator" not in manifest_src
    agent_src = _read(AGENT_RUN_WF)
    assert "@pause.validator" in agent_src
    assert "@resume.validator" in agent_src

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


def _assert_skill_catalog_reference(doc: str) -> set[str]:
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    expected = {
        entry.activity_type
        for entry in build_default_activity_catalog().activities
        if entry.activity_type.startswith("agent_skill.")
    }
    assert expected
    documented = set(re.findall(r"`(agent_skill\.[a-z_]+)`", doc))
    assert documented == expected
    return expected


def test_skill_operations_match_live_catalog_and_bindings() -> None:
    doc = _normalized(_read(TOPOLOGY))
    expected = _assert_skill_catalog_reference(doc)
    catalog_src = _read(CATALOG)
    for op in expected:
        assert f'activity_type="{op}"' in catalog_src
    runtime_src = _read(RUNTIME)
    for op in expected:
        assert f'"{op}"' in runtime_src

    for op in expected:
        assert op in doc, f"docs must list actually registered operation {op}"
    assert "not yet a core live catalog family" not in doc
    # Executable tool contracts vs portable instruction bundles stay distinct.
    assert "must not reimplement Skill semantics" in doc


@pytest.mark.parametrize("mutation", ["missing", "invented"])
def test_skill_registry_reference_drift_is_detected(mutation) -> None:
    doc = _read(TOPOLOGY)
    _assert_skill_catalog_reference(doc)
    if mutation == "missing":
        doc = doc.replace("agent_skill.resolve", "obsolete.resolve")
    else:
        doc += "\n`agent_skill.invented`\n"
    with pytest.raises(AssertionError):
        _assert_skill_catalog_reference(doc)


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
    assert len(handlers) == 7
    handler_names = {h.__name__ for h in handlers}
    assert handler_names == {
        "resolve_adapter_metadata",
        "get_activity_route",
        "resolve_external_adapter",
        "external_adapter_execution_style",
        "mark_checkpoint_branch_turn_running",
        "persist_checkpoint_branch_turn_terminal",
        "persist_checkpoint_branch_turn_terminal_rejection",
    }

    for doc_path in (TOPOLOGY, ARCHITECTURE):
        doc = _normalized(_read(doc_path))
        for activity_type in (
            "integration.resolve_adapter_metadata",
            "integration.get_activity_route",
            "integration.resolve_external_adapter",
            "integration.external_adapter_execution_style",
            "checkpoint_branch.turn.mark_running",
            "checkpoint_branch.turn.persist_terminal",
            "checkpoint_branch.turn.persist_terminal_rejection",
        ):
            assert activity_type in doc, f"{doc_path.name} must list {activity_type}"
        assert "#3949" in doc


def test_queue_inventory_matches_client_scope() -> None:
    tree = ast.parse(_read(CLIENT))
    queues: tuple[str, ...] = ()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "_MOONMIND_TASK_QUEUES"
        ):
            queues = tuple(e.value for e in node.value.elts)
    assert queues, "must derive the queue tuple from client.py"
    assert "mm.workflow.merge_automation" in queues

    catalog_src = _read(CATALOG)
    assert "def get_workflow_poll_task_queues" in catalog_src
    settings_src = (REPO_ROOT / "moonmind" / "config" / "settings.py").read_text(
        encoding="utf-8"
    )
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
            [
                _read(TYPE_CATALOG),
                _read(PAUSE_SYSTEM),
                _read(TOPOLOGY),
                _read(ARCHITECTURE),
            ]
        )
    )
    for ref in ("#3959", "#3953", "#3949"):
        assert ref in combined, f"docs must cross-link {ref}"
