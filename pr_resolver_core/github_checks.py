"""Provider-independent reconciliation of GitHub check requirements and statuses."""

import re
from collections.abc import Mapping, Sequence
from typing import Any


def required_check_contexts(
    branch: Any, protection: Any, rules: Any
) -> list[str] | None:
    """Unknown policy is distinct from an observed unprotected branch."""
    if isinstance(branch, Mapping) and branch.get("protected") is False:
        return []
    if not isinstance(protection, Mapping) or not isinstance(rules, list):
        return None
    required = protection.get("required_status_checks") or {}
    if not isinstance(required, Mapping):
        return None
    contexts = required.get("contexts") or []
    checks = required.get("checks") or []
    if not isinstance(contexts, list) or not isinstance(checks, list):
        return None
    names = {str(value).strip() for value in contexts if str(value).strip()}
    names.update(
        str(check.get("context") or "").strip()
        for check in checks
        if isinstance(check, Mapping)
    )
    for rule in rules:
        if (
            not isinstance(rule, Mapping)
            or rule.get("type") != "required_status_checks"
        ):
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, Mapping) or not isinstance(
            parameters.get("required_status_checks"), list
        ):
            return None
        names.update(
            str(check.get("context") or "").strip()
            for check in parameters["required_status_checks"]
            if isinstance(check, Mapping)
        )
    return sorted(names - {""})


