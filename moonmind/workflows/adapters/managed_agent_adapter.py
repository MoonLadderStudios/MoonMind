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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from moonmind.schemas.agent_runtime_models import (
    _ALLOWED_MANAGED_LAUNCH_METADATA_KEYS,
    _contains_sensitive_key,
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
    ManagedRuntimeProfile,
    TERMINAL_AGENT_RUN_STATES,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.tasks.runtime_defaults import resolve_runtime_defaults

logger = logging.getLogger(__name__)

# GitHub CLI authentication is required for workflows like pr-resolver.
# Only the *key names* are propagated through workflow/activity payloads; the
# values are injected at launch time by the agent-runtime activity worker.
_SECRET_ENV_PASSTHROUGH_KEYS: tuple[str, ...] = ("GITHUB_TOKEN",)
_RESERVED_MANAGED_LAUNCH_ENV_KEYS: frozenset[str] = frozenset(
    {
        "MOONMIND_EXECUTION_PROFILE_REF",
        "MOONMIND_EXECUTION_PROFILE_RUNTIME",
    }
)

_PR_RESOLVER_RESULT_PATHS: tuple[Path, ...] = (
    Path("var/pr_resolver/result.json"),
    Path("artifacts/pr_resolver_result.json"),
)
_PR_RESOLVER_ATTEMPTS_DIR = Path("var/pr_resolver/attempts")
_PR_RESOLVER_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"failed", "blocked", "attempts_exhausted"}
)
_PR_RESOLVER_BLOCKED_STATUSES: frozenset[str] = frozenset(
    {"blocked", "attempts_exhausted"}
)
_PR_RESOLVER_MERGED_STATUSES: frozenset[str] = frozenset({"merged"})
_PR_RESOLVER_TERMINAL_STATUSES: frozenset[str] = (
    _PR_RESOLVER_FAILURE_STATUSES | _PR_RESOLVER_MERGED_STATUSES
)
_PR_RESOLVER_RESULT_PATH_LIST = ", ".join(
    str(path) for path in _PR_RESOLVER_RESULT_PATHS
)


def _normalize_pr_resolver_text(value: Any) -> str:
    """Return one normalized resolver status candidate."""

    return str(value or "").strip().lower()


def _first_terminal_pr_resolver_status(*values: Any) -> str:
    """Prefer known terminal resolver statuses over informational outcomes."""

    normalized = [_normalize_pr_resolver_text(value) for value in values]
    for candidate in normalized:
        if candidate in _PR_RESOLVER_TERMINAL_STATUSES:
            return candidate
    for candidate in normalized:
        if candidate:
            return candidate
    return ""


def _pr_resolver_status(payload: dict[str, Any]) -> str:
    """Return the normalized terminal status from known resolver artifacts."""

    status = _first_terminal_pr_resolver_status(
        payload.get("status"),
        payload.get("state"),
        payload.get("merge_outcome"),
        payload.get("outcome"),
        payload.get("final_state"),
        payload.get("finalState"),
    )
    if status:
        return status

    final = _pr_resolver_final_payload(payload)
    if final:
        return _first_terminal_pr_resolver_status(
            final.get("state"),
            final.get("status"),
            final.get("merge_outcome"),
            final.get("outcome"),
            final.get("final_state"),
            final.get("finalState"),
        )
    return ""


