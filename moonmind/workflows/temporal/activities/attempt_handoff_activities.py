"""Production Activity entrypoints for portable per-attempt issue handoffs.

Sibling runtime work (issue #4177 consumption contract) calls these Activities
at the admission, progress, and release boundaries instead of reimplementing
the portable format: every decision lives in
:mod:`moonmind.workflows.temporal.github_issue_attempt`, and this module only
supplies the production service object (``GitHubService``), the canonical
installation identity, and the caller-owned trusted-poster allow-list.

Credentials never come from the workflow payload: ``GitHubService`` resolves
its own token at the adapter boundary.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal.github_issue_attempt import AttemptHandoff


async def publish_attempt_progress(
    *,
    repository: str,
    issue_number: int,
    handoff: AttemptHandoff,
    trusted_posters: Sequence[str],
    last_publish_ts: float | None = None,
    now_ts: float | None = None,
    force: bool = False,
    prior_activity: str | None = None,
    prior_outcome: str | None = None,
    service: Any | None = None,
) -> dict[str, Any]:
    """Publish one progress update for the owning attempt.

    Resolves the canonical installation identity first so a misconfigured
    deployment fails before any GitHub read. ``service`` is injectable for
    tests; production passes ``None`` to use ``GitHubService``.
    """
    installation_id, identity_error = attempt.resolve_installation_id()
    if identity_error:
        return {
            "ok": False,
            "reasonCode": "installation_unconfigured",
            "summary": identity_error,
            "commentId": None,
        }
    if handoff.deployment_id != installation_id:
        return {
            "ok": False,
            "reasonCode": "issue_identity_mismatch",
            "summary": "Handoff deployment identity does not match this installation.",
            "commentId": None,
        }
    if service is None:
        from moonmind.workflows.adapters.github_service import GitHubService

        service = GitHubService()
    return await attempt.publish_attempt_handoff(
        service=service,
        repository=repository,
        issue_number=issue_number,
        handoff=handoff,
        last_publish_ts=last_publish_ts,
        now_ts=now_ts,
        force=force,
        trusted_posters=trusted_posters,
        prior_activity=prior_activity,
        prior_outcome=prior_outcome,
    )


async def publish_attempt_release(
    *,
    repository: str,
    issue_number: int,
    handoff: AttemptHandoff,
    trusted_posters: Sequence[str],
    release: Mapping[str, Any],
    service: Any | None = None,
) -> dict[str, Any]:
    """Publish the terminal handoff only after all four release confirmations.

    ``release`` carries the caller-observed ``writers_stopped``,
    ``mutations_settled``, ``preservation_verified_or_no_work``, and
    ``label_outcome_observed`` booleans. Anything less than all four stays
    ``releasing`` and is published as a proposed (non-terminal) disposition.
    """
    evaluation = attempt.evaluate_release(
        handoff,
        writers_stopped=bool(release.get("writers_stopped")),
        mutations_settled=bool(release.get("mutations_settled")),
        preservation_verified_or_no_work=bool(release.get("preservation_verified_or_no_work")),
        label_outcome_observed=bool(release.get("label_outcome_observed")),
    )
    if not evaluation["released"]:
        return {
            "ok": False,
            "reasonCode": "release_proposed",
            "summary": evaluation["summary"],
            "missing": evaluation["missing"],
            "commentId": None,
        }
    return await publish_attempt_progress(
        repository=repository,
        issue_number=issue_number,
        handoff=handoff,
        trusted_posters=trusted_posters,
        force=True,
        service=service,
    )


__all__ = ["publish_attempt_progress", "publish_attempt_release"]
