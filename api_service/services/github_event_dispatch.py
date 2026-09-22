"""Default Temporal dispatch for opt-in GitHub event deliveries (#3967).

Single dispatch identity for the event trigger path: an admitted delivery
launches through the existing :class:`TemporalExecutionService` admission
boundary (pause guard, worker freshness, Skill input validation, provider
profile runtime, plan source) with the stable delivery identity key as the
Temporal idempotency key, so a redelivery that re-attempts a lost start
reconciles to the same logical execution instead of minting a second one.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.temporal.service import TemporalExecutionService


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
    ) -> str:
        """Protocol stub; implementations dispatch the admitted delivery."""
        raise NotImplementedError


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
    ) -> str:
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
            initial_parameters=parameters,
            idempotency_key=identity_key,
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
    """Build minimal instructions-bearing parameters for an admitted delivery.

    Carries only bounded normalized facts plus the preset slug the operator
    bound to the trigger; the preset's own admission (Skill validation and
    beyond) remains the authority on what actually runs.
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
