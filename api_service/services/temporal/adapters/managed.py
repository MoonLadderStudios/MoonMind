import asyncio
import uuid
import datetime
from typing import Dict
from .base import AgentAdapter
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult
from ..runtime.store import ManagedRunStore
from ..runtime.launcher import ManagedRuntimeLauncher
from ..runtime.supervisor import ManagedRunSupervisor
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile

# Hardcoded profile registry (Phase 5 replaces with auth-profile system)
_DEFAULT_PROFILES: Dict[str, ManagedRuntimeProfile] = {
    "default-managed": ManagedRuntimeProfile(
        runtime_id="codex-cli",
        command_template=["codex", "run"],
        default_model="o4-mini",
        default_effort="medium",
        default_timeout_seconds=3600,
        workspace_mode="tempdir",
        env_overrides={},
    ),
}


def _resolve_profile(profile_ref: str) -> ManagedRuntimeProfile:
    """Resolve a profile reference to a ManagedRuntimeProfile."""
    profile = _DEFAULT_PROFILES.get(profile_ref)
    if profile is None:
        raise ValueError(f"Unknown execution profile: {profile_ref}")
    return profile


class ManagedAgentAdapter(AgentAdapter):
    def __init__(
        self,
        store: ManagedRunStore | None = None,
        launcher: ManagedRuntimeLauncher | None = None,
        supervisor: ManagedRunSupervisor | None = None,
    ) -> None:
        self._store = store
        self._launcher = launcher
        self._supervisor = supervisor

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        run_id = request.idempotency_key or f"managed-{uuid.uuid4()}"

        # DOC-REQ-MNG-RESP: resolve auth/runtime profiles
        profile = _resolve_profile(request.execution_profile_ref or "default-managed")

        if self._launcher is None or self._store is None or self._supervisor is None:
            # Stub mode: no runtime components injected
            return AgentRunHandle(
                run_id=run_id,
                agent_kind="managed",
                agent_id=request.agent_id,
                status=AgentRunStatus.launching,
                started_at=datetime.datetime.utcnow().isoformat() + "Z",
                poll_hint_seconds=5,
            )

        # Idempotency: check if run already exists and is active
        existing = self._store.load(run_id)
        if existing is not None and existing.status not in (
            "completed", "failed", "cancelled", "timed_out"
        ):
            return AgentRunHandle(
                run_id=existing.run_id,
                agent_kind="managed",
                agent_id=existing.agent_id,
                status=AgentRunStatus(existing.status),
                started_at=existing.started_at.isoformat(),
                poll_hint_seconds=5,
            )

        # DOC-REQ-MNG-RESP: launch asynchronously
        timeout_seconds = profile.default_timeout_seconds
        if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
            timeout_seconds = request.timeout_policy["timeout_seconds"]

        record, process = await self._launcher.launch(
            run_id=run_id,
            request=request,
            profile=profile,
            workspace_path=request.workspace_spec.get("path") if request.workspace_spec else None,
        )

        # Start supervision in background
        asyncio.create_task(
            self._supervisor.supervise(
                run_id=run_id,
                process=process,
                timeout_seconds=timeout_seconds,
            )
        )

        return AgentRunHandle(
            run_id=run_id,
            agent_kind="managed",
            agent_id=request.agent_id,
            status=AgentRunStatus.launching,
            started_at=record.started_at.isoformat(),
            poll_hint_seconds=5,
        )

    async def status(self, run_id: str) -> AgentRunStatus:
        if self._store is None:
            return AgentRunStatus.running

        record = self._store.load(run_id)
        if record is None:
            raise ValueError(f"Run not found: {run_id}")
        return AgentRunStatus(record.status)

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        if self._store is None:
            return AgentRunResult(summary="Managed run complete", output_refs=[])

        record = self._store.load(run_id)
        if record is None:
            raise ValueError(f"Run not found: {run_id}")

        return AgentRunResult(
            summary=record.error_message if record.status != "completed" else "Managed run complete",
            output_refs=[],
            diagnostics_ref=record.diagnostics_ref,
            failure_class=record.failure_class,
        )

    async def cancel(self, run_id: str) -> None:
        if self._supervisor is None:
            return
        await self._supervisor.cancel(run_id)
