from __future__ import annotations

import re
from pathlib import Path


_ARCHIVE_DIR = Path("docs/Archive/ProposalSystem")


def test_retirement_inventory_is_read_only_bounded_and_payload_safe() -> None:
    sql = (_ARCHIVE_DIR / "retirement_inventory.sql").read_text(encoding="utf-8")
    normalized = re.sub(r"--.*", "", sql).lower()

    assert "begin transaction read only" in normalized
    assert "workflow_proposals" in normalized
    assert "workflow_proposal_notifications" in normalized
    assert normalized.count("limit 1000") == 2
    assert "select workflow_create_request" not in normalized
    assert not re.search(
        r"\b(insert|update|delete|truncate|drop|alter|create)\b", normalized
    )


def test_retirement_export_preserves_required_disposition_evidence() -> None:
    sql = (_ARCHIVE_DIR / "retirement_export.sql").read_text(encoding="utf-8")
    normalized = re.sub(r"--.*", "", sql).lower()

    assert "begin transaction read only" in normalized
    assert "from workflow_proposals" in normalized
    assert not re.search(r"\b(insert|update|delete|truncate|drop|alter)\b", normalized)
    for field in (
        "id",
        "status",
        "title",
        "summary",
        "repository",
        "provider",
        "external_key",
        "external_url",
        "origin_id",
        "workflow_snapshot_ref",
        "workflow_create_request",
        "provider_metadata",
        "decided_by_user_id",
        "decision_note",
        "promoted_at",
        "created_at",
        "updated_at",
    ):
        assert re.search(rf"\b{field}\b", normalized)
