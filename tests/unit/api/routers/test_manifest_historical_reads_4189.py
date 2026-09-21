"""Historical Manifest reads stay safe under degraded lifecycle values.

MoonLadderStudios/MoonMind#4189 (MR2/MR6): after executable ManifestIngest
support is removed, old-release ``MoonMind.ManifestIngest`` rows must remain
readable through the generic list/detail serializers. The MR6 historical-safety
gate requires a workflow-boundary regression: an old execution carrying a
blank, unknown, or newly introduced ``mm_state``/lifecycle value through the
changed projection and status-normalization path must still produce a safe
read-only generic response rather than fail serialization or disappear.

These tests drive the production serializers directly (no Temporal server, no
database, no compiler/parser) with degraded ``state`` values a newer or older
release may have persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api_service.api.routers.executions import (
    _serialize_execution,
    _serialize_execution_list_item,
)
from api_service.db.models import TemporalWorkflowType
from moonmind.config.settings import settings


def _historical_manifest_record(*, state, mm_state="completed") -> SimpleNamespace:
    """Build an old-release ManifestIngest row with a degraded lifecycle value."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:historical-manifest:4189",
        run_id="run-historical-1",
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        state=state,
        close_status=None,
        search_attributes={
            "mm_owner_id": "user-123",
            "mm_owner_type": "user",
            "mm_entry": "manifest",
            "mm_state": mm_state,
            "mm_repo": "Moon/Mind",
        },
        memo={
            "title": "Historical manifest ingest",
            "summary": "Completed on the old release.",
        },
        artifact_refs=["art_historical_1"],
        finish_outcome_code=None,
        finish_summary_json=None,
        input_ref=None,
        plan_ref="art_plan_historical",
        manifest_ref="artifact://manifest/historical",
        parameters={
            "requestedBy": {"type": "user", "id": "user-1"},
            "manifestNodes": [{"nodeId": "node-a", "state": "succeeded"}],
        },
        paused=False,
        waiting_reason=None,
        attention_required=False,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=now,
        owner_id="user-123",
        owner_type="user",
        entry="manifest",
        integration_state=None,
    )


@pytest.mark.parametrize(
    "degraded_state",
    ["", "bogus_future_state", "some_new_lifecycle"],
    ids=["blank", "unknown", "newly_introduced"],
)
@pytest.mark.parametrize(
    "degraded_mm_state",
    ["", "unknown", "some_future_mm_state"],
    ids=["mm_blank", "mm_unknown", "mm_future"],
)
def test_historical_manifest_detail_read_tolerates_degraded_lifecycle(
    monkeypatch: pytest.MonkeyPatch, degraded_state: str, degraded_mm_state: str
) -> None:
    """Degraded lifecycle values still yield a safe read-only generic response."""
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    record = _historical_manifest_record(
        state=degraded_state, mm_state=degraded_mm_state
    )

    model = _serialize_execution(record)

    assert model.workflow_id == "mm:historical-manifest:4189"
    assert model.workflow_type == "MoonMind.ManifestIngest"
    assert model.entry == "manifest"
    assert model.dashboard_status == "queued"
    assert model.status == "queued"
    # Immutable historical identity survives without reinterpretation.
    assert model.run_id == "run-historical-1"
    # Raw lineage refs follow the current owner/raw-access policy: withheld
    # from non-admin readers, preserved for admins (asserted below).
    assert model.manifest_artifact_ref is None


def test_historical_manifest_lineage_refs_preserved_for_admin_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin readers still see the original input/plan lineage refs."""
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    admin = SimpleNamespace(is_superuser=True)
    record = _historical_manifest_record(state="bogus_future_state")

    model = _serialize_execution(record, user=admin)

    assert model.entry == "manifest"
    assert model.manifest_artifact_ref == "artifact://manifest/historical"
    assert model.plan_artifact_ref == "art_plan_historical"


@pytest.mark.parametrize(
    "degraded_state",
    ["", "bogus_future_state", "some_new_lifecycle"],
    ids=["blank", "unknown", "newly_introduced"],
)
def test_historical_manifest_list_read_tolerates_degraded_lifecycle(
    degraded_state: str,
) -> None:
    """The list projection degrades to a generic entry instead of disappearing."""
    record = _historical_manifest_record(state=degraded_state)

    item = _serialize_execution_list_item(record)

    assert item.workflow_id == "mm:historical-manifest:4189"
    assert item.entry == "manifest"
    assert item.dashboard_status == "queued"
    assert item.status == "queued"


def test_historical_manifest_degraded_state_disables_recreate_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded historical row never re-enables retired recreate affordances."""
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )
    record = _historical_manifest_record(state="bogus_future_state")

    actions = _serialize_execution(record).actions

    assert actions.can_rerun is False
    assert actions.can_edit_for_rerun is False
    assert actions.can_update_inputs is False
