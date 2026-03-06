"""Unit tests for shared Jules status normalization."""

from __future__ import annotations

from moonmind.jules.status import normalize_jules_status


def test_normalize_jules_status_maps_success_aliases() -> None:
    snapshot = normalize_jules_status(" Completed ")

    assert snapshot.provider_status == "Completed"
    assert snapshot.provider_status_token == "completed"
    assert snapshot.normalized_status == "succeeded"
    assert snapshot.terminal is True
    assert snapshot.succeeded is True
    assert snapshot.failed is False
    assert snapshot.canceled is False


def test_normalize_jules_status_defaults_missing_values_to_pending() -> None:
    snapshot = normalize_jules_status(None)

    assert snapshot.provider_status == "pending"
    assert snapshot.provider_status_token == "pending"
    assert snapshot.normalized_status == "queued"
    assert snapshot.terminal is False


def test_normalize_jules_status_distinguishes_canceled_and_unknown() -> None:
    canceled = normalize_jules_status("cancelled")
    unknown = normalize_jules_status("awaiting_operator")

    assert canceled.normalized_status == "canceled"
    assert canceled.terminal is True
    assert canceled.canceled is True

    assert unknown.normalized_status == "unknown"
    assert unknown.terminal is False
