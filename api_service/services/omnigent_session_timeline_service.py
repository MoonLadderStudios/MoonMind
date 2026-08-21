"""Read-only persistence adapter for Omnigent operator projections."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.observability import TelemetrySettings, build_backend_url
from moonmind.omnigent.control_plane.repositories import ControlPlaneRepositories
from moonmind.omnigent.control_plane.stuck_state_reconciliation import (
    inspect_stuck_state,
)
from moonmind.omnigent.control_plane.timeline import build_timeline, safe_timeline_ref


_EVENT_OBSERVATION_TYPES = (
    "event",
    "event_frontier",
    "event_batch",
    "provider_event",
    "provider_event_batch",
)
_SNAPSHOT_OBSERVATION_TYPES = ("snapshot", "provider_snapshot")


class OmnigentSessionProjectionNotFound(LookupError):
    pass


class OmnigentDiagnosticTargetUnavailable(LookupError):
    pass


class OmnigentSessionTimelineService:
    """Build bounded read projections without exposing repositories to routers."""

    def __init__(self, session: AsyncSession) -> None:
        self._repos = ControlPlaneRepositories.bind(session)

    async def _session(self, session_id: str):
        session = await self._repos.sessions.get(session_id)
        if session is None:
            raise OmnigentSessionProjectionNotFound(session_id)
        return session

    async def timeline(self, session_id: str) -> dict:
        session = await self._session(session_id)
        active_turn = (
            await self._repos.turn_attempts.get(session.active_turn_attempt_id)
            if session.active_turn_attempt_id is not None
            else None
        )
        turn_attempt_count = await self._repos.turn_attempts.count_for_session(
            session_id
        )
        latest_snapshot = await self._repos.observations.latest_for_session(
            session_id, observation_types=_SNAPSHOT_OBSERVATION_TYPES
        )
        latest_event = await self._repos.observations.latest_for_session(
            session_id, observation_types=_EVENT_OBSERVATION_TYPES
        )
        active_command = await self._repos.commands.active_for_session(session_id)
        latest_decision = await self._repos.decisions.latest_for_session(session_id)
        if latest_decision is None and session.last_decision_ref is not None:
            latest_decision = await self._repos.decisions.get(
                session.last_decision_ref
            )
        cleanup = await self._repos.cleanup.get(session_id)
        telemetry = TelemetrySettings.from_env()
        encoded_session_id = quote(session_id, safe="")
        trace_link = (
            f"/api/omnigent/sessions/{encoded_session_id}/trace"
            if latest_decision is not None
            and safe_timeline_ref(latest_decision.trace_ref) is not None
            and telemetry.trace_url_template
            else None
        )
        log_link = (
            f"/api/omnigent/sessions/{encoded_session_id}/logs"
            if telemetry.logs_url_template and session.moonmind_workflow_id
            else None
        )
        return build_timeline(
            session=session,
            turn_attempts=[active_turn] if active_turn is not None else (),
            observations=[
                item
                for item in (latest_snapshot, latest_event)
                if item is not None
            ],
            commands=[active_command] if active_command is not None else (),
            decisions=[latest_decision] if latest_decision is not None else (),
            cleanup=cleanup,
            turn_attempt_count=turn_attempt_count,
            trace_link=trace_link,
            log_link=log_link,
        ).to_dict()

    async def diagnostic_target(self, session_id: str, kind: str) -> str:
        session = await self._session(session_id)
        telemetry = TelemetrySettings.from_env()
        if kind == "trace":
            decision = await self._repos.decisions.latest_for_session(session_id)
            if decision is None and session.last_decision_ref is not None:
                decision = await self._repos.decisions.get(
                    session.last_decision_ref
                )
            trace_id = (
                safe_timeline_ref(decision.trace_ref)
                if decision is not None
                else None
            )
            target = build_backend_url(
                telemetry.trace_url_template, trace_id=trace_id
            )
        else:
            target = build_backend_url(
                telemetry.logs_url_template,
                workflow_id=session.moonmind_workflow_id,
                run_id=session.moonmind_run_id,
            )
        if target is None:
            raise OmnigentDiagnosticTargetUnavailable(kind)
        return target

    async def stuck_state(self, session_id: str) -> dict:
        await self._session(session_id)
        inspection = await inspect_stuck_state(
            self._repos, session_id=session_id, now=datetime.now(UTC)
        )
        findings = inspection.findings if inspection is not None else ()
        response = inspection.response if inspection is not None else None
        return {
            "sessionId": session_id,
            "findings": [
                {
                    "reason": finding.reason.value,
                    "action": finding.action.value,
                    "detail": finding.detail,
                    "remediation": finding.remediation,
                }
                for finding in findings
            ],
            "response": None
            if response is None
            else {
                "reconcile": response.reconcile,
                "quarantine": response.quarantine,
                "expectedRevision": response.expected_revision,
                "expectedFencingGeneration": response.expected_fencing_generation,
                "reasons": [reason.value for reason in response.reasons],
                "remediation": response.remediation,
            },
        }


__all__ = [
    "OmnigentDiagnosticTargetUnavailable",
    "OmnigentSessionProjectionNotFound",
    "OmnigentSessionTimelineService",
]