def partition_commit_statuses(
    statuses: Sequence[Mapping[str, Any]],
    required_contexts: list[str] | None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Retain latest statuses, gating every context when policy is unavailable.

    GitHub returns newest status observations first. Actions check-runs are
    always evaluated separately; an empty requirement list never disables CI.
    """
    latest = {}
    for status in statuses:
        context = str(status.get("context") or "").strip()
        if context:
            latest.setdefault(context, status)
    gating, advisory = [], []
    for context, status in latest.items():
        (
            gating
            if required_contexts is None or context in required_contexts
            else advisory
        ).append(status)
    return gating, advisory


def head_ci_reported(
    check_runs: Sequence[Mapping[str, Any]],
    gating_statuses: Sequence[Mapping[str, Any]],
    advisory_statuses: Sequence[Mapping[str, Any]],
    required_contexts: list[str] | None,
) -> bool:
    """Whether the exact head has reported any CI signal that can be judged.

    Nothing reported means CI has not been queued yet, or never runs for this
    base. Only an observed unprotected base that reported advisory status is a
    clean signal without gating checks. The merge gate and the pr-resolver
    Skill share this rule so one cannot reopen what the other calls degraded.
    """
    return (
        bool(check_runs)
        or bool(gating_statuses)
        or (required_contexts == [] and bool(advisory_statuses))
    )


# A job URL binds a check to a workflow run, but its numeric job ID does not
# establish a rerun attempt. The caller can enrich run_attempt from Actions jobs.
_ACTIONS_JOB_URL = re.compile(
    r"/actions/runs/(\d+)(?:/attempts/(\d+))?/job/(\d+)(?:[/?#]|$)"
)
_INCOMPLETE_CHECK_STATES = {"queued", "in_progress", "pending", "waiting", "requested"}
_PASSING_CHECK_STATES = {"success", "neutral", "skipped"}


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _workflow_identity(run: Mapping[str, Any]) -> tuple | None:
    workflow_id = _positive_integer(run.get("workflow_id"))
    path = run.get("path")
    if workflow_id is not None:
        return ("id", workflow_id)
    if isinstance(path, str) and path.strip():
        return ("path", path)
    return None


def _pull_request_context(run: Mapping[str, Any]) -> tuple | None:
    """Cross-run equivalence needs observed PR/base context, not just a head SHA."""
    pull_requests = run.get("pull_requests")
    if not isinstance(pull_requests, list):
        return None
    if not pull_requests and str(run.get("event") or "").startswith("pull_request"):
        return None
    identities = []
    for pull_request in pull_requests:
        if not isinstance(pull_request, Mapping):
            return None
        number = _positive_integer(pull_request.get("number"))
        base = pull_request.get("base")
        if number is None or not isinstance(base, Mapping):
            return None
        ref, sha = base.get("ref"), base.get("sha")
        if not all(isinstance(value, str) and value.strip() for value in (ref, sha)):
            return None
        repo = base.get("repo")
        repo_id = repo.get("id") if isinstance(repo, Mapping) else None
        identities.append((number, ref, sha, repo_id))
    return tuple(sorted(identities, key=lambda identity: identity[0]))


def reconcile_check_runs(
    check_runs: Sequence[Mapping[str, Any]],
    workflow_runs: Sequence[Mapping[str, Any]] | None,
    *,
    head_sha: str,
) -> tuple[list[Mapping[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Keep applicable checks, with auditable supersession and stranded evidence.

    Only same-head, same-workflow/event/app/name checks can supersede one
    another. Across runs, matching observed PR/base context is required and
    run_number orders them; within a run, per-job run_attempt evidence is required. Checks and workflow observations are
    never mutated. Unknown mappings remain applicable, and unresolvedChecks
    must degrade the caller's CI signal rather than grant a clean gate.

    Successful unique checks need no workflow lookup. Other providers, including
    security apps, always remain applicable. Empty input remains empty, so the
    caller must still apply the ordinary missing-CI/required-context gate.
    """
    evidence: dict[str, list[dict[str, Any]]] = {
        "supersededChecks": [],
        "strandedChecks": [],
        "unresolvedChecks": [],
    }
    # Conflicting observations of a run are not trustworthy supersession proof.
    runs: dict[int, Mapping[str, Any] | None] = {}
    for run in workflow_runs or []:
        run_id = _positive_integer(run.get("id"))
        if run_id is not None:
            if run_id in runs and runs[run_id] != run:
                runs[run_id] = None
            else:
                runs[run_id] = run

    mapped: dict[int, dict[str, Any]] = {}
    unresolved: dict[int, str] = {}
    actions: dict[int, tuple[Any, Any, Any]] = {}
    for index, check in enumerate(check_runs):
        app = check.get("app")
        app = app if isinstance(app, Mapping) else {}
        slug = app.get("slug")
        match = _ACTIONS_JOB_URL.search(str(check.get("details_url") or ""))
        if slug != "github-actions" and (slug or not match):
            continue
        name = check.get("name")
        app_id = _positive_integer(app.get("id"))
        actions[index] = (slug, app_id, name)
        run_id = int(match[1]) if match else None
        run = runs.get(run_id)
        if not match or app_id is None or slug != "github-actions" or not name:
            unresolved[index] = "check_identity_unavailable"
            continue
        if not run:
            unresolved[index] = "workflow_run_unavailable"
            continue
        if (
            not head_sha
            or check.get("head_sha") != head_sha
            or run.get("head_sha") != head_sha
        ):
            unresolved[index] = "check_head_unverified"
            continue
        workflow = _workflow_identity(run)
        run_number = _positive_integer(run.get("run_number"))
        if workflow is None or not run.get("event") or run_number is None:
            unresolved[index] = "workflow_identity_unavailable"
            continue
        run_attempt = _positive_integer(run.get("run_attempt"))
        explicit_attempt = check.get("run_attempt")
        url_attempt = int(match[2]) if match[2] else None
        attempt = (
            _positive_integer(explicit_attempt)
            if explicit_attempt is not None
            else url_attempt
        )
        if (
            (explicit_attempt is not None and attempt is None)
            or (url_attempt is not None and attempt != url_attempt)
            or (attempt is not None and (run_attempt is None or attempt > run_attempt))
        ):
            unresolved[index] = "check_attempt_unverified"
            continue
        if attempt is None and run_attempt == 1:
            attempt = 1
        mapped[index] = {
            "run": run,
            "runId": run_id,
            "runNumber": run_number,
            "runAttempt": attempt,
            "pullRequestContext": _pull_request_context(run),
            "identity": (head_sha, workflow, run["event"], slug, app_id, name),
        }

    def later(candidate: dict[str, Any], old: dict[str, Any]) -> bool:
        if candidate["identity"] != old["identity"]:
            return False
        for field in ("path", "head_branch"):
            candidate_value, old_value = candidate["run"].get(field), old["run"].get(
                field
            )
            if candidate_value and old_value and candidate_value != old_value:
                return False
        if candidate["runId"] != old["runId"]:
            return (
                candidate["pullRequestContext"] is not None
                and candidate["pullRequestContext"] == old["pullRequestContext"]
                and candidate["runNumber"] > old["runNumber"]
            )
        return (
            candidate["runAttempt"] is not None
            and old["runAttempt"] is not None
            and candidate["runAttempt"] > old["runAttempt"]
        )

    applicable = []
    for index, check in enumerate(check_runs):
        current = mapped.get(index)
        detail = {"checkId": check.get("id"), "name": check.get("name")}
        if current:
            detail.update(runId=current["runId"], runAttempt=current["runAttempt"])
            replacements = [
                (other, candidate)
                for other, candidate in mapped.items()
                if later(candidate, current)
            ]
            if replacements:
                other, replacement = max(
                    replacements,
                    key=lambda item: (item[1]["runNumber"], item[1]["runAttempt"] or 0),
                )
                evidence["supersededChecks"].append(
                    {
                        **detail,
                        "supersededByCheckId": check_runs[other].get("id"),
                        "supersededByRunId": replacement["runId"],
                        "supersededByRunAttempt": replacement["runAttempt"],
                        "reason": (
                            "newer_run_attempt"
                            if current["runId"] == replacement["runId"]
                            else "newer_workflow_run"
                        ),
                    }
                )
                continue
        applicable.append(check)
        if index not in actions:
            continue
        state = str(check.get("conclusion") or check.get("status") or "").lower()
        duplicated = (
            sum(identity == actions[index] for identity in actions.values()) > 1
        )
        if current:
            run = current["run"]
            if (
                state in _INCOMPLETE_CHECK_STATES
                and str(run.get("status") or "").lower() == "completed"
            ):
                evidence["strandedChecks"].append(
                    {
                        **detail,
                        "workflowStatus": "completed",
                        "workflowConclusion": run.get("conclusion"),
                        "reason": "terminal_workflow_with_incomplete_check",
                    }
                )
            if any(
                other != index
                and candidate["identity"] == current["identity"]
                and candidate["runId"] == current["runId"]
                and (candidate["runAttempt"] is None or current["runAttempt"] is None)
                for other, candidate in mapped.items()
            ):
                unresolved[index] = "check_attempt_unverified"
            if any(
                candidate["identity"] == current["identity"]
                and candidate["runId"] != current["runId"]
                and (
                    candidate["pullRequestContext"] is None
                    or current["pullRequestContext"] is None
                )
                for candidate in mapped.values()
            ):
                unresolved[index] = "workflow_pr_context_unverified"
        if index in unresolved and (state not in _PASSING_CHECK_STATES or duplicated):
            evidence["unresolvedChecks"].append({**detail, "reason": unresolved[index]})
    return applicable, evidence
