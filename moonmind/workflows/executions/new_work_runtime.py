"""One new-work runtime path for ordinary Codex work.

Source issue: MoonLadderStudios/MoonMind#3931.

Normal Codex work uses the shared Omnigent path through the existing
selection/admission owner
(:func:`moonmind.workflows.executions.runtime_target_selection.resolve_runtime_target_selection`).
This helper is the only new-work entrypoint for Workflow Create and
schedules: it resolves the authored-or-default runtime through that one
boundary and then enforces the two bounded admission authorities:

* the deployment-owned direct-retirement cutoff
  (``MOONMIND_CODEX_DIRECT_RETIRED_AT`` via
  :func:`moonmind.omnigent.codex_cutover_drain.assert_new_admission_allowed`),
  which closes the retired direct lane to new work while unset preserves it;
* the code-owned retirement class
  (:func:`moonmind.omnigent.cutover.assert_runtime_new_admission`), which
  stops admitting runtimes whose retirement class closed them.

The retired Codex phase/deployed-phase switches
(``MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE`` /
``MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE``) are never read here: defaults
promotion is owned by the versioned rollout policy, not by a phase gate.
Recorded authority (edits, reruns, continuations) stays in the shared
boundary; this helper only authors new work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class NewWorkAdmissionRejected(ValueError):
    """Raised when a new-work runtime is not selectable for new admission."""


def resolve_new_work_selection(
    *,
    surface: Any,
    workflow_settings: Any = None,
    authored_runtime: object = None,
    requested_target_id: str | None = None,
    policy: Any | None = None,
    env: Mapping[str, Any] | None = None,
):
    """Resolve one new-work selection through the single shared boundary.

    Returns the shared
    :class:`~moonmind.workflows.executions.runtime_target_selection.RuntimeTargetSelection`
    so callers can persist actual artifact provenance (target identity,
    policy version/generation, rollout state) without a phase gate or a
    SHA/digest compatibility fingerprint.
    """

    from moonmind.omnigent.codex_cutover_drain import (
        assert_new_admission_allowed,
    )
    from moonmind.omnigent.cutover import assert_runtime_new_admission
    from moonmind.workflows.executions.runtime_target_selection import (
        resolve_runtime_target_selection,
    )

    selection = resolve_runtime_target_selection(
        surface=surface,
        requested_runtime=authored_runtime,
        requested_target_id=requested_target_id,
        workflow_settings=workflow_settings,
        policy=policy,
        env=env,
    )
    if not selection.available:
        reason = (
            str(selection.reason_code)
            if selection.reason_code is not None
            else "unavailable"
        )
        raise NewWorkAdmissionRejected(
            f"Requested runtime target is not selectable for new work: "
            f"runtime={selection.runtime_id!r} "
            f"target={selection.target_id!r} reason={reason}"
        )
    try:
        assert_new_admission_allowed(selection.runtime_id, env=env)
        assert_runtime_new_admission(selection.runtime_id)
    except ValueError as exc:
        raise NewWorkAdmissionRejected(str(exc)) from exc
    return selection


def resolve_new_work_runtime(
    *,
    surface: Any,
    workflow_settings: Any = None,
    authored_runtime: object = None,
    requested_target_id: str | None = None,
    policy: Any | None = None,
    env: Mapping[str, Any] | None = None,
) -> str:
    """Resolve one new-work runtime id through the single shared boundary.

    ``authored_runtime`` preserves explicitly selected harness, Profile,
    credential, model/cost/privacy, source, and publication intent: an
    explicit selection is never rewritten into a different runtime. An
    unavailable or retired selection raises :class:`NewWorkAdmissionRejected`
    instead of silently falling back.
    """

    return resolve_new_work_selection(
        surface=surface,
        workflow_settings=workflow_settings,
        authored_runtime=authored_runtime,
        requested_target_id=requested_target_id,
        policy=policy,
        env=env,
    ).runtime_id


def new_work_evidence(selection: Any) -> dict[str, Any]:
    """Return passive provenance for one new-work selection.

    Records the actual target identity and policy version/generation. It
    never records a phase or a SHA/digest compatibility fingerprint, and
    unknown ownership stays unknown: a missing target yields ``None``
    rather than permission to terminate or delete history.
    """

    return {
        "runtimeId": selection.runtime_id,
        "targetId": selection.target_id,
        "policyVersion": selection.policy_version,
        "policyGeneration": selection.policy_generation,
        "rolloutState": (
            str(selection.rollout_state)
            if selection.rollout_state is not None
            else None
        ),
        "selectionSource": str(selection.source),
        "surface": str(selection.surface),
    }


__all__ = [
    "NewWorkAdmissionRejected",
    "new_work_evidence",
    "resolve_new_work_runtime",
    "resolve_new_work_selection",
]
