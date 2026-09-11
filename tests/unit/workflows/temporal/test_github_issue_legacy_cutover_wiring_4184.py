"""Legacy cutover wiring, emission-path, and replay evidence (#4184).

Proves the #4184 cutover decision layer is reachable through existing
tooling (not library-only), the new path provably stops writing obsolete
shapes, and retained-history classification runs over real
producer/consumer shapes:

* ``github_issue.assess_legacy`` / ``github_issue.plan_legacy_repair``
  activity + catalog + runtime bindings import and exercise the four
  cutover entrypoints (assess, repair plan, drainage, deployment);
* the real new-path emission set from ``story_output_tools`` and the
  lifecycle canonical labels neither emit nor require ``status: todo``
  and never write ``OBSOLETE_SHAPES``;
* retained-history replay uses a real ``AttemptHandoff`` render/extract
  round-trip at the live format version, not only synthetic dicts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle
from moonmind.workflows.temporal import github_issue_legacy_cutover as cutover
from moonmind.workflows.temporal.activities import (
    github_issue_legacy_cutover_activities as cutover_acts,
)
from moonmind.workflows.temporal.github_issue_attempt import (
    AttemptHandoff,
    render_attempt_comment,
)
from moonmind.workflows.temporal.story_output_tools import _GITHUB_STATUS_ACTIONS

REPO = "o/r"
ISSUE = 11
ATTEMPT_ID = "att_" + "b" * 24
TRUSTED = ["moonmind-bot"]


class _FakeCutoverService:
    """Minimal production-service double: bounded reads only, no writes."""

    trusted_posters = list(TRUSTED)

    def __init__(self, *, issue: dict[str, Any], comments: list[dict[str, Any]]) -> None:
        self._issue = issue
        self._comments = comments

    async def get_issue(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        assert repo == REPO
        assert issue_number == ISSUE
        return {"ok": True, "reasonCode": "read", "issue": dict(self._issue)}

    async def list_issue_comments(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        assert repo == REPO
        assert issue_number == ISSUE
        return {"ok": True, "comments": list(self._comments)}


def _real_handoff_body(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "attempt_id": ATTEMPT_ID,
        "deployment_id": "deploy-a",
        "repository": REPO,
        "issue_number": ISSUE,
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "activity": "active",
        "writers_stopped": False,
        "outcome": "pending",
        "next_action": "continue-implementation",
    }
    base.update(overrides)
    body, error = render_attempt_comment(AttemptHandoff(**base))
    assert not error, error
    return body


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_cutover_entrypoints_are_wired_through_existing_tooling() -> None:
    """All four entrypoints are reachable through the activity boundary."""
    assert cutover_acts.assess_legacy_cutover_issue is not None
    assert cutover_acts.plan_legacy_cutover_repair is not None
    assert cutover_acts.legacy_cutover_drainage_plan is not None
    assert cutover_acts.evaluate_legacy_cutover_deployment is not None
    # Drainage + deployment wrappers execute the pure cutover entrypoints.
    drainage = cutover_acts.legacy_cutover_drainage_plan(
        [{"family": "pending_finalization", "shape": "terminal_handoff", "version": "v1"}]
    )
    assert drainage["writesObsoleteShapes"] is False
    assert len(drainage["steps"]) == 1
    qualified = cutover_acts.evaluate_legacy_cutover_deployment(
        [{"installationId": "deploy-a", "codeVersion": "v2", "defaultsReconciled": True}]
    )
    assert qualified["qualified"] is True


def test_activity_catalog_and_runtime_bind_cutover_routes() -> None:
    from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
    from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS

    catalog = build_default_activity_catalog()
    for activity_type, handler in (
        ("github_issue.assess_legacy", "github_issue_assess_legacy"),
        ("github_issue.plan_legacy_repair", "github_issue_plan_legacy_repair"),
    ):
        route = catalog.resolve_activity(activity_type)
        assert route.task_queue
        assert route.fleet == "integrations"
        assert _ACTIVITY_HANDLER_ATTRS[activity_type] == ("integrations", handler)


def test_assess_then_plan_round_trip_through_activity_boundary() -> None:
    """Assess via fake GitHub reads, then plan a guarded repair from the report."""
    body = _real_handoff_body(writers_stopped=False)
    service = _FakeCutoverService(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[{"body": body, "user": {"login": "moonmind-bot"}}],
    )
    assessed = _run(
        cutover_acts.assess_legacy_cutover_issue(repository=REPO, issue_number=ISSUE, service=service)
    )
    assert assessed["ok"] is True
    assert assessed["settled"] == lifecycle.SETTLED_IN_PROGRESS
    assert assessed["complete"] is True
    assert assessed["findings"], "validated handoff must produce findings, not a clean report"
    planned = _run(
        cutover_acts.plan_legacy_cutover_repair(
            assessment=assessed,
            from_settled=lifecycle.SETTLED_IN_PROGRESS,
            to_target=lifecycle.TO_RECOVERY_NEEDED,
            evidence={"writers_stopped": True, "handoff_published": "handoff-1"},
            reason="preserve partial work",
            conclusively_stopped=True,
        )
    )
    assert planned["allowed"] is True
    assert planned["preserved"] and "human labels" in planned["preserved"]


def test_assess_is_read_only_and_reports_incomplete_evidence() -> None:
    """Unreadable comments defer explicitly; overflow is partial, never clean."""
    service = _FakeCutoverService(
        issue={"state": "open", "labels": []},
        comments=[{"body": f"note {i}", "user": {"login": "someone"}} for i in range(3)],
    )

    async def _overflow() -> dict[str, Any]:
        service_overflow = _FakeCutoverService(
            issue={"state": "open", "labels": []},
            comments=[
                {"body": f"note {i}", "user": {"login": "someone"}}
                for i in range(cutover.MAX_COMMENTS_ASSESSED + 5)
            ],
        )
        return await cutover_acts.assess_legacy_cutover_issue(
            repository=REPO, issue_number=ISSUE, service=service_overflow
        )

    assert _run(_overflow())["complete"] is False
    assessed = _run(
        cutover_acts.assess_legacy_cutover_issue(repository=REPO, issue_number=ISSUE, service=service)
    )
    assert assessed["ok"] is True
    # Plain notes carry no versioned handoff: absent metadata is incomplete
    # evidence, never proof of no work. The assessment stays complete (all
    # supplied evidence examined) but reports missing history for triage.
    assert assessed["complete"] is True
    assert any(f["finding"] == cutover.FINDING_MISSING_HISTORY for f in assessed["findings"])
    empty_service = _FakeCutoverService(issue={"state": "open", "labels": []}, comments=[])
    empty = _run(
        cutover_acts.assess_legacy_cutover_issue(
            repository=REPO, issue_number=ISSUE, service=empty_service
        )
    )
    assert empty["ok"] is True
    assert any(f["finding"] == cutover.FINDING_MISSING_HISTORY for f in empty["findings"])


def test_new_path_emission_set_is_todo_free_and_obsolete_free() -> None:
    """Real emission values from story_output_tools write no obsolete shapes."""
    emitted: list[str] = []
    for action in _GITHUB_STATUS_ACTIONS.values():
        emitted.extend(action.get("labelsToAdd") or [])
        emitted.extend(action.get("labelsToRemove") or [])
    assert emitted, "emission set must be non-empty to prove anything"
    for label in emitted:
        assert label.strip().lower() != lifecycle.LEGACY_TODO_LABEL
        assert not cutover.is_obsolete_shape(label), f"new path must not write {label!r}"
    for canonical in lifecycle.CANONICAL_OPEN_LABELS | {lifecycle.STATUS_DONE}:
        assert not cutover.is_obsolete_shape(canonical)
    verdict = cutover.verify_new_path_todo_free(emitted)
    assert verdict["todoFree"] is True
    assert verdict["survivingPolicyOwner"] == cutover.SURVIVING_POLICY_OWNER


def test_retained_history_replay_over_real_handoff_round_trip() -> None:
    """Classify a retained payload built from a real render/extract round-trip."""
    body = _real_handoff_body(writers_stopped=True)
    metadata, error = attempt.extract_attempt_metadata(body)
    assert error in (None, "")
    assert metadata is not None
    live_version = attempt.ATTEMPT_COMMENT_FORMAT_VERSION
    assert str(metadata.get("formatVersion")) == live_version
    retained = {
        "family": "activity_inputs",
        "shape": "attempt_handoff",
        "version": str(metadata.get("formatVersion")),
        "attemptId": str(metadata.get("attemptId")),
    }
    assert cutover.classify_retained_payload(retained)["disposition"] == "replay"
    legacy_retained = {"family": "tool_results", "shape": lifecycle.LEGACY_TODO_LABEL, "version": "v0"}
    assert cutover.classify_retained_payload(legacy_retained)["disposition"] == "drain"
    pending_plan = cutover_acts.legacy_cutover_drainage_plan([retained, legacy_retained])
    assert pending_plan["writesObsoleteShapes"] is False
    assert [step["classification"]["disposition"] for step in pending_plan["steps"]] == ["replay", "drain"]
