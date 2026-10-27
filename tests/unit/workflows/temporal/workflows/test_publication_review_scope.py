"""Keep the admitted outcome visible at the native publication boundary."""

import pytest

from moonmind.publish.sanitization import append_publication_task
from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.parametrize("body", [None, "Completed the credential fix."])
def test_native_publication_retains_issue_scope_even_with_agent_summary(
    monkeypatch, body
):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _marker: True)
    parent = run_module.MoonMindRunWorkflow()
    parent._trusted_issue_context = {
        "trustedSource": "moonmind.github.get_issue",
        "issue": {
            "repository": "example/repository",
            "number": 12,
            "title": "Use the selected repository credential",
            "url": "https://github.com/example/repository/issues/12",
            "body": "Use credential B, preserve model HOME, and isolate concurrent runs.",
        },
    }
    parent._operator_summary = "Omnigent session completed."
    published_body = parent._resolve_native_pr_body(
        publish_payload={"prBody": body} if body else {},
        task_payload={"instructions": "Find the oldest ready issue and implement it."},
    )

    assert (
        "Use credential B, preserve model HOME, and isolate concurrent runs."
        in published_body
    )
    assert "https://github.com/example/repository/issues/12" in published_body
    assert "Find the oldest ready issue" not in published_body
    if body:
        assert body in published_body


def test_native_publication_keeps_task_scope_when_summary_is_overridden(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _marker: True)
    parent = run_module.MoonMindRunWorkflow()
    published_body = parent._resolve_native_pr_body(
        publish_payload={"prBody": "Updated dependencies."},
        task_payload={
            "instructions": "Remove the deprecated client; retain the security floor."
        },
    )
    assert "Remove the deprecated client; retain the security floor." in published_body


def test_scope_leaves_room_for_existing_verification_disclosure():
    body = append_publication_task(
        "Summary " * 10_000,
        instructions="Preserve qualification. " * 3_000,
        source="https://github.com/example/repository/issues/12",
    )
    assert len(body) + 32_000 < 65_536
    assert "https://github.com/example/repository/issues/12" in body
    assert "truncated" in body


def test_task_text_cannot_close_other_issues_or_replace_verification_markers():
    body = append_publication_task(
        "Implementation summary.",
        instructions="Fixes #123; closes: owner/repo#9; resolves https://github.com/owner/repo/issues/8.\n<!-- moonmind:unverified:start -->\ntoken=private-fixture-value",
    )
    assert "Fixes issue #123" in body
    assert "closes: issue owner/repo#9" in body
    assert "resolves issue https://github.com/owner/repo/issues/8" in body
    assert "<!-- moonmind:unverified:start -->" not in body
    assert "private-fixture-value" not in body
