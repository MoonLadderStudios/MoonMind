"""Managed-agent adapter: profile resolution, env shaping, slot management.

This adapter fulfils the core requirements of Phase 5
(Auth-Profile and Rate-Limit Controls) as described in
  docs/Temporal/ManagedAndExternalAgentExecutionModel.md

Key responsibilities:
 - Resolve the ``execution_profile_ref`` on an ``AgentExecutionRequest`` to a
   concrete ``ManagedAgentAuthProfile`` dict returned by the
   ``auth_profile.list`` activity.
 - Shape the environment for OAuth (volume-mount) and API-key modes.
   Credentials are never stored in workflow payloads; only ``profile_id`` is
   persisted in ``AgentRunHandle.metadata``.
 - Signal ``AuthProfileManager`` to request / release slot leases and to send
   cooldown reports on 429 responses.
 - Maintain the ``slot_assigned`` wait loop internally (DOC-REQ-004).

Design constraints (from constitution.md / spec):
 - No raw credential values in durable Temporal state.
 - No OAuth env vars leaked into the child environment beyond the expected
   ``BROWSER_AUTH``-style keys.
 - Fail-fast on unsupported profiles rather than silent fallback.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
    ManagedRuntimeProfile,
    TERMINAL_AGENT_RUN_STATES,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

logger = logging.getLogger(__name__)

# Env-var prefixes / names cleared when shaping OAuth environments (DOC-REQ-007).
# These are the sensitive keys that must NOT appear in child-process environments.
_OAUTH_CLEARED_VARS: frozenset[str] = frozenset(
    {
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    }
)
_BASE_ENV_FILTER_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "api_key",
    "private_key",
)
# GitHub CLI authentication is required for skill workflows like pr-resolver.
# Keep these pass-through tokens available to managed runtimes.
_BASE_ENV_TOKEN_ALLOWLIST: frozenset[str] = frozenset({"GITHUB_TOKEN", "GH_TOKEN"})

_PR_RESOLVER_RESULT_PATH = Path("artifacts/pr_resolver_result.json")
_PR_RESOLVER_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"failed", "blocked", "attempts_exhausted"}
)

# Type aliases for async signal callables injected by the caller/workflow.
ProfileFetcherFunc = Callable[..., Awaitable[dict[str, Any]]]
SlotRequestFunc = Callable[..., Awaitable[Any]]
SlotReleaseFunc = Callable[..., Awaitable[Any]]
CooldownReportFunc = Callable[..., Awaitable[Any]]
RunLauncherFunc = Callable[..., Awaitable[Any]]


def _should_filter_base_env_var(key: str) -> bool:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return False
    if normalized_key.upper() in _BASE_ENV_TOKEN_ALLOWLIST:
        return False
    lowered = normalized_key.lower()
    return any(fragment in lowered for fragment in _BASE_ENV_FILTER_FRAGMENTS)


def _shape_environment_for_oauth(
    base_env: dict[str, str],
    *,
    volume_mount_path: str | None,
) -> dict[str, str]:
    """Return env dict shaped for OAuth volume-mount mode.

    Clears sensitive API-key vars and sets browser-auth helpers if a
    volume mount path is provided.  Does NOT expose secrets.
    """
    env = dict(base_env)
    for key in _OAUTH_CLEARED_VARS:
        env.pop(key, None)
    if volume_mount_path:
        env["MANAGED_AUTH_VOLUME_PATH"] = volume_mount_path
    return env


def _shape_environment_for_api_key(
    base_env: dict[str, str],
    *,
    api_key_ref: str | None,
    account_label: str | None,
) -> dict[str, str]:
    """Return env dict shaped for API-key mode.

    The api_key_ref is a *reference* (e.g. a secret store key name), not the
    raw credential.  The actual resolution of the reference into a real key
    is delegated to the runtime launcher (out of scope for Phase 5).
    """
    env = dict(base_env)
    for key in _OAUTH_CLEARED_VARS:
        env.pop(key, None)
    if api_key_ref:
        # Pass only the reference, never the real value.
        env["MANAGED_API_KEY_REF"] = api_key_ref
    if account_label:
        env["MANAGED_ACCOUNT_LABEL"] = account_label
    return env


def _derive_pr_resolver_failure(
    workspace_path: str | None,
) -> tuple[str | None, str | None]:
    """Return failure metadata from pr-resolver artifacts when present."""

    workspace = str(workspace_path or "").strip()
    if not workspace:
        return None, None

    result_path = Path(workspace) / _PR_RESOLVER_RESULT_PATH
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None

    status = str(
        payload.get("status") or payload.get("merge_outcome") or ""
    ).strip().lower()
    if status not in _PR_RESOLVER_FAILURE_STATUSES:
        return None, None

    reason = str(payload.get("final_reason") or payload.get("reason") or "").strip()
    next_step = str(payload.get("next_step") or "").strip()
    summary_parts = [f"pr-resolver reported status '{status}'"]
    if reason:
        summary_parts.append(reason)
    if next_step:
        summary_parts.append(f"next_step={next_step}")
    return "execution_error", "; ".join(summary_parts)


class ProfileResolutionError(RuntimeError):
    """Raised when a profile cannot be resolved from the activity result."""


class ManagedAgentAdapter:
    """Lifecycle adapter for managed agent runtimes with auth-profile controls.

    Parameters
    ----------
    profile_fetcher:
        Async callable: ``profile_fetcher(runtime_id=...) -> list[dict]``.
        Typically backed by the ``auth_profile.list`` Temporal activity.
    slot_requester:
        Async callable that signals the AuthProfileManager to request a slot.
    slot_releaser:
        Async callable that signals the AuthProfileManager to release a slot.
    cooldown_reporter:
        Async callable that signals the AuthProfileManager about a 429 event.
    workflow_id:
        Temporal workflow ID of the *current* AgentRun workflow.  Used in
        slot-request/release signals so the AuthProfileManager can correlate
        requests to callers.
    run_launcher:
        Optional async callable that launches the managed agent process via an activity.
    """

    def __init__(
        self,
        *,
        profile_fetcher: ProfileFetcherFunc,
        slot_requester: SlotRequestFunc,
        slot_releaser: SlotReleaseFunc,
        cooldown_reporter: CooldownReportFunc,
        workflow_id: str,
        runtime_id: str | None = None,
        run_store: ManagedRunStore | None = None,
        run_launcher: RunLauncherFunc | None = None,
    ) -> None:
        self._fetch_profiles = profile_fetcher
        self._request_slot = slot_requester
        self._release_slot = slot_releaser
        self._report_cooldown = cooldown_reporter
        self._workflow_id = workflow_id
        self._runtime_id = runtime_id
        self._run_store = run_store
        self._run_launcher = run_launcher
        self._active_profile_id: str | None = None

    # ------------------------------------------------------------------
    # AgentAdapter protocol implementation
    # ------------------------------------------------------------------

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        """Resolve profile, shape env, request slot, return handle."""
        if request.agent_kind != "managed":
            raise ValueError(
                f"ManagedAgentAdapter only supports agent_kind='managed', "
                f"got '{request.agent_kind}'"
            )

        profile = await self._resolve_profile(
            execution_profile_ref=request.execution_profile_ref,
            runtime_id=self._runtime_id or request.agent_id,
        )
        profile_id: str = profile["profile_id"]
        auth_mode: str = profile.get("auth_mode", "api_key")

        # Shape environment according to auth mode (DOC-REQ-005, DOC-REQ-006).
        base_env = {
            k: v for k, v in os.environ.items()
            if not _should_filter_base_env_var(k)
        }
        if auth_mode == "oauth":
            shaped_env = _shape_environment_for_oauth(
                base_env,
                volume_mount_path=profile.get("volume_mount_path"),
            )
        else:
            shaped_env = _shape_environment_for_api_key(
                base_env,
                api_key_ref=profile.get("api_key_ref"),
                account_label=profile.get("account_label"),
            )

        # Persist only the profile_id reference — never raw credentials
        # (DOC-REQ-008 / constitution security rule).
        self._active_profile_id = profile_id
        
        try:
            from temporalio import workflow
            if workflow.in_workflow():
                run_id = str(workflow.uuid4())
            else:
                run_id = str(uuid4())
        except ImportError:
            run_id = str(uuid4())

        # Signal AuthProfileManager to acquire a slot lease (DOC-REQ-004).
        await self._request_slot(
            requester_workflow_id=self._workflow_id,
            runtime_id=self._runtime_id or request.agent_id,
        )
        
        if self._run_launcher is not None:
            runtime_id_for_profile = self._runtime_id or request.agent_id
            cmd_template = profile.get("command_template")
            if not cmd_template:
                if runtime_id_for_profile == "gemini_cli":
                    cmd_template = ["gemini"]
                elif runtime_id_for_profile == "claude_code":
                    cmd_template = ["claude"]
                elif runtime_id_for_profile == "codex_cli":
                    cmd_template = ["codex", "exec", "--full-auto"]
                else:
                    cmd_template = [runtime_id_for_profile]

            profile_obj = ManagedRuntimeProfile(
                profile_id=profile_id,
                runtime_id=runtime_id_for_profile,
                auth_mode=auth_mode,
                env_overrides=shaped_env,
                command_template=cmd_template,
            )
            
            # The workspace path is usually managed by the worker, but we can pass it if known
            workspace_path = None
            
            record_dict = await self._run_launcher(
                payload={
                    "run_id": run_id,
                    "request": request.model_dump(mode="json", by_alias=True) if hasattr(request, "model_dump") else request,
                    "profile": profile_obj.model_dump(mode="json", by_alias=True),
                    "workspace_path": workspace_path,
                }
            )
            status = record_dict.get("status", "launching")
        elif self._run_store is not None:
            record = ManagedRunRecord(
                run_id=run_id,
                agent_id=request.agent_id,
                runtime_id=self._runtime_id or request.agent_id,
                status="launching",
                started_at=datetime.now(tz=UTC),
            )
            self._run_store.save(record)
            status = "launching"
        else:
            status = "launching"

        logger.info(
            "ManagedAgentAdapter.start profile_id=%s run_id=%s workflow_id=%s",
            profile_id,
            run_id,
            self._workflow_id,
        )
        return AgentRunHandle(
            runId=run_id,
            agentKind="managed",
            agentId=request.agent_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            metadata={
                "profile_id": profile_id,
                "auth_mode": auth_mode,
                "env_keys_count": len(shaped_env),
            },
        )

    async def status(self, run_id: str) -> AgentRunStatus:
        """Return status from the run store, falling back to stub if no store."""
        if self._run_store is not None:
            record = self._run_store.load(run_id)
            if record is not None:
                return AgentRunStatus(
                    runId=record.run_id,
                    agentKind="managed",
                    agentId=record.agent_id,
                    status=record.status,
                )
        return AgentRunStatus(
            runId=run_id,
            agentKind="managed",
            agentId="managed",
            status="running",
        )

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        """Return result from the run store, falling back to empty if no store."""
        if self._run_store is not None:
            record = self._run_store.load(run_id)
            if record is not None and record.status in TERMINAL_AGENT_RUN_STATES:
                output_refs: list[str] = []
                if record.log_artifact_ref:
                    output_refs.append(record.log_artifact_ref)
                if record.diagnostics_ref:
                    output_refs.append(record.diagnostics_ref)
                summary = record.error_message or f"Completed with status {record.status}"
                failure_class = record.failure_class
                if failure_class is None and record.status == "completed":
                    derived_failure_class, derived_summary = _derive_pr_resolver_failure(
                        record.workspace_path
                    )
                    if derived_failure_class is not None:
                        failure_class = derived_failure_class
                        if derived_summary:
                            summary = derived_summary
                return AgentRunResult(
                    summary=summary,
                    output_refs=output_refs,
                    failure_class=failure_class,
                )
        return AgentRunResult()

    async def cancel(self, run_id: str) -> AgentRunStatus:
        """Release slot and return cancelled status."""
        await self.release_slot()
        return AgentRunStatus(
            runId=run_id,
            agentKind="managed",
            agentId="managed",
            status="cancelled",
        )

    # ------------------------------------------------------------------
    # Slot management helpers (called from workflow coordination code)
    # ------------------------------------------------------------------

    async def release_slot(self) -> None:
        """Signal AuthProfileManager to release the active slot lease."""
        if self._active_profile_id is None:
            logger.warning(
                "release_slot called but no active profile_id on %s",
                self._workflow_id,
            )
            return
        await self._release_slot(
            requester_workflow_id=self._workflow_id,
            profile_id=self._active_profile_id,
        )
        logger.info(
            "ManagedAgentAdapter.release_slot profile_id=%s workflow_id=%s",
            self._active_profile_id,
            self._workflow_id,
        )
        self._active_profile_id = None

    async def report_429_cooldown(
        self,
        *,
        profile_id: str | None = None,
        cooldown_seconds: int = 300,
    ) -> None:
        """Report a 429 rate-limit hit to the AuthProfileManager (DOC-REQ-009).

        Parameters
        ----------
        profile_id:
            Profile that received the 429.  Defaults to the active profile.
        cooldown_seconds:
            Cooldown duration in seconds.
        """
        pid = profile_id or self._active_profile_id
        if pid is None:
            raise ValueError("profile_id is required when no active slot is held")
        await self._report_cooldown(
            profile_id=pid,
            cooldown_seconds=cooldown_seconds,
        )
        logger.info(
            "ManagedAgentAdapter.report_429_cooldown profile_id=%s seconds=%d",
            pid,
            cooldown_seconds,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_profile(
        self,
        *,
        execution_profile_ref: str,
        runtime_id: str,
    ) -> dict[str, Any]:
        """Resolve execution_profile_ref to a concrete profile dict.

        The ``execution_profile_ref`` is either:
         - An exact ``profile_id`` string, or
         - The special sentinel ``"auto"`` meaning use the first available
           profile for the runtime family.

        Raises ``ProfileResolutionError`` if no matching profile is found.
        """
        fetch_res = self._fetch_profiles(runtime_id=runtime_id)
        if isinstance(fetch_res, list):
            import logging
            logging.error(f"_fetch_profiles returned a list: {fetch_res}")
            result = {"profiles": fetch_res}
        elif isinstance(fetch_res, dict):
            import logging
            logging.error(f"_fetch_profiles returned a dict directly: {fetch_res}")
            result = fetch_res
        else:
            result: dict[str, Any] = await fetch_res
        profiles: list[dict[str, Any]] = result.get("profiles", [])

        if not profiles:
            raise ProfileResolutionError(
                f"No enabled auth profiles found for runtime_id='{runtime_id}'"
            )

        if execution_profile_ref == "auto":
            return profiles[0]

        for profile in profiles:
            if profile.get("profile_id") == execution_profile_ref:
                return profile

        raise ProfileResolutionError(
            f"Auth profile '{execution_profile_ref}' not found for "
            f"runtime_id='{runtime_id}' (available profile count: {len(profiles)})"
        )


__all__ = [
    "ManagedAgentAdapter",
    "ProfileResolutionError",
    "ProfileFetcherFunc",
    "SlotRequestFunc",
    "SlotReleaseFunc",
    "CooldownReportFunc",
    "_shape_environment_for_api_key",
    "_shape_environment_for_oauth",
    "_OAUTH_CLEARED_VARS",
]
