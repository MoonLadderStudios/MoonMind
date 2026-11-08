"""Operator preview upgrade/retry journeys on the production database dialect."""

import pytest
import pytest_asyncio

from api_service.db.models import Base
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.api.routers import test_artifact_preview_upgrade as preview_cases

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

preview_api = preview_cases.preview_api


@pytest_asyncio.fixture
async def preview_sessions():
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        yield sessions


test_operator_repairs_retained_legacy_preview_without_raw_rights = (
    preview_cases.test_operator_repairs_retained_legacy_preview_without_raw_rights
)
test_concurrent_operator_requests_publish_one_preview = (
    preview_cases.test_concurrent_operator_requests_publish_one_preview
)
test_failed_repair_withholds_stale_bytes_then_retries = (
    preview_cases.test_failed_repair_withholds_stale_bytes_then_retries
)
test_lost_publication_response_reuses_committed_preview = (
    preview_cases.test_lost_publication_response_reuses_committed_preview
)
test_unavailable_source_is_not_repaired = (
    preview_cases.test_unavailable_source_is_not_repaired
)
test_upgrade_read_is_bounded = preview_cases.test_upgrade_read_is_bounded
