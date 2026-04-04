"""Managed-agent adapter: profile resolution, env shaping, slot management.

This adapter fulfils the core requirements of Phase 5
(Auth-Profile and Rate-Limit Controls) as described in
  docs/Temporal/ManagedAndExternalAgentExecutionModel.md

Key responsibilities:
 - Resolve the ``execution_profile_ref`` on an ``AgentExecutionRequest`` to a
   concrete ``ManagedAgentProviderProfile`` dict returned by the
   ``provider_profile.list`` activity.
 - Shape the environment for OAuth (volume-mount) and API-key modes.
   Credentials are never stored in workflow payloads; only ``profile_id`` is
   persisted in ``AgentRunHandle.metadata``.
 - Signal ``ProviderProfileManager`` to request / release slot leases and to send
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
from moonmind.auth.env_shaping import (
    OAUTH_CLEARED_VARS,
    _should_filter_base_env_var,
)
from moonmind.workflows.tasks.runtime_defaults import resolve_runtime_defaults

logger = logging.getLogger(__name__)


# GitHub CLI authentication is required for workflows like pr-resolver.
# Only the *key names* are propagated through workflow/activity payloads; the
# values are injected at launch time by the agent-runtime activity worker.
_SECRET_ENV_PASSTHROUGH_KEYS: tuple[str, ...] = ("GH_TOKEN", "GITHUB_TOKEN")

_PR_RESOLVER_RESULT_PATHS: tuple[Path, ...] = (
    Path("var/pr_resolver/result.json"),
    Path("artifacts/pr_resolver_result.json"),
)
_PR_RESOLVER_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"failed", "blocked", "attempts_exhausted"}
)
_PR_RESOLVER_BLOCKED_STATUSES: frozenset[str] = frozenset(
    {"blocked", "attempts_exhausted"}
)


def _load_pr_resolver_result(workspace_path: str | None) -> dict[str, Any] | None:
    """Load a pr-resolver result payload from known workspace locations."""

    workspace = str(workspace_path or "").strip()
    if not workspace:
        return None

    for rel_path in _PR_RESOLVER_RESULT_PATHS:
        result_path = Path(workspace) / rel_path
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None

# Type aliases for async signal callables injected by the caller/workflow.
ProfileFetcherFunc = Callable[..., Awaitable[dict[str, Any]]]
SlotRequestFunc = Callable[..., Awaitable[Any]]
SlotReleaseFunc = Callable[..., Awaitable[Any]]
CooldownReportFunc = Callable[..., Awaitable[Any]]
RunLauncherFunc = Callable[..., Awaitable[Any]]



def _derive_pr_resolver_failure(
    workspace_path: str | None,
) -> tuple[str | None, str | None]:
    """Return failure metadata from pr-resolver artifacts when present."""
    payload = _load_pr_resolver_result(workspace_path)
    if payload is None:
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
    failure_class = (
        "user_error" if status in _PR_RESOLVER_BLOCKED_STATUSES else "execution_error"
    )
    return failure_class, "; ".join(summary_parts)


def _is_generic_process_exit_summary(summary: str | None) -> bool:
    """Return whether a run summary only reports generic process exit."""

    text = str(summary or "").strip().lower()
    if not text:
        return True
    return text.startswith("process exited with code")


class ProfileResolutionError(RuntimeError):
    """Raised when a profile cannot be resolved from the activity result."""


class ManagedAgentAdapter:
    """Lifecycle adapter for managed agent runtimes with auth-profile controls.

    Parameters
    ----------
    profile_fetcher:
        Async callable: ``profile_fetcher(runtime_id=...) -> list[dict]``.
        Typically backed by the ``provider_profile.list`` Temporal activity.
    slot_requester:
        Async callable that signals the ProviderProfileManager to request a slot.
    slot_releaser:
        Async callable that signals the ProviderProfileManager to release a slot.
    cooldown_reporter:
        Async callable that signals the ProviderProfileManager about a 429 event.
    workflow_id:
        Temporal workflow ID of the *current* AgentRun workflow.  Used in
        slot-request/release signals so the ProviderProfileManager can correlate
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
            profile_selector=request.profile_selector.model_dump(by_alias=True, exclude_none=True) if hasattr(request, "profile_selector") and request.profile_selector else None,
        )
        profile_id: str = profile["profile_id"]
        runtime_for_profile = self._runtime_id or request.agent_id

        # --- Strategy delegation for defaults (Phase 1) ---
        from moonmind.workflows.temporal.runtime.strategies import get_strategy

        _strategy = get_strategy(runtime_for_profile)

        default_auth = (
            _strategy.default_auth_mode
            if _strategy is not None
            else "api_key"
        )
        # Map legacy strategy auth_mode values to credential_source equivalents.
        default_credential_source = (
            "oauth_volume" if default_auth == "oauth" else "secret_ref"
        )
        credential_source: str = profile.get(
            "credential_source", default_credential_source
        )

        # Build a safe delta-only env_overrides dict for ManagedRuntimeProfile.
        #
        # The launcher (launcher.py) already starts from os.environ and then
        # overlays env_overrides on top.  Therefore env_overrides MUST contain
        # only the profile-specific delta keys — never the full base environment.
        # Passing the entire shaped_env (which inherits os.environ) would cause
        # the ManagedRuntimeProfile validator to reject any sensitive-named env
        # vars that the profile legitimately reads from the ambient environment
        # (e.g. ANTHROPIC_AUTH_TOKEN, MINIMAX_API_KEY).
        #
        # The full shaped_env is still computed below for use in env_keys_count
        # metadata only.
        base_env = {
            k: v for k, v in os.environ.items()
            if not _should_filter_base_env_var(k)
        }
        # Determine if we should use proxy-first token injection instead of 
        # distributing the raw secret_ref downstream.
        tags = profile.get("tags") or []
        is_proxy_first = "proxy-first" in tags
        
        # delta_env_overrides: only the safe profile-specific additions.
        delta_env_overrides: dict[str, str] = {}
        
        if is_proxy_first:
            import time
            from cryptography.fernet import Fernet
            from api_service.core.encryption import get_encryption_key
            
            provider = str(profile.get("provider_id") or "anthropic").strip().lower()
            
            # Mint a synthetic proxy token to authorize internal routes without leaking the true DB secret
            payload_bytes = json.dumps({
                "provider": provider,
                "workflow_id": self._workflow_id,
                "secret_refs": profile.get("secret_refs", {}),
                "exp": time.time() + 3600  # 1 hour expiration for proxied tokens
            }).encode("utf-8")
            
            fernet = Fernet(get_encryption_key().encode("utf-8"))
            proxy_token = "mm-proxy-token:" + fernet.encrypt(payload_bytes).decode("utf-8")
            
            api_url = os.environ.get("MOONMIND_PROXY_URL", "http://moonmind-api:8000/api/v1/proxy")
            
            # Inject standard proxy variables into the delta block directly for the worker
            delta_env_overrides["MOONMIND_PROXY_TOKEN"] = proxy_token
            
            if provider == "anthropic" or provider == "minimax":
                delta_env_overrides["ANTHROPIC_BASE_URL"] = f"{api_url}/{provider}"
                delta_env_overrides["ANTHROPIC_API_KEY"] = proxy_token
                delta_env_overrides["ANTHROPIC_AUTH_TOKEN"] = proxy_token
            elif provider == "openai":
                delta_env_overrides["OPENAI_BASE_URL"] = f"{api_url}/openai/v1"
                delta_env_overrides["OPENAI_API_KEY"] = proxy_token

        # Phase 4: Removed auth_mode branching and shape_environment_* calls.
        # Ensure base environment variables from proxy and runtime overrides are preserved.
        volume_mount_path = profile.get("volume_mount_path")
        if volume_mount_path:
            delta_env_overrides["MANAGED_AUTH_VOLUME_PATH"] = volume_mount_path

        account_label = profile.get("account_label")
        if account_label:
            delta_env_overrides["MANAGED_ACCOUNT_LABEL"] = account_label

        runtime_env_overrides = profile.get("runtime_env_overrides") or {}
        if isinstance(runtime_env_overrides, dict):
            for key, value in runtime_env_overrides.items():
                ks = str(key).strip()
                if not ks:
                    continue
                # Only propagate non-sensitive runtime_env_overrides keys
                # into delta_env_overrides so they reach the launcher.
                is_safe_proxy_var = is_proxy_first and (
                    ks == "MOONMIND_PROXY_TOKEN" or ks.endswith("_BASE_URL")
                )
                if not _should_filter_base_env_var(ks) or is_safe_proxy_var:
                    delta_env_overrides[ks] = str(value) if value is not None else ""

        # We construct shaped_env purely for the metadata count metric, matching the old behavior
        # where it included base_env + delta.
        shaped_env = base_env.copy()
        shaped_env.update(delta_env_overrides)
        passthrough_env_keys = [
            key
            for key in _SECRET_ENV_PASSTHROUGH_KEYS
            if str(os.environ.get(key, "")).strip()
        ]

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

        # NOTE: Slot acquisition is handled by AgentRun before adapter.start()
        # is called.  Do NOT request a slot here — a duplicate request_slot
        # signal triggers the manager loop and can interact with
        # verify_lease_holders to incorrectly free and reassign slots.

        if self._run_launcher is not None:
            runtime_id_for_profile = self._runtime_id or request.agent_id
            runtime_default_model, runtime_default_effort = resolve_runtime_defaults(
                runtime_id_for_profile
            )
            cmd_template = profile.get("command_template")
            if not cmd_template:
                if _strategy is not None:
                    cmd_template = list(_strategy.default_command_template)
                else:
                    cmd_template = [runtime_id_for_profile]

            profile_obj = ManagedRuntimeProfile(
                profile_id=profile_id,
                runtime_id=runtime_id_for_profile,
                provider_id=profile.get("provider_id"),
                provider_label=profile.get("provider_label"),
                credential_source=credential_source,
                runtime_materialization_mode=profile.get(
                    "runtime_materialization_mode"
                ),
                # Use delta_env_overrides (profile-specific additions only), NOT
                # shaped_env (which contains the full base environment).  The
                # launcher already starts from os.environ and overlays
                # env_overrides on top, so passing the full base env here would
                # (a) violate the ManagedRuntimeProfile security validator and
                # (b) be redundant.
                env_overrides=delta_env_overrides,
                passthrough_env_keys=passthrough_env_keys,
                command_template=cmd_template,
                default_model=profile.get("default_model") or runtime_default_model,
                default_effort=profile.get("default_effort") or runtime_default_effort,
                model_overrides=profile.get("model_overrides") or {},
                command_behavior=profile.get("command_behavior") or {},
                env_template=profile.get("env_template") or {},
                file_templates=profile.get("file_templates") or [],
                home_path_overrides=profile.get("home_path_overrides") or {},
                secret_refs=profile.get("secret_refs") or {},
                clear_env_keys=profile.get("clear_env_keys") or [],
            )
            
            # The workspace path is usually managed by the worker, but we can pass it if known
            workspace_path = None
            
            record_dict = await self._run_launcher(
                payload={
                    "run_id": run_id,
                    "workflow_id": self._workflow_id,
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
                "credential_source": credential_source,
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

    async def fetch_result(
        self,
        run_id: str,
        *,
        pr_resolver_expected: bool = False,
    ) -> AgentRunResult:
        """Return result from the run store, falling back to empty if no store.

        Parameters
        ----------
        pr_resolver_expected:
            When ``True``, the adapter checks for a ``pr-resolver`` result
            artifact in the workspace and may override the run result with
            structured failure metadata.  Defaults to ``False`` so that
            autonomous/incidental pr-resolver invocations (e.g. the agent
            deciding on its own to run the skill) do not override the
            actual task result.
        """
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
                if pr_resolver_expected:
                    derived_failure_class, derived_summary = _derive_pr_resolver_failure(
                        record.workspace_path
                    )
                    if derived_failure_class is not None:
                        should_apply_derived = False
                        if record.status == "completed" and failure_class is None:
                            should_apply_derived = True
                        elif (
                            record.status == "failed"
                            and failure_class in {None, "execution_error"}
                            and _is_generic_process_exit_summary(summary)
                        ):
                            should_apply_derived = True
                        if should_apply_derived:
                            failure_class = derived_failure_class
                            if derived_summary:
                                summary = derived_summary
                return AgentRunResult(
                    summary=summary,
                    output_refs=output_refs,
                    failure_class=failure_class,
                    provider_error_code=record.provider_error_code,
                )
        return AgentRunResult()

    async def cancel(self, run_id: str) -> AgentRunStatus:
        """Release slot and return cancelled status."""
        await self.release_slot()
        return AgentRunStatus(
            runId=run_id,
            agentKind="managed",
            agentId="managed",
            status="canceled",
        )

    # ------------------------------------------------------------------
    # Slot management helpers (called from workflow coordination code)
    # ------------------------------------------------------------------

    async def release_slot(self) -> None:
        """Signal ProviderProfileManager to release the active slot lease."""
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
        """Report a 429 rate-limit hit to the ProviderProfileManager (DOC-REQ-009).

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
        execution_profile_ref: str | None,
        runtime_id: str,
        profile_selector: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve execution_profile_ref or profile_selector to a concrete profile dict.

        If ``execution_profile_ref`` is provided, we match exactly by that ID.
        If ``execution_profile_ref`` is None or \"auto\", we evaluate ``profile_selector``.
        If no profile matches, raises ``ProfileResolutionError``.
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
                f"No enabled provider profiles found for runtime_id='{runtime_id}'"
            )

        if execution_profile_ref == "auto":
            execution_profile_ref = None

        if execution_profile_ref is not None:
            for profile in profiles:
                if profile.get("profile_id") == execution_profile_ref:
                    return profile
            raise ProfileResolutionError(
                f"Provider profile '{execution_profile_ref}' not found for "
                f"runtime_id='{runtime_id}' (available profile count: {len(profiles)})"
            )

        # Fallback routing via profile_selector when execution_profile_ref is not specified
        eligible_profiles = []
        for profile in profiles:
            if not profile.get("enabled", True):
                continue
            if profile_selector:
                if profile_selector.get("providerId") and profile.get("provider_id") != profile_selector.get("providerId"):
                    continue
                if profile_selector.get("runtimeMaterializationMode") and profile.get("runtime_materialization_mode") != profile_selector.get("runtimeMaterializationMode"):
                    continue
                
                tags_any = profile_selector.get("tagsAny", [])
                profile_tags = set(profile.get("tags") or [])
                if tags_any and not set(tags_any).intersection(profile_tags):
                    continue
                    
                tags_all = profile_selector.get("tagsAll", [])
                if tags_all and not set(tags_all).issubset(profile_tags):
                    continue

            eligible_profiles.append(profile)

        if not eligible_profiles:
            raise ProfileResolutionError(
                f"No eligible provider profiles found for runtime_id='{runtime_id}' matching selector constraints."
            )

        eligible_profiles.sort(
            key=lambda p: (
                p.get("priority", 100),
                p.get("available_slots", 0),
            ),
            reverse=True,
        )
        return eligible_profiles[0]


__all__ = [
    "ManagedAgentAdapter",
    "ProfileResolutionError",
    "ProfileFetcherFunc",
    "SlotRequestFunc",
    "SlotReleaseFunc",
    "CooldownReportFunc",

]
