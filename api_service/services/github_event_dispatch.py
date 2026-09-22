"""Default Temporal dispatch for opt-in GitHub event deliveries (#3967).

Single dispatch identity for the event trigger path: an admitted delivery
expands the operator-bound preset through the existing preset catalog, then
launches through the existing :class:`TemporalExecutionService` admission
boundary (pause guard, worker freshness, Skill input validation, provider
profile runtime, plan source) with the stable delivery identity key as the
Temporal idempotency key, so a redelivery that re-attempts a lost start
reconciles to the same logical execution instead of minting a second one.

Trigger-configured execution limits are translated into canonical launch
controls before admission; anything without a canonical control fails
closed. ``publication_intent="none"`` (the first-slice default) strips
preset-declared publication payloads so an unattended trigger never
publishes; any other intent is rejected as unsupported.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.executions.preset_expansion import (
    expand_preset_for_child_run,
)
from moonmind.workflows.temporal.service import TemporalExecutionService

#: Trigger ``execution_limits`` keys with a canonical launch control.
#: Anything else fails closed: silently ignoring a spend control would
#: discard billing authority.
_SUPPORTED_EXECUTION_LIMITS = ("maxModelBudgetUsd",)


class EventExecutionDispatcher(Protocol):
    """Dispatch one admitted event delivery; return the execution reference."""

    async def dispatch(
        self,
        *,
        preset_slug: str,
        identity_key: str,
        repository: str,
        issue_number: int,
        title: str,
        parameters: dict[str, Any],
        execution_limits: dict[str, Any] | None = None,
        publication_intent: str = "none",
    ) -> str:
        """Protocol stub; implementations dispatch the admitted delivery."""
        raise NotImplementedError


def apply_event_execution_limits(
    initial_parameters: dict[str, Any],
    execution_limits: dict[str, Any] | None,
) -> dict[str, Any]:
    """Translate trigger execution limits into canonical launch controls.

    Raises ``ValueError`` for unknown limit keys or invalid values so an
    unenforceable spend control fails closed instead of launching uncapped.
    """
    params = dict(initial_parameters or {})
    limits = dict(execution_limits or {})
    unknown = sorted(set(limits) - set(_SUPPORTED_EXECUTION_LIMITS))
    if unknown:
        raise ValueError(
            "Unsupported execution_limits keys with no canonical launch "
            f"control: {', '.join(unknown)}."
        )
    budget = limits.get("maxModelBudgetUsd")
    if budget is not None:
        try:
            budget_value = float(budget)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "execution_limits.maxModelBudgetUsd must be a positive number."
            ) from exc
        if not math.isfinite(budget_value) or budget_value <= 0:
            raise ValueError(
                "execution_limits.maxModelBudgetUsd must be a positive number."
            )
        params["maxBudgetUsd"] = budget_value
    return params


def enforce_event_publication_intent(
    initial_parameters: dict[str, Any],
    publication_intent: str,
) -> dict[str, Any]:
    """Enforce the trigger publication intent on expanded launch parameters.

    ``"none"`` strips preset-declared publication payloads so an unattended
    trigger never publishes; any other intent is rejected because this path
    has no approval-gated publication control.
    """
    intent = str(publication_intent or "none").strip() or "none"
    if intent != "none":
        raise ValueError(
            f"publication_intent {intent!r} is not supported by the "
            "unattended GitHub event path; only 'none' is allowed."
        )
    params = dict(initial_parameters or {})
    params.pop("publish", None)
    for key in ("workflow", "task"):
        nested = params.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("publish"), dict):
            nested = dict(nested)
            nested.pop("publish", None)
            params[key] = nested
    return params


class TemporalEventExecutionDispatcher:
    """Dispatch admitted deliveries as instance-owned Temporal workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def dispatch(
        self,
        *,
        preset_slug: str,
        identity_key: str,
        repository: str,
        issue_number: int,
        title: str,
        parameters: dict[str, Any],
        execution_limits: dict[str, Any] | None = None,
        publication_intent: str = "none",
    ) -> str:
        task_payload = {
            "taskTemplate": {"slug": preset_slug, "scope": "global"},
            "inputs": {
                "repository": repository,
                "issueNumber": issue_number,
            },
        }
        initial_parameters = dict(parameters or {})
        initial_parameters["task"] = task_payload
        initial_parameters["repository"] = repository
        # Route the launch through the existing catalog expansion owner so
        # the run executes the preset's steps, runtime, and publication
        # settings instead of a generic instruction. Unknown presets and
        # invalid bindings raise here; the caller keeps the receipt pending
        # with a safe reason instead of launching the wrong work.
        expanded = await expand_preset_for_child_run(
            session=self._session,
            initial_parameters=initial_parameters,
            allow_goal_schedule=False,
            user_id=None,
        )
        expanded = apply_event_execution_limits(expanded, execution_limits)
        expanded = enforce_event_publication_intent(expanded, publication_intent)
        service = TemporalExecutionService(self._session)
        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            # Single-user instance: event-triggered launches are
            # instance-owned (SYSTEM default), never human-owned.
            owner_id=None,
            owner_type=None,
            title=title,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=expanded,
            idempotency_key=identity_key,
            repository=repository,
            integration="github",
        )
        return str(getattr(record, "workflow_id", "") or "")


def build_dispatch_parameters(
    *,
    preset_slug: str,
    repository: str,
    issue_number: int,
    delivery_key: str,
    identity_key: str,
    execution_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build diagnostics-bearing parameters for an admitted delivery.

    Carries bounded normalized facts plus the preset slug the operator
    bound to the trigger. The preset's own expansion and admission remain
    the authority on what actually runs; configured limits travel alongside
    for the dispatcher to translate, never as an unenforced hint.
    """
    issue_ref = f"{repository}#{issue_number}" if issue_number else repository
    return {
        "instructions": (
            f"GitHub event trigger for {issue_ref}: run preset "
            f"'{preset_slug}' within its configured execution limits."
        ),
        "presetSlug": preset_slug,
        "githubEventTrigger": {
            "repository": repository,
            "issueNumber": issue_number,
            "deliveryKey": delivery_key,
            "identityKey": identity_key,
            "executionLimits": dict(execution_limits or {}),
        },
    }
