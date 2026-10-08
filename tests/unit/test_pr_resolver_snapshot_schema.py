"""Validate portable review evidence against the published snapshot contract."""

from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

SKILL_ROOT = Path(__file__).resolve().parents[2] / ".agents/skills/pr-resolver"
HEAD = "a" * 40
PROVIDER_LOGIN = "chatgpt-codex-connector[bot]"


@pytest.fixture(scope="module")
def validator() -> Draft7Validator:
    schema = json.loads(
        (SKILL_ROOT / "schemas/pr_resolver_snapshot.schema.json").read_text()
    )
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture
def snapshot_factory(monkeypatch):
    module = runpy.run_path(str(SKILL_ROOT / "bin/pr_resolve_snapshot.py"))

    def unexpected_command(*args, **kwargs):
        pytest.fail("Schema tests must use supplied evidence without running commands")

    build = module["build_automated_review_evidence"]
    for name in (
        "run_command",
        "run_command_optional",
        "run_command_optional_with_error",
    ):
        monkeypatch.setitem(build.__globals__, name, unexpected_command)

    def build_snapshot(state: str = "refused") -> dict[str, Any]:
        comments = [
            {
                "id": 100,
                "type": "issue_comment",
                "user": "operator",
                "body": "@codex review",
                "created_at": "2026-10-06T21:02:09Z",
            }
        ]
        reviews = []
        reactions = []
        if state in {"refused", "clean_reply"}:
            comments.append(
                {
                    "id": 101,
                    "type": "issue_comment",
                    "user": PROVIDER_LOGIN,
                    "body": (
                        "You have reached your Codex usage limits for code reviews."
                        if state == "refused"
                        else "Codex Review: Didn't find any major issues. 🚀"
                    ),
                    "created_at": "2026-10-06T21:02:18Z",
                }
            )
        elif state == "submitted_review":
            reviews.append(
                {
                    "id": 102,
                    "commit_id": HEAD,
                    "submitted_at": "2026-10-06T21:02:18Z",
                    "state": "COMMENTED",
                    "user": {"login": PROVIDER_LOGIN},
                }
            )
        elif state == "clean_reaction":
            reactions.append(
                {
                    "id": 103,
                    "content": "+1",
                    "created_at": "2026-10-06T21:02:18Z",
                    "user": {"login": PROVIDER_LOGIN},
                }
            )
        evidence = build(
            provider="none" if state == "disabled" else "codex",
            require_fresh_review=True,
            pr_repo="owner/repo",
            pr_number=350,
            head_sha=HEAD,
            comments=comments,
            reviews=reviews,
            head_committed_at=datetime(2026, 10, 6, 21, tzinfo=UTC),
            reactions_for_request=reactions,
            reactions_for_pr=[],
        )
        return {
            "pr": {"number": 350, "headRefOid": HEAD},
            "ci": module["summarize_ci_checks"]([{"name": "unit", "state": "SUCCESS"}]),
            "commentsFetch": {"succeeded": True, "source": "test_inventory"},
            "comments": comments,
            "commentsSummary": module["summarize_comments"](
                comments, head_commit_sha=HEAD
            ),
            "automatedReview": evidence,
        }

    return build_snapshot


@pytest.mark.parametrize(
    "state",
    [
        "disabled",
        "pending",
        "submitted_review",
        "clean_reply",
        "clean_reaction",
        "refused",
    ],
)
def test_produced_review_evidence_validates(validator, snapshot_factory, state) -> None:
    snapshot = snapshot_factory(state)
    review = snapshot["automatedReview"]
    assert review["enabled"] is (state != "disabled")
    if state != "disabled":
        assert review["requestPending"] is (state == "pending")
        assert review["requestFailed"] is (state == "refused")
        assert review["freshReviewForHead"] is state.startswith(
            ("clean_", "submitted_")
        )
        assert (review["requestFailure"] is not None) is (state == "refused")
    validator.validate(snapshot)


@pytest.mark.parametrize("state", ["pending", "clean_reply"])
def test_legacy_review_evidence_without_failure_fields_validates(
    validator, snapshot_factory, state
) -> None:
    snapshot = snapshot_factory(state)
    snapshot["automatedReview"].pop("requestFailed")
    snapshot["automatedReview"].pop("requestFailure")
    validator.validate(snapshot)


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_request_failed_rejects_non_booleans(
    validator, snapshot_factory, value
) -> None:
    snapshot = snapshot_factory()
    snapshot["automatedReview"]["requestFailed"] = value
    errors = list(validator.iter_errors(snapshot))
    assert any(
        list(error.path) == ["automatedReview", "requestFailed"] for error in errors
    )


@pytest.mark.parametrize("value", ["rate_limit", True, 1, []])
def test_request_failure_rejects_non_object_evidence(
    validator, snapshot_factory, value
) -> None:
    snapshot = snapshot_factory()
    snapshot["automatedReview"]["requestFailure"] = value
    errors = list(validator.iter_errors(snapshot))
    assert any(
        list(error.path) == ["automatedReview", "requestFailure"] for error in errors
    )


@pytest.mark.parametrize("field", ["kind", "id", "failedAt", "providerErrorClass"])
def test_request_failure_requires_its_evidence_fields(
    validator, snapshot_factory, field
) -> None:
    snapshot = snapshot_factory()
    del snapshot["automatedReview"]["requestFailure"][field]
    errors = list(validator.iter_errors(snapshot))
    assert any(error.validator == "required" for error in errors)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "review"),
        ("kind", None),
        ("id", "101"),
        ("id", True),
        ("failedAt", 123),
        ("failedAt", None),
        ("failedAt", ""),
        ("providerErrorClass", []),
        ("providerErrorClass", None),
        ("providerErrorClass", ""),
    ],
)
def test_request_failure_rejects_malformed_evidence_fields(
    validator, snapshot_factory, field, value
) -> None:
    snapshot = snapshot_factory()
    snapshot["automatedReview"]["requestFailure"][field] = value
    errors = list(validator.iter_errors(snapshot))
    assert any(
        list(error.path) == ["automatedReview", "requestFailure", field]
        for error in errors
    )


def test_request_failure_allows_unavailable_comment_id(
    validator, snapshot_factory
) -> None:
    snapshot = snapshot_factory()
    snapshot["automatedReview"]["requestFailure"]["id"] = None
    validator.validate(snapshot)
