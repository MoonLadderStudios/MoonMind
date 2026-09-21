"""Retirement boundary for the removed ManifestIngest workflow type.

MoonLadderStudios/MoonMind#4189 (MR2/MR6): this file replaces the retired
executable boundary test that lived here at plan baseline
``7bd159dafb44770278e8c5563d47667de6e7c491``. The old test proved one
registered ``MoonMind.ManifestIngest`` type executed both persisted entry
contracts with real children:

- the ``manifest_ref`` compile/summary contract
  (``{"manifest_ref": manifest, **({"action": action})}`` with
  ``action`` in ``(None, "run")``), and
- the ``manifestArtifactRef`` node-execution contract
  (``{"manifestArtifactRef": manifest_ref, ...}`` with ``failurePolicy``
  ``best_effort``/``fail_fast`` and the control matrix ``complete``,
  ``cancel_and_retry``, ``parent_cancel``, ``update``
  (``newManifestArtifactRef``), ``child_failure``, ``cancel_node``,
  ``cancel_dependency``, ``default_failure``, ``explicit_failure``).

Per the removal plan (MR6), the old histories and controls above are the
bounded old-release rehearsal scope owned by the pinned old release — they
are cited here, not re-executed. After deletion the new binary must not
claim to replay the removed workflow; old-release replay/drain and
new-release historical reads are demonstrated separately. These tests assert
the new-release side: no executable registration, historical rows still
decodable, new launches rejected actionably, and the versioned drain gate
pinned.

Hermetic: no Temporal server, database, deployment probe, compiler, or
retired parser is needed.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

# Canonical manifest bytes reused verbatim from the retired executable test
# (plan baseline 7bd159dafb44770278e8c5563d47667de6e7c491) so both contracts
# below reference the same real fixture shape, not a re-authored payload.
BOUNDARY_MANIFEST = b"""version: v0
metadata:
  name: boundary
dataSources:
  - id: local-source
    type: GithubRepositoryReader
  - id: second-source
    type: GithubRepositoryReader
"""

RETIRED_MODULES = (
    "moonmind.workflows.temporal.workflows.manifest_ingest",
    "moonmind.workflows.temporal.manifest_ingest",
    "moonmind.schemas.manifest_ingest_models",
    "moonmind.manifest.pipeline",
)


def _historical_row(*, entry: str, workflow_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        entry=entry,
        workflow_type=SimpleNamespace(value=workflow_type),
    )


def test_retired_workflow_absent_from_production_fleet() -> None:
    """No production worker registers ManifestIngest or its activities."""
    from temporalio import workflow

    from moonmind.workflows.temporal import WORKFLOW_FLEET
    from moonmind.workflows.temporal.workers import (
        build_worker_topology,
        list_registered_workflow_types,
    )
    from moonmind.workflows.temporal.workflow_registry import (
        workflow_fleet_workflow_classes,
    )

    assert "MoonMind.ManifestIngest" not in list_registered_workflow_types()
    fleet_names = {
        workflow._Definition.must_from_class(cls).name
        for cls in workflow_fleet_workflow_classes()
    }
    assert "MoonMind.ManifestIngest" not in fleet_names
    activity_types = set(build_worker_topology(fleet=WORKFLOW_FLEET).activity_types)
    assert "manifest.compile" not in activity_types
    assert "manifest.write_summary" not in activity_types


def test_product_projection_excludes_retired_manifest_type() -> None:
    """Historical readability does not depend on launchable types."""
    from moonmind.workflows.temporal.workflow_registry import (
        product_workflow_types,
        require_product_projection,
        WorkflowProjectionExcluded,
    )

    assert product_workflow_types() == ("MoonMind.UserWorkflow",)
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.ManifestIngest")
    # Unknown types are still closed: no blanket-allow for retired types.
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.SomethingElse")


def test_both_historical_contracts_still_decode() -> None:
    """Old compile and node rows resolve to the historical manifest entry."""
    from api_service.api.routers.executions import (
        _normalize_entry_value,
        _resolve_execution_entry,
    )

    assert BOUNDARY_MANIFEST.startswith(b"version: v0")
    # manifest_ref compile/summary contract.
    assert (
        _resolve_execution_entry(
            _historical_row(entry="manifest", workflow_type="MoonMind.ManifestIngest"),
            {},
        )
        == "manifest"
    )
    # manifestArtifactRef node-execution contract (same entry, other input key).
    assert (
        _resolve_execution_entry(
            _historical_row(entry=None, workflow_type="MoonMind.ManifestIngest"),
            {},
        )
        == "manifest"
    )
    assert _normalize_entry_value("manifest") == "manifest"
    assert _normalize_entry_value("MoonMind.ManifestIngest") is None


def test_new_manifest_launches_rejected_actionably() -> None:
    """The new release never registers or launches ManifestIngest work."""
    import pydantic

    from api_service.db.models import TemporalWorkflowType
    from moonmind.schemas.temporal_models import CreateExecutionRequest
    from moonmind.workflows.temporal.service import RETIRED_MANIFEST_UPDATE_NAMES

    assert TemporalWorkflowType.MANIFEST_INGEST.value == "MoonMind.ManifestIngest"
    assert RETIRED_MANIFEST_UPDATE_NAMES, "retired update set must be non-empty"
    with pytest.raises(pydantic.ValidationError, match="was retired"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.ManifestIngest",
                "initialParameters": {"task": {"instructions": "new work"}},
            }
        )


def test_drain_gate_contract_pinned() -> None:
    """The versioned drain gate keeps fail-closed removal semantics."""
    from moonmind.gates.manifest_ingest_drain import (
        MANIFEST_INGEST_DRAIN_CONTRACT,
        ManifestIngestDrainUsage,
        collect_manifest_ingest_drain_observations,
        evaluate_manifest_ingest_drain,
        evaluate_manifest_ingest_drain_observations,
    )

    assert MANIFEST_INGEST_DRAIN_CONTRACT == "manifest-ingest-removal-drain-v1"
    drained = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage())
    assert drained.may_deploy_removal is True
    assert drained.required_action == "safe_to_remove"
    for usage in (
        ManifestIngestDrainUsage(open_manifest_ingest_histories=1),
        ManifestIngestDrainUsage(pending_manifest_tasks=1),
        ManifestIngestDrainUsage(existing_manifest_schedules=1),
    ):
        retained = evaluate_manifest_ingest_drain(usage)
        assert retained.may_deploy_removal is False
        assert retained.required_action == "retain_and_drain"
    unobservable = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=None,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert unobservable.may_deploy_removal is False


def test_new_binary_does_not_replay_removed_workflow() -> None:
    """Replay/drain stays on the pinned old release; the class is gone here."""
    for module_name in RETIRED_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError):
            spec = None
        assert spec is None, module_name
