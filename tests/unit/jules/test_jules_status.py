"""Unit tests for JulesStatusSnapshot and normalize_jules_status in moonmind.jules.status."""

from __future__ import annotations

import pytest

from moonmind.jules.status import (
    JulesStatusSnapshot,
    normalize_jules_status,
)


class TestNormalizeJulesStatus:
    """Verify every documented status category maps correctly."""

    @pytest.mark.parametrize(
        "raw_status,expected_normalized",
        [
            ("completed", "succeeded"),
            ("succeeded", "succeeded"),
            ("success", "succeeded"),
            ("done", "succeeded"),
            ("resolved", "succeeded"),
            ("finished", "succeeded"),
        ],
    )
    def test_success_statuses(self, raw_status: str, expected_normalized: str) -> None:
        snapshot = normalize_jules_status(raw_status)
        assert snapshot.normalized_status == expected_normalized
        assert snapshot.succeeded is True
        assert snapshot.terminal is True
        assert snapshot.failed is False

    @pytest.mark.parametrize(
        "raw_status,expected_normalized",
        [
            ("error", "failed"),
            ("failed", "failed"),
            ("rejected", "failed"),
            ("timed_out", "failed"),
            ("timeout", "failed"),
        ],
    )
    def test_failure_statuses(self, raw_status: str, expected_normalized: str) -> None:
        snapshot = normalize_jules_status(raw_status)
        assert snapshot.normalized_status == expected_normalized
        assert snapshot.failed is True
        assert snapshot.terminal is True
        assert snapshot.succeeded is False

    @pytest.mark.parametrize("raw_status", ["cancelled", "canceled"])
    def test_canceled_statuses(self, raw_status: str) -> None:
        snapshot = normalize_jules_status(raw_status)
        assert snapshot.normalized_status == "canceled"
        assert snapshot.canceled is True
        assert snapshot.terminal is True

    @pytest.mark.parametrize("raw_status", ["pending", "queued"])
    def test_queued_statuses(self, raw_status: str) -> None:
        snapshot = normalize_jules_status(raw_status)
        assert snapshot.normalized_status == "queued"
        assert snapshot.terminal is False

    @pytest.mark.parametrize(
        "raw_status", ["running", "in_progress", "in-progress", "processing"]
    )
    def test_running_statuses(self, raw_status: str) -> None:
        snapshot = normalize_jules_status(raw_status)
        assert snapshot.normalized_status == "running"
        assert snapshot.terminal is False

    @pytest.mark.parametrize("raw_status", [None, "", "  ", "mystery", "UNEXPECTED"])
    def test_unknown_statuses(self, raw_status: str | None) -> None:
        snapshot = normalize_jules_status(raw_status)
        # None and blank both default to "pending" → queued
        if raw_status is None or not str(raw_status or "").strip():
            assert snapshot.normalized_status == "queued"
        else:
            assert snapshot.normalized_status == "unknown"
        assert snapshot.terminal is False

    def test_snapshot_has_all_expected_fields(self) -> None:
        snapshot = normalize_jules_status("completed")
        assert isinstance(snapshot, JulesStatusSnapshot)
        assert snapshot.provider_status == "completed"
        assert snapshot.provider_status_token == "completed"
        assert snapshot.normalized_status == "succeeded"
        assert snapshot.terminal is True
        assert snapshot.succeeded is True
        assert snapshot.failed is False
        assert snapshot.canceled is False

    def test_case_insensitive(self) -> None:
        snapshot = normalize_jules_status("COMPLETED")
        assert snapshot.normalized_status == "succeeded"

    def test_whitespace_handling(self) -> None:
        snapshot = normalize_jules_status("  completed  ")
        assert snapshot.normalized_status == "succeeded"
        assert snapshot.provider_status == "completed"
