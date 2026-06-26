from __future__ import annotations

import pytest

from tests.integration.test_proposal_delivery_status_execution_detail import (
    _proposal_execution_record,
)

from api_service.api.routers.executions import _serialize_execution

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_execution_detail_payload_keeps_proposal_review_off_primary_queue() -> None:
    record = _proposal_execution_record()
    record.memo["proposals"]["externalLinks"].append(
        {
            "provider": "github",
            "externalKey": "99",
            "externalUrl": "https://github.example/Moon/Mind/issues/99",
            "promotionResult": {
                "promotedExecutionId": "mm-promoted-1",
                "promotedExecutionUrl": "/tasks/temporal/mm-promoted-1",
            },
        }
    )

    payload = _serialize_execution(record).model_dump(by_alias=True)
    serialized = repr(payload).lower()

    assert payload["detailHref"] == "/workflows/mm:proposal-contract"
    assert "/tasks/proposals" not in serialized
    assert "/api/proposals" not in serialized
    assert "/promote" not in serialized
    assert "/dismiss" not in serialized
    assert any(
        item.get("externalUrl") == "https://github.example/Moon/Mind/issues/99"
        for item in payload["proposalOutcomes"]
    )
