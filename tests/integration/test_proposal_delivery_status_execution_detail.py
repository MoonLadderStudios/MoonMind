from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api_service.api.routers.executions import _serialize_execution
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _proposal_execution_record() -> SimpleNamespace:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:proposal-contract",
        run_id="run-proposal-contract",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.PROPOSALS,
        close_status=None,
        search_attributes={
            "mm_owner_id": "user-1",
            "mm_owner_type": "user",
            "mm_entry": "run",
            "mm_repo": "MoonLadderStudios/MoonMind",
        },
        memo={
            "title": "Proposal diagnostics",
            "summary": "Proposal delivery is in progress.",
            "proposals": {
                "requested": True,
                "generatedCount": 3,
                "submittedCount": 3,
                "deliveredCount": 1,
                "validationErrors": [
                    {
                        "code": "proposal_validation_error",
                        "message": "proposal skipped: [REDACTED]",
                    }
                ],
                "deliveryFailures": [
                    {
                        "provider": "jira",
                        "externalKey": "MM-902",
                        "code": "delivery_failed",
                        "message": "delivery failed: [REDACTED]",
                    }
                ],
                "externalLinks": [
                    {
                        "provider": "jira",
                        "externalKey": "MM-901",
                        "externalUrl": "https://jira.example/browse/MM-901",
                    }
                ],
                "dedupUpdates": [
                    {
                        "provider": "github",
                        "externalKey": "42",
                        "created": False,
                        "duplicateSource": "existing-open-issue",
                    }
                ],
            },
        },
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        parameters={},
        paused=False,
        waiting_reason=None,
        attention_required=False,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id="user-1",
        owner_type="user",
        entry="run",
        integration_state=None,
    )


def test_execution_detail_contract_exposes_proposal_delivery_diagnostics() -> None:
    payload = _serialize_execution(_proposal_execution_record()).model_dump(
        by_alias=True
    )

    assert payload["state"] == "proposals"
    assert payload["rawState"] == "proposals"
    assert payload["dashboardStatus"] == "running"
    assert payload["proposalSummary"]["generatedCount"] == 3
    assert payload["proposalSummary"]["deliveryFailures"][0]["externalKey"] == "MM-902"

    outcomes = {
        item.get("externalKey"): item for item in payload["proposalOutcomes"]
    }
    assert outcomes["MM-901"]["provider"] == "jira"
    assert outcomes["MM-901"]["deliveryStatus"] == "delivered"
    assert outcomes["42"]["deliveryStatus"] == "updated"
    assert outcomes["42"]["created"] is False
    assert outcomes["MM-902"]["deliveryStatus"] == "failed"
    assert outcomes["MM-902"]["message"] == "delivery failed: [REDACTED]"
    assert "ghp_secret" not in repr(payload)
