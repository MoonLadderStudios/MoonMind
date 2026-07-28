"""Production adapters for bounded remediation observability reads."""

from __future__ import annotations

import asyncio
from pathlib import Path

from api_service.api.routers import agent_runs
from moonmind.workflows.temporal.remediation_tools import (
    RemediationLiveFollowEvent,
    RemediationLiveFollowResult,
    RemediationLogReadResult,
    RemediationLogStream,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


class ManagedRunRemediationLogAdapter:
    """Read the canonical managed-run journal/spool/artifact fallback."""

    async def _events(
        self, *, agent_run_id: str, since: int | None, limit: int, streams: set[str]
    ) -> list[dict[str, object]]:
        store = ManagedRunStore(agent_runs._get_agent_runtime_store_root())
        record = await agent_runs._load_managed_run_record(store, agent_run_id)
        if record is None:
            return []
        session_record = await asyncio.to_thread(
            agent_runs._load_agent_run_session_record, agent_run_id
        )
        events, _source = await asyncio.to_thread(
            agent_runs._load_agent_run_observability_events,
            record=record,
            session_record=session_record,
            limit=limit,
            since=since,
            streams=streams,
        )
        events.sort(key=agent_runs._event_sort_key)
        return events[-limit:] if since is None else events[:limit]

    async def read_logs(
        self,
        *,
        agent_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
    ) -> RemediationLogReadResult:
        since = int(cursor) if cursor and cursor.isdigit() else None
        limit = max(1, min(tail_lines or 200, 2000))
        if stream == "diagnostics":
            store = ManagedRunStore(agent_runs._get_agent_runtime_store_root())
            record = await agent_runs._load_managed_run_record(store, agent_run_id)
            diagnostics_ref = str(
                getattr(record, "diagnostics_ref", None) or ""
            ).strip()
            if not diagnostics_ref:
                return RemediationLogReadResult(
                    agent_run_id=agent_run_id,
                    stream=stream,
                    lines=(),
                    next_cursor=None,
                )
            root = Path(agent_runs._get_agent_runtime_artifacts_root()).resolve()
            path = (root / diagnostics_ref).resolve()
            if root not in path.parents or not path.is_file():
                return RemediationLogReadResult(
                    agent_run_id=agent_run_id,
                    stream=stream,
                    lines=(),
                    next_cursor=None,
                )
            content = await asyncio.to_thread(
                path.read_text, encoding="utf-8", errors="replace"
            )
            return RemediationLogReadResult(
                agent_run_id=agent_run_id,
                stream=stream,
                lines=tuple(content.splitlines()[-limit:]),
                next_cursor=None,
            )
        streams = (
            {"stdout", "stderr", "system", "session"}
            if stream == "merged"
            else {stream}
        )
        events = await self._events(
            agent_run_id=agent_run_id, since=since, limit=limit, streams=streams
        )
        lines = tuple(str(event.get("text") or "") for event in events)
        last_sequence = max(
            (int(event.get("sequence") or 0) for event in events), default=0
        )
        return RemediationLogReadResult(
            agent_run_id=agent_run_id,
            stream=stream,
            lines=lines,
            next_cursor=str(last_sequence) if last_sequence else None,
        )

    async def follow_logs(
        self, *, agent_run_id: str, from_sequence: int | None = None
    ) -> RemediationLiveFollowResult:
        events = await self._events(
            agent_run_id=agent_run_id,
            since=from_sequence,
            limit=200,
            streams={"stdout", "stderr", "system", "session"},
        )
        normalized = tuple(
            RemediationLiveFollowEvent(
                sequence=int(event.get("sequence") or 0),
                stream=str(event.get("stream") or "system"),
                text=str(event.get("text") or ""),
                timestamp=str(event.get("timestamp") or "") or None,
            )
            for event in events
        )
        last_sequence = max(
            (event.sequence for event in normalized), default=from_sequence or 0
        )
        return RemediationLiveFollowResult(
            agent_run_id=agent_run_id,
            events=normalized,
            resume_cursor={"sequence": last_sequence},
        )


__all__ = ["ManagedRunRemediationLogAdapter"]
