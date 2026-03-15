import asyncio
import uuid
import datetime
from typing import Dict
from temporalio import workflow
from datetime import timedelta

from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile

from .base import AgentAdapter
from ..workflows.shared import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult
from ..runtime.store import ManagedRunStore
from ..runtime.launcher import ManagedRuntimeLauncher
from ..runtime.supervisor import ManagedRunSupervisor

# Hardcoded profile registry (Phase 5 replaces with auth-profile system)
_DEFAULT_PROFILES = {
    "default-managed": {
        "runtime_id": "codex-cli",
        "command_template": ["codex", "run"],
        "default_model": "o4-mini",
        "default_effort": "medium",
        "default_timeout_seconds": 3600,
        "workspace_mode": "tempdir",
        "env_overrides": {},
    },
}

def _resolve_profile(profile_ref: str) -> "ManagedRuntimeProfile":
    """Resolve a profile reference to a ManagedRuntimeProfile."""
    profile_data = _DEFAULT_PROFILES.get(profile_ref)
    if profile_data is None:
        raise ValueError(f"Unknown execution profile: {profile_ref}")
    return ManagedRuntimeProfile(**profile_data)


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

    async def _fetch_profile(self, runtime_id: str, profile_ref: str) -> "ManagedRuntimeProfile":
        if profile_ref == "default-managed":
            return _resolve_profile(profile_ref)
            
        try:
            # Load from DB via temporal activity
            result = await workflow.execute_activity(
                "auth_profile.list",
                {"runtime_id": runtime_id},
                start_to_close_timeout=timedelta(seconds=30),
            )
            profiles_data = result.get("profiles", []) if result else []
            matched_profile_data = next((p for p in profiles_data if p["profile_id"] == profile_ref), None)
            
            if matched_profile_data is None:
                raise ValueError(f"Unknown execution profile: {profile_ref}")
            
            command_template = ["codex", "run"]
            if runtime_id == "gemini_cli":
                command_template = ["gemini", "run"]
            elif runtime_id == "claude_code":
                command_template = ["claude", "run"]
            
            # Use profile to define aspects of ManagedRuntimeProfile
            return ManagedRuntimeProfile(
                runtime_id=runtime_id,
                command_template=command_template,
                default_model="default",
                default_effort="medium",
                default_timeout_seconds=3600,
                workspace_mode="tempdir",
                env_overrides={},
            )
        except Exception as e:
            workflow.logger.warning(f"Failed to fetch auth profile '{profile_ref}', falling back to default: {e}", exc_info=True)
            if isinstance(e, ValueError):
                raise
            # Fallback legacy behavior
            return _resolve_profile("default-managed")

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        run_id = request.idempotency_key or f"managed-{uuid.uuid4()}"

        # DOC-REQ-MNG-RESP: resolve auth/runtime profiles
        profile = await self._fetch_profile(request.agent_id, request.execution_profile_ref or "default-managed")

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
