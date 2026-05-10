"""Tests for MM-680 lease-aware branch publishing helpers."""

from __future__ import annotations

from moonmind.workflows.temporal.activity_runtime import (
    build_git_push_with_lease_args,
    classify_git_push_failure,
)


def test_build_git_push_with_lease_args_pins_recorded_remote_sha() -> None:
    args = build_git_push_with_lease_args(
        branch="feature/mm-680",
        recorded_remote_sha="abc123",
    )

    assert args == [
        "push",
        "-u",
        "--force-with-lease=refs/heads/feature/mm-680:abc123",
        "origin",
        "feature/mm-680",
    ]


def test_classify_git_push_failure_returns_retryable_lease_conflict() -> None:
    result = classify_git_push_failure(
        stderr="! [rejected] feature -> feature (stale info)",
        branch="feature",
    )

    assert result["push_status"] == "lease_conflict"
    assert result["retryable"] is True
    assert result["diagnostic"]["reasonCode"] == "publish_lease_conflict"

