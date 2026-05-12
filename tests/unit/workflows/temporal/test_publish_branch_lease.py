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


def test_build_git_push_with_lease_args_uses_empty_expect_for_new_branch() -> None:
    args = build_git_push_with_lease_args(branch="feature/new")

    assert args == [
        "push",
        "-u",
        "--force-with-lease=refs/heads/feature/new:",
        "origin",
        "feature/new",
    ]


def test_classify_git_push_failure_returns_retryable_lease_conflict() -> None:
    result = classify_git_push_failure(
        stderr="! [rejected] feature -> feature (stale info)",
        branch="feature",
    )

    assert result["push_status"] == "lease_conflict"
    assert result["retryable"] is True
    assert result["diagnostic_kind"] == "publish_lease_conflict"


def test_classify_git_push_failure_does_not_treat_generic_rejection_as_lease() -> None:
    result = classify_git_push_failure(
        stderr="remote: protected branch update denied\n"
        "! [remote rejected] feature -> feature (protected branch hook declined)\n"
        "error: failed to push some refs",
        branch="feature",
    )

    assert result["push_status"] == "failed"
    assert result["push_branch"] == "feature"
