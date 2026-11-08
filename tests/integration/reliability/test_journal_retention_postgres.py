"""Journal compaction and expiry use exact paired refs on PostgreSQL."""

import pytest
import pytest_asyncio

from api_service.db.models import Base
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.omnigent import test_journal_retention as journal_cases

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

journals = journal_cases.journals


@pytest_asyncio.fixture
async def journal_sessions():
    # The production bridge and artifact owners touch their canonical records
    # and retention dependencies; isolate the same complete schema as SQLite.
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        yield sessions


test_compaction_preserves_live_history_for_legacy_readers_and_sweepers = (
    journal_cases.test_compaction_preserves_live_history_for_legacy_readers_and_sweepers
)
test_terminal_pending_chunks_retain_exact_bytes_without_new_raw_rights = (
    journal_cases.test_terminal_pending_chunks_retain_exact_bytes_without_new_raw_rights
)
test_terminal_journal_metadata_cannot_keep_expired_unrelated_bytes = (
    journal_cases.test_terminal_journal_metadata_cannot_keep_expired_unrelated_bytes
)

test_completed_chunks_of_live_journal_survive_troubleshooting_expiry = (
    journal_cases.test_completed_chunks_of_live_journal_survive_troubleshooting_expiry
)
