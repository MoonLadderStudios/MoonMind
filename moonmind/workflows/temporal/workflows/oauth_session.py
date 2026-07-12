"""OAuth session orchestrator workflow.

Manages the lifecycle of a single OAuth auth session — from volume
provisioning through runner readiness to verification and profile
registration.

Workflow ID convention: ``oauth-session:<session_id>``
  e.g. ``oauth-session:oas_abc123def456``

Design reference:
  - docs/ManagedAgents/OAuthTerminal.md
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

WORKFLOW_NAME = "MoonMind.OAuthSession"
ACTIVITY_TASK_QUEUE = "mm.activity.artifacts"
RUNNER_ACTIVITY_TASK_QUEUE = "mm.activity.agent_runtime"

logger = logging.getLogger(__name__)
OAUTH_CREDENTIAL_MAINTENANCE_LEASE_PATCH = (
    "oauth-session-credential-maintenance-lease-v1"
)

# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


class OAuthSessionInput(TypedDict, total=False):
    """Input payload for starting an OAuth session workflow."""

    session_id: str
    runtime_id: str
    profile_id: str
    volume_ref: str
    volume_mount_path: str
    requested_by_user_id: str
    profile_settings: dict[str, Any]
    session_transport: str


class OAuthSessionOutput(TypedDict):
    session_id: str
    status: str
    failure_reason: Optional[str]


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

# Default session TTL — sessions auto-expire after this duration.
_DEFAULT_SESSION_TTL_SECONDS = 1800  # 30 minutes
_SECRET_ASSIGNMENT_PATTERN = re.compile(r"(?i)(token|password|secret|api[_-]?key)=\S+")


def _redact_workflow_failure(prefix: str, error: object) -> str:
    """Return a bounded workflow-safe failure reason."""
    message = str(error)
    if _SECRET_ASSIGNMENT_PATTERN.search(message):
        return f"{prefix}: redacted provider output"
    return f"{prefix}: {message}"


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindOAuthSessionWorkflow:
    """Orchestrates a single OAuth auth session lifecycle.

    Flow:
      1. Validate input and resolve provider spec
      2. Transition to ``starting``
      3. Ensure Docker volume exists
      4. Transition to ``bridge_ready`` / ``awaiting_user``
      5. Wait for finalize signal or session expiry
      6. On finalize: verify volume, register profile, mark ``succeeded``
      7. On expiry/cancel: mark ``expired`` / ``cancelled``
    """

    def __init__(self) -> None:
        self._session_id: str = ""
        self._finalize_requested: bool = False
        self._api_finalize_succeeded: bool = False
        self._cancel_requested: bool = False
        self._failure_requested: bool = False
        self._failure_reason: str = ""
        self._container_name: str = ""
        self._terminal_connected: bool = False
        self._maintenance_lease_acquired: bool = False
        self._maintenance_profile_id: str = ""
        self._maintenance_runtime_id: str = ""
        self._maintenance_lease_id: str = ""
        self._maintenance_purpose: str = "oauth_connect"

    # -- Signals ---------------------------------------------------------------

    @workflow.signal
    def finalize(self) -> None:
        """User or API triggered finalize — verify and register profile."""
        self._finalize_requested = True

    @workflow.signal
    def api_finalize_succeeded(self) -> None:
        """API has already verified credentials and registered the profile."""
        self._api_finalize_succeeded = True

    @workflow.signal
    def cancel(self) -> None:
        """Cancel the session."""
        self._cancel_requested = True

    @workflow.signal
    def fail(self, reason: str) -> None:
        """Externally observed terminal failure."""
        self._failure_requested = True
        self._failure_reason = reason or "OAuth terminal session failed"

    @workflow.signal
    def terminal_connected(self) -> None:
        """Browser has attached to the terminal bridge."""
        self._terminal_connected = True

    @workflow.signal
    def terminal_disconnected(self) -> None:
        """Browser has detached from the terminal bridge."""
        self._terminal_connected = False

    # -- Queries ---------------------------------------------------------------

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "finalize_requested": self._finalize_requested,
            "api_finalize_succeeded": self._api_finalize_succeeded,
            "cancel_requested": self._cancel_requested,
            "failure_requested": self._failure_requested,
            "failure_reason": self._failure_reason,
            # Mirrors cancel_requested for consistency with run workflows
            "canceling": self._cancel_requested,
            "container_name": self._container_name,
            "terminal_connected": self._terminal_connected,
        }

    # -- Main workflow ---------------------------------------------------------

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> OAuthSessionOutput:
        self._session_id = input_payload.get("session_id", "")
        runtime_id = input_payload.get("runtime_id", "")
        profile_id = input_payload.get("profile_id", "")
        volume_ref = input_payload.get("volume_ref", "")
        volume_mount_path = input_payload.get("volume_mount_path", "")
        raw_session_transport = input_payload.get("session_transport")
        session_transport = (
            "moonmind_pty_ws"
            if raw_session_transport is None
            else str(raw_session_transport).strip() or "none"
        )
        session_ttl = _DEFAULT_SESSION_TTL_SECONDS

        if not self._session_id or not runtime_id:
            raise exceptions.ApplicationError(
                "session_id and runtime_id are required", non_retryable=True
            )

        # Step 1: Transition to starting
        await self._update_status("starting")

        if runtime_id == "codex_cli" and (
            not str(volume_ref or "").strip()
            or not str(volume_mount_path or "").strip()
        ):
            failure_reason = (
                "volume_ref and volume_mount_path are required for Codex OAuth sessions"
            )
            await self._mark_failed(failure_reason)
            return OAuthSessionOutput(
                session_id=self._session_id,
                status="failed",
                failure_reason=failure_reason,
            )

        if profile_id and workflow.patched(OAUTH_CREDENTIAL_MAINTENANCE_LEASE_PATCH):
            try:
                await self._acquire_maintenance_lease(
                    runtime_id=runtime_id,
                    profile_id=profile_id,
                    purpose=str(
                        input_payload.get("maintenance_purpose") or "oauth_connect"
                    ),
                )
            except Exception as exc:
                failure_reason = _redact_workflow_failure(
                    "Failed to acquire credential maintenance lease", exc
                )
                await self._mark_failed(failure_reason)
                return OAuthSessionOutput(
                    session_id=self._session_id,
                    status="failed",
                    failure_reason=failure_reason,
                )
            try:
                await workflow.execute_activity(
                    "oauth_session.prepare_credential_maintenance",
                    {
                        "session_id": self._session_id,
                        "runtime_id": runtime_id,
                        "profile_id": profile_id,
                        "purpose": self._maintenance_purpose,
                    },
                    task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=2), maximum_attempts=3
                    ),
                )
            except Exception as exc:
                failure_reason = _redact_workflow_failure(
                    "Failed to drain credential consumers", exc
                )
                await self._mark_failed(failure_reason)
                return await self._finish(
                    OAuthSessionOutput(
                        session_id=self._session_id,
                        status="failed",
                        failure_reason=failure_reason,
                    )
                )

        # Step 2: Ensure the Docker volume exists
        try:
            await workflow.execute_activity(
                "oauth_session.ensure_volume",
                {"session_id": self._session_id, "volume_ref": volume_ref},
                task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_attempts=3,
                ),
            )
        except Exception as exc:
            await self._mark_failed(f"Failed to ensure volume: {exc}")
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="failed",
                    failure_reason=f"Volume provisioning failed: {exc}",
                )
            )

        # Step 3: Launch auth runner only when an interactive transport is enabled.
        if session_transport != "none":
            try:
                runner_result = await workflow.execute_activity(
                    "oauth_session.start_auth_runner",
                    {
                        "session_id": self._session_id,
                        "runtime_id": runtime_id,
                        "volume_ref": volume_ref,
                        "volume_mount_path": volume_mount_path,
                        "session_transport": session_transport,
                        "session_ttl": session_ttl,
                    },
                    task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=3),
                        maximum_attempts=2,
                    ),
                )
                self._container_name = runner_result.get("container_name", "")
                terminal_session_id = runner_result.get("terminal_session_id", "")
                terminal_bridge_id = runner_result.get("terminal_bridge_id", "")

                # Store extracted terminal IDs in DB
                await workflow.execute_activity(
                    "oauth_session.update_terminal_session",
                    {
                        "session_id": self._session_id,
                        "terminal_session_id": terminal_session_id,
                        "terminal_bridge_id": terminal_bridge_id,
                        "container_name": self._container_name,
                        "session_transport": runner_result.get(
                            "session_transport",
                            session_transport,
                        ),
                        "expires_at": runner_result.get("expires_at"),
                    },
                    task_queue=ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_attempts=3,
                    ),
                )
            except Exception as exc:
                failure_reason = _redact_workflow_failure(
                    "Failed to start auth runner", exc
                )
                output_reason = _redact_workflow_failure(
                    "Auth runner launch failed", exc
                )
                await self._mark_failed(failure_reason)
                return await self._finish(
                    OAuthSessionOutput(
                        session_id=self._session_id,
                        status="failed",
                        failure_reason=output_reason,
                    )
                )

        # Step 4: Transition to bridge_ready then awaiting_user
        await self._update_status("bridge_ready")
        await self._update_status("awaiting_user")

        # Step 5: Wait for finalize, cancel, or session timeout
        try:
            await workflow.wait_condition(
                lambda: (
                    self._finalize_requested
                    or self._api_finalize_succeeded
                    or self._cancel_requested
                    or self._failure_requested
                ),
                timeout=timedelta(seconds=session_ttl),
            )
        except TimeoutError:
            await self._update_status("expired")
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="expired",
                    failure_reason="Session timed out before finalization",
                )
            )

        if self._cancel_requested:
            await self._update_status("cancelled")
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="cancelled",
                    failure_reason=None,
                )
            )

        if self._failure_requested:
            await self._mark_failed(self._failure_reason)
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="failed",
                    failure_reason=self._failure_reason,
                )
            )

        if self._api_finalize_succeeded:
            await self._update_status("succeeded")
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="succeeded",
                    failure_reason=None,
                ),
                revalidate_bound_host=True,
            )

        # Step 6: Finalize — verify and register
        await self._update_status("verifying")

        try:
            verify_result = await workflow.execute_activity(
                "oauth_session.verify_cli_fingerprint",
                {
                    "session_id": self._session_id,
                    "runtime_id": runtime_id,
                    "volume_ref": volume_ref,
                    "volume_mount_path": volume_mount_path,
                },
                task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_attempts=2,
                ),
            )

            if verify_result.get("verified"):
                await self._update_status("registering_profile")
                await workflow.execute_activity(
                    "oauth_session.register_profile",
                    {
                        "session_id": self._session_id,
                        "verification": verify_result,
                    },
                    task_queue=ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_attempts=3,
                    ),
                )

                await self._update_status("succeeded")

                return await self._finish(
                    OAuthSessionOutput(
                        session_id=self._session_id,
                        status="succeeded",
                        failure_reason=None,
                    ),
                    revalidate_bound_host=True,
                )
            else:
                reason = verify_result.get("reason", "unknown")
                await self._mark_failed(f"Volume verification failed: {reason}")
                return await self._finish(
                    OAuthSessionOutput(
                        session_id=self._session_id,
                        status="failed",
                        failure_reason=f"Volume verification failed: {reason}",
                    )
                )
        except Exception as exc:
            await self._mark_failed(f"Finalize sequence failed: {exc}")
            return await self._finish(
                OAuthSessionOutput(
                    session_id=self._session_id,
                    status="failed",
                    failure_reason=f"Finalize sequence failed: {exc}",
                )
            )

    # -- Internal helpers ------------------------------------------------------

    async def _acquire_maintenance_lease(
        self, *, runtime_id: str, profile_id: str, purpose: str
    ) -> None:
        if not profile_id:
            raise exceptions.ApplicationError(
                "profile_id is required for OAuth credential maintenance",
                non_retryable=True,
            )
        await workflow.execute_activity(
            "provider_profile.ensure_manager",
            {"runtime_id": runtime_id},
            task_queue=ACTIVITY_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1), maximum_attempts=3
            ),
        )
        owner_id = f"oauth-session:{self._session_id}"
        manager = workflow.get_external_workflow_handle(
            f"provider-profile-manager:{runtime_id}"
        )
        result = await manager.execute_update(
            "AcquireCredentialMaintenanceLease",
            {
                "requester_workflow_id": owner_id,
                "owner_id": owner_id,
                "runtime_id": runtime_id,
                "execution_profile_ref": profile_id,
                "purpose": purpose,
                "metadata": {
                    "workflowId": workflow.info().workflow_id,
                    "oauthSessionId": self._session_id,
                    "ownerIsWorkflow": True,
                },
            },
        )
        self._maintenance_lease_acquired = True
        self._maintenance_profile_id = profile_id
        self._maintenance_runtime_id = runtime_id
        self._maintenance_lease_id = str(result.get("lease_id") or owner_id)
        self._maintenance_purpose = purpose

    async def _release_maintenance_lease(self) -> None:
        if not self._maintenance_lease_acquired:
            return
        owner_id = f"oauth-session:{self._session_id}"
        manager = workflow.get_external_workflow_handle(
            f"provider-profile-manager:{self._maintenance_runtime_id}"
        )
        await manager.signal(
            "release_slot",
            {
                "requester_workflow_id": owner_id,
                "owner_id": owner_id,
                "runtime_id": self._maintenance_runtime_id,
                "profile_id": self._maintenance_profile_id,
                "lease_id": self._maintenance_lease_id,
                "purpose": self._maintenance_purpose,
            },
        )
        self._maintenance_lease_acquired = False

    async def _finish(
        self,
        output: OAuthSessionOutput,
        *,
        revalidate_bound_host: bool = False,
    ) -> OAuthSessionOutput:
        """Stop the credential writer before releasing shared capacity."""

        await self._stop_auth_runner()
        safe_to_release = True
        if revalidate_bound_host and self._maintenance_lease_acquired:
            try:
                await workflow.execute_activity(
                    "oauth_session.revalidate_bound_host",
                    {
                        "session_id": self._session_id,
                        "runtime_id": self._maintenance_runtime_id,
                        "profile_id": self._maintenance_profile_id,
                        "provider_lease_id": self._maintenance_lease_id,
                    },
                    task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=3), maximum_attempts=3
                    ),
                )
            except Exception as exc:
                safe_to_release = False
                failure_reason = _redact_workflow_failure(
                    "Bound host credential preflight failed", exc
                )
                await self._mark_failed(failure_reason)
                output = OAuthSessionOutput(
                    session_id=self._session_id,
                    status="failed",
                    failure_reason=failure_reason,
                )
        if safe_to_release:
            await self._release_maintenance_lease()
        return output

    async def _update_status(self, status: str) -> None:
        """Update the session status in the database via activity."""
        try:
            await workflow.execute_activity(
                "oauth_session.update_status",
                {"session_id": self._session_id, "status": status},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_attempts=3,
                ),
            )
        except Exception:
            workflow.logger.warning(
                "Failed to update session %s status to %s",
                self._session_id,
                status,
                exc_info=True,
            )

    async def _stop_auth_runner(self) -> None:
        """Stop the auth runner container (best-effort)."""
        try:
            await workflow.execute_activity(
                "oauth_session.stop_auth_runner",
                {
                    "session_id": self._session_id,
                    "container_name": self._container_name,
                },
                task_queue=RUNNER_ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_attempts=2,
                ),
            )
        except Exception:
            workflow.logger.warning(
                "Failed to stop auth runner for session %s",
                self._session_id,
                exc_info=True,
            )

    async def _mark_failed(self, reason: str) -> None:
        """Mark the session as failed with a reason."""
        try:
            await workflow.execute_activity(
                "oauth_session.mark_failed",
                {"session_id": self._session_id, "reason": reason},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_attempts=3,
                ),
            )
        except Exception:
            workflow.logger.warning(
                "Failed to mark session %s as failed",
                self._session_id,
                exc_info=True,
            )