def _pr_resolver_final_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the nested final-state payload when resolver artifacts include one."""

    final = payload.get("final")
    if isinstance(final, dict):
        return final
    merge_outcome = payload.get("mergeOutcome")
    if isinstance(merge_outcome, dict):
        return merge_outcome
    return {}


def _first_stripped_text(*values: Any) -> str:
    """Return the first non-empty value after string normalization."""

    for value in values:
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return ""


@dataclass(frozen=True, slots=True)
class ManagedProfileLaunchContext:
    """Resolved managed-profile launch context shared across adapters."""

    profile_id: str
    credential_source: str
    delta_env_overrides: dict[str, str]
    passthrough_env_keys: list[str]
    env_keys_count: int

def default_credential_source_for_runtime(runtime_id: str) -> str:
    """Return the deterministic default credential source for one runtime."""

    from moonmind.workflows.temporal.runtime.strategies import get_strategy

    strategy = get_strategy(runtime_id)
    default_auth = strategy.default_auth_mode if strategy is not None else "api_key"
    return "oauth_volume" if default_auth == "oauth" else "secret_ref"

def build_managed_profile_launch_context(
    *,
    profile: dict[str, Any],
    runtime_for_profile: str,
    workflow_id: str,
    default_credential_source: str,
) -> ManagedProfileLaunchContext:
    """Build deterministic launch metadata safe to compute in workflow code."""

    del workflow_id  # Reserved for activity-side shaping.
    credential_source = str(
        profile.get("credential_source") or default_credential_source
    ).strip() or default_credential_source
    delta_env_overrides: dict[str, str] = {}

    volume_mount_path = profile.get("volume_mount_path")
    if volume_mount_path:
        delta_env_overrides["MANAGED_AUTH_VOLUME_PATH"] = str(volume_mount_path)

    account_label = profile.get("account_label")
    if account_label:
        delta_env_overrides["MANAGED_ACCOUNT_LABEL"] = str(account_label)

    profile_id = str(profile.get("profile_id") or "").strip()
    profile_runtime = str(runtime_for_profile or "").strip()

    runtime_env_overrides = profile.get("runtime_env_overrides") or {}
    if isinstance(runtime_env_overrides, dict):
        for key, value in runtime_env_overrides.items():
            ks = str(key).strip()
            if not ks:
                continue
            if ks in _RESERVED_MANAGED_LAUNCH_ENV_KEYS:
                continue
            if _contains_sensitive_key(
                {ks: value},
                allowed_sensitive_keys=_ALLOWED_MANAGED_LAUNCH_METADATA_KEYS,
            ):
                continue
            delta_env_overrides[ks] = str(value) if value is not None else ""

    if profile_id:
        delta_env_overrides["MOONMIND_EXECUTION_PROFILE_REF"] = profile_id
    if profile_runtime:
        delta_env_overrides["MOONMIND_EXECUTION_PROFILE_RUNTIME"] = profile_runtime

    passthrough_env_keys = list(_SECRET_ENV_PASSTHROUGH_KEYS)
    return ManagedProfileLaunchContext(
        profile_id=profile_id,
        credential_source=credential_source,
        delta_env_overrides=delta_env_overrides,
        passthrough_env_keys=passthrough_env_keys,
        env_keys_count=len(delta_env_overrides) + len(passthrough_env_keys),
    )

def _in_workflow_context() -> bool:
    try:
        from temporalio import workflow
    except ImportError:
        return False
    try:
        return workflow.in_workflow()
    except RuntimeError:
        return False

def _generate_run_id() -> str:
    if _in_workflow_context():
        from temporalio import workflow

        return str(workflow.uuid4())
    return str(uuid4())

def _current_time() -> datetime:
    if _in_workflow_context():
        from temporalio import workflow

        return workflow.now()
    return datetime.now(tz=UTC)

def _load_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None

def _parse_payload_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

def _payload_sort_timestamp(path: Path, payload: dict[str, Any]) -> datetime:
    for key in ("timestamp", "finished_at", "finishedAt", "updated_at", "updatedAt"):
        parsed = _parse_payload_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    for key in ("started_at", "startedAt"):
        parsed = _parse_payload_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return datetime.min.replace(tzinfo=UTC)

def _load_pr_resolver_result(workspace_path: str | None) -> dict[str, Any] | None:
    """Load the most recent pr-resolver payload from result/attempt artifacts."""

    workspace = str(workspace_path or "").strip()
    if not workspace:
        return None

    workspace_root = Path(workspace)
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []

    for rel_path in _PR_RESOLVER_RESULT_PATHS:
        result_path = workspace_root / rel_path
        payload = _load_json_dict(result_path)
        if payload is not None:
            candidates.append(
                (_payload_sort_timestamp(result_path, payload), result_path, payload)
            )

    attempts_dir = workspace_root / _PR_RESOLVER_ATTEMPTS_DIR
    try:
        attempt_paths = sorted(attempts_dir.glob("*.json"))
    except OSError:
        attempt_paths = []
    for attempt_path in attempt_paths:
        payload = _load_json_dict(attempt_path)
        if payload is not None:
            candidates.append(
                (_payload_sort_timestamp(attempt_path, payload), attempt_path, payload)
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], str(item[1])))
    return candidates[-1][2]

# Type aliases for async signal callables injected by the caller/workflow.
ProfileFetcherFunc = Callable[..., Awaitable[dict[str, Any]]]
SlotRequestFunc = Callable[..., Awaitable[Any]]
SlotReleaseFunc = Callable[..., Awaitable[Any]]
CooldownReportFunc = Callable[..., Awaitable[Any]]
RunLauncherFunc = Callable[..., Awaitable[Any]]
LaunchContextBuilderFunc = Callable[..., Awaitable[ManagedProfileLaunchContext | dict[str, Any]]]

def _derive_pr_resolver_failure(
    workspace_path: str | None,
) -> tuple[str | None, str | None]:
    """Return failure metadata from pr-resolver artifacts when present."""
    payload = _load_pr_resolver_result(workspace_path)
    if payload is None:
        return (
            "user_error",
            (
                "pr-resolver result artifact missing; expected one of "
                f"{_PR_RESOLVER_RESULT_PATH_LIST}"
            ),
        )

    status = _pr_resolver_status(payload)
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

def _derive_pr_resolver_metadata(workspace_path: str | None) -> dict[str, Any]:
    """Return compact metadata from pr-resolver artifacts for parent workflows."""
    payload = _load_pr_resolver_result(workspace_path)
    if payload is None:
        return {}

    status = _pr_resolver_status(payload)
    reason = str(payload.get("final_reason") or payload.get("reason") or "").strip()
    metadata: dict[str, Any] = {}
    explicit_disposition = str(payload.get("mergeAutomationDisposition") or "").strip()
    if explicit_disposition:
        metadata["mergeAutomationDisposition"] = explicit_disposition
    elif status in _PR_RESOLVER_MERGED_STATUSES:
        metadata["mergeAutomationDisposition"] = (
            "already_merged" if reason == "already_merged" else "merged"
        )
    final = _pr_resolver_final_payload(payload)
    head_sha = _first_stripped_text(
        payload.get("headSha"),
        payload.get("head_sha"),
        payload.get("latestHeadSha"),
        payload.get("latest_head_sha"),
        payload.get("headOid"),
        payload.get("head_oid"),
        payload.get("headCommit"),
        payload.get("head_commit"),
        final.get("headRefOid"),
        final.get("headSha"),
        final.get("head_sha"),
        final.get("latestHeadSha"),
        final.get("latest_head_sha"),
        final.get("headOid"),
        final.get("head_oid"),
        final.get("headCommit"),
        final.get("head_commit"),
    )
    if head_sha:
        metadata["headSha"] = head_sha
    return metadata

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
        launch_context_builder: LaunchContextBuilderFunc | None = None,
    ) -> None:
        self._fetch_profiles = profile_fetcher
        self._request_slot = slot_requester
        self._release_slot = slot_releaser
        self._report_cooldown = cooldown_reporter
        self._workflow_id = workflow_id
        self._runtime_id = runtime_id
        self._run_store = run_store
        self._run_launcher = run_launcher
        self._launch_context_builder = launch_context_builder
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
        default_credential_source = default_credential_source_for_runtime(
            runtime_for_profile
        )
        if self._launch_context_builder is not None:
            built_context = await self._launch_context_builder(
                profile=profile,
                runtime_for_profile=runtime_for_profile,
                workflow_id=self._workflow_id,
                default_credential_source=default_credential_source,
            )
            launch_context = (
                built_context
                if isinstance(built_context, ManagedProfileLaunchContext)
                else ManagedProfileLaunchContext(**built_context)
            )
        else:
            launch_context = build_managed_profile_launch_context(
                profile=profile,
                runtime_for_profile=runtime_for_profile,
                workflow_id=self._workflow_id,
                default_credential_source=default_credential_source,
            )
        credential_source = launch_context.credential_source
        delta_env_overrides = launch_context.delta_env_overrides
        passthrough_env_keys = launch_context.passthrough_env_keys

        # Persist only the profile_id reference — never raw credentials
        # (DOC-REQ-008 / constitution security rule).
        self._active_profile_id = launch_context.profile_id
        
        run_id = _generate_run_id()
        started_at = _current_time()

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
                started_at=started_at,
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
                startedAt=started_at,
                metadata={
                "profile_id": launch_context.profile_id,
                "credential_source": credential_source,
                "env_keys_count": launch_context.env_keys_count,
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
                for ref in (
                    record.log_artifact_ref,
                    record.stdout_artifact_ref,
                    record.stderr_artifact_ref,
                    record.merged_log_artifact_ref,
                    record.diagnostics_ref,
                    record.observability_events_ref,
                ):
                    if ref and ref not in output_refs:
                        output_refs.append(ref)
                summary = record.error_message or f"Completed with status {record.status}"
                failure_class = record.failure_class
                metadata = _derive_pr_resolver_metadata(record.workspace_path)
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
                    metadata=metadata,
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

        normalized_selector: dict[str, Any] | None = None
        if profile_selector:
            selector_payload: dict[str, Any] = {}
            for key, value in profile_selector.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, list) and not value:
                    continue
                selector_payload[key] = value
            if selector_payload:
                normalized_selector = selector_payload

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
            if normalized_selector:
                if normalized_selector.get("providerId") and profile.get("provider_id") != normalized_selector.get("providerId"):
                    continue
                if normalized_selector.get("runtimeMaterializationMode") and profile.get("runtime_materialization_mode") != normalized_selector.get("runtimeMaterializationMode"):
                    continue

                tags_any = normalized_selector.get("tagsAny", [])
                profile_tags = set(profile.get("tags") or [])
                if tags_any and not set(tags_any).intersection(profile_tags):
                    continue

                tags_all = normalized_selector.get("tagsAll", [])
                if tags_all and not set(tags_all).issubset(profile_tags):
                    continue

            eligible_profiles.append(profile)

        if not eligible_profiles:
            raise ProfileResolutionError(
                f"No eligible provider profiles found for runtime_id='{runtime_id}' matching selector constraints."
            )

        if not normalized_selector:
            default_profiles = [
                profile for profile in eligible_profiles if profile.get("is_default")
            ]
            if len(eligible_profiles) == 1:
                return eligible_profiles[0]
            if len(default_profiles) == 1:
                return default_profiles[0]
            raise ProfileResolutionError(
                f"No default provider profile configured for runtime_id='{runtime_id}'"
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
    "ManagedProfileLaunchContext",
    "ProfileResolutionError",
    "ProfileFetcherFunc",
    "SlotRequestFunc",
    "SlotReleaseFunc",
    "CooldownReportFunc",
    "build_managed_profile_launch_context",

]
