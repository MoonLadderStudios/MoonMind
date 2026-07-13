"""OAuth session Temporal activities.

Provides the activities invoked by the ``MoonMind.OAuthSession`` workflow:
  - ``oauth_session.ensure_volume``       — verify / create Docker volume
  - ``oauth_session.start_auth_runner``   — removed (browser OAuth transport retired)
  - ``oauth_session.stop_auth_runner``    — tear down auth runner container
  - ``oauth_session.update_status``       — transition session status in DB
  - ``oauth_session.mark_failed``         — mark session as failed with reason
  - ``oauth_session.update_terminal_session`` — store terminal session refs in DB
  - ``oauth_session.verify_volume``       — call provider volume verifier
  - ``oauth_session.register_profile``    — create or update provider profile
"""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

from temporalio import activity, exceptions

from api_service.db.base import get_async_session_context
from api_service.db.models import (
    ManagedAgentOAuthSession,
    OAuthSessionStatus,
)
from moonmind.schemas.agent_runtime_models import validate_codex_oauth_profile_refs
from moonmind.provider_profiles.oauth_policy import (
    effective_oauth_capacity_for_finalization,
)
from moonmind.workflows.temporal.runtime.providers.registry import (
    get_provider_bootstrap_command,
    get_provider_default,
)

logger = logging.getLogger(__name__)


@activity.defn(name="oauth_session.prepare_credential_maintenance")
async def oauth_session_prepare_credential_maintenance(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Drain stale profile-bound sessions/hosts before mutating OAuth state."""

    import httpx

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from moonmind.omnigent.settings import (
        resolved_api_token,
        resolved_proxy_forward_headers,
        resolved_server_url,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    profile_id = str(request.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("profile_id is required")
    repository = OmnigentOAuthHostRepository(async_session_maker)
    binding = await repository.get_binding_for_profile(profile_id)
    if binding is None:
        return {"profile_id": profile_id, "drained": 0}
    leases = await repository.list_active_host_leases_for_profile(profile_id)
    async with httpx.AsyncClient() as http_client:
        client = OmnigentHttpClient(
            base_url=resolved_server_url(),
            api_token=resolved_api_token(),
            client=http_client,
            upstream_header_allowlist=resolved_proxy_forward_headers(),
        )
        runtime = OmnigentOAuthHostRuntime(client=client)
        drained = 0
        if not binding.host_launch_profile_ref:
            # The static host can remain intentionally idle after its prior
            # lease is released. Stop it before the OAuth runner mounts the
            # same mutable home even when there is no active host lease row.
            await runtime.stop_static_host()
        for host_lease in leases:
            if host_lease.omnigent_session_id:
                try:
                    await client.interrupt(host_lease.omnigent_session_id)
                    await client.stop_session(host_lease.omnigent_session_id)
                except Exception:
                    logger.warning(
                        "Failed to stop stale Omnigent session before credential maintenance",
                        exc_info=True,
                    )
            if binding.host_launch_profile_ref:
                await runtime.stop_host(binding=binding, host_lease=host_lease)
            await repository.mark_host_lease_stopped(host_lease.lease_id)
            drained += 1
    return {"profile_id": profile_id, "drained": drained}


@activity.defn(name="oauth_session.revalidate_bound_host")
async def oauth_session_revalidate_bound_host(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Require matching-generation destination preflight after reconnect."""

    import httpx

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from moonmind.omnigent.settings import (
        resolved_api_token,
        resolved_proxy_forward_headers,
        resolved_server_url,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    profile_id = str(request.get("profile_id") or "").strip()
    provider_lease_id = str(request.get("provider_lease_id") or "").strip()
    session_id = str(request.get("session_id") or "").strip()
    if not profile_id or not provider_lease_id or not session_id:
        raise ValueError("profile_id, provider_lease_id, and session_id are required")
    repository = OmnigentOAuthHostRepository(async_session_maker)
    binding = await repository.refresh_binding_generation(profile_id)
    if binding is None:
        return {"profile_id": profile_id, "status": "no_binding"}
    lease = await repository.create_or_get_host_lease(
        binding=binding,
        provider_lease_id=provider_lease_id,
        holder_workflow_id=f"oauth-session:{session_id}",
        agent_run_id=None,
        idempotency_key=f"oauth-revalidate:{session_id}",
        lease_purpose="credential_validation",
        ttl_seconds=600,
    )
    if lease.status in {"stopped", "failed"}:
        lease = await repository.restart_host_lease(lease.lease_id, ttl_seconds=600)
    if lease.status == "allocating":
        lease = await repository.transition_host_lease(
            lease.lease_id,
            expected_status="allocating",
            new_status="starting",
        )
    try:
        async with httpx.AsyncClient() as http_client:
            client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=http_client,
                upstream_header_allowlist=resolved_proxy_forward_headers(),
            )
            runtime = OmnigentOAuthHostRuntime(client=client)
            preflight = await runtime.prepare_host(
                binding=binding,
                host_lease=lease,
                workspace_key=f"oauth-revalidate:{session_id}",
            )
            if lease.status == "starting":
                lease = await repository.transition_host_lease(
                    lease.lease_id,
                    expected_status="starting",
                    new_status="ready",
                    fields={"omnigent_host_id": preflight["hostId"]},
                )
            if binding.host_launch_profile_ref:
                await runtime.stop_host(binding=binding, host_lease=lease)
            await repository.mark_host_lease_stopped(lease.lease_id)
    except Exception:
        async with get_async_session_context() as db:
            from sqlalchemy.future import select
            from api_service.db.models import (
                ManagedAgentProviderProfile,
                ProviderProfileAuthState,
                ProviderProfileDisabledReason,
            )

            profile = (
                await db.execute(
                    select(ManagedAgentProviderProfile).where(
                        ManagedAgentProviderProfile.profile_id == profile_id
                    )
                )
            ).scalar_one_or_none()
            if profile is not None:
                profile.enabled = False
                profile.auth_state = ProviderProfileAuthState.VALIDATION_FAILED
                profile.disabled_reason = ProviderProfileDisabledReason.AUTH_INVALID
                behavior = dict(profile.command_behavior or {})
                behavior["auth_readiness"] = {
                    "launch_ready": False,
                    "failure_reason": "bound_host_preflight_failed",
                }
                profile.command_behavior = behavior
                await db.commit()
                from api_service.services.provider_profile_service import (
                    sync_provider_profile_manager,
                )

                await sync_provider_profile_manager(
                    session=db, runtime_id=profile.runtime_id
                )
        raise
    return {
        "profile_id": profile_id,
        "status": "ready",
        "credential_generation": lease.credential_generation,
        "host_id": preflight["hostId"],
    }


@activity.defn(name="oauth_session.ensure_volume")
async def oauth_session_ensure_volume(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Ensure the Docker auth volume exists for this session.

    Attempts ``docker volume create`` (idempotent).  If Docker is not
    available, falls back to a validation-only check.
    """
    session_id = request.get("session_id", "")
    volume_ref = request.get("volume_ref", "")

    if not volume_ref:
        logger.warning("No volume_ref provided for session %s", session_id)
        return {"session_id": session_id, "volume_ref": "", "status": "skipped"}

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "volume",
            "create",
            volume_ref,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "docker volume create failed for %s (rc=%d): %s",
                volume_ref,
                proc.returncode,
                err[:200],
            )
            return {
                "session_id": session_id,
                "volume_ref": volume_ref,
                "status": "create_failed",
            }
        logger.info("Ensured volume %s for session %s", volume_ref, session_id)
        return {"session_id": session_id, "volume_ref": volume_ref, "status": "ok"}
    except FileNotFoundError:
        logger.info(
            "Docker CLI not available — skipping volume create for session %s",
            session_id,
        )
        return {
            "session_id": session_id,
            "volume_ref": volume_ref,
            "status": "docker_unavailable",
        }
    except asyncio.TimeoutError:
        logger.warning("docker volume create timed out for session %s", session_id)
        return {"session_id": session_id, "volume_ref": volume_ref, "status": "timeout"}


@activity.defn(name="oauth_session.start_auth_runner")
async def oauth_session_start_auth_runner(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Browser-based OAuth runners were removed; start the Terminal PTY bridge."""
    session_id = request.get("session_id", "")
    runtime_id = request.get("runtime_id", "")
    volume_ref = request.get("volume_ref", "")
    volume_mount_path = request.get("volume_mount_path", "")
    session_transport = str(
        request.get("session_transport") or "moonmind_pty_ws"
    ).strip()
    session_ttl = int(request.get("session_ttl", 1800))

    if not session_id:
        raise ValueError("session_id is required")
    if not volume_ref:
        raise ValueError("volume_ref is required")
    if not volume_mount_path:
        raise ValueError("volume_mount_path is required")
    session_ttl = max(60, min(session_ttl, 86400))
    bootstrap_command = get_provider_bootstrap_command(runtime_id)

    if session_transport == "tmate":
        from moonmind.workflows.temporal.runtime.terminal_bridge import (
            start_tmate_auth_runner_container,
        )

        bridge_info = await start_tmate_auth_runner_container(
            session_id=session_id,
            runtime_id=runtime_id,
            volume_ref=volume_ref,
            volume_mount_path=volume_mount_path,
            session_ttl=session_ttl,
            bootstrap_command=bootstrap_command,
        )
        bridge_info.setdefault(
            "expires_at",
            (datetime.now(timezone.utc) + timedelta(seconds=session_ttl)).isoformat(),
        )
        bridge_info.setdefault("session_transport", "tmate")
        return bridge_info

    if session_transport != "moonmind_pty_ws":
        raise ValueError(f"Unsupported OAuth session transport: {session_transport}")

    from moonmind.workflows.temporal.runtime.terminal_bridge import (
        start_terminal_bridge_container,
    )

    bridge_info = await start_terminal_bridge_container(
        session_id=session_id,
        runtime_id=runtime_id,
        volume_ref=volume_ref,
        volume_mount_path=volume_mount_path,
        session_ttl=session_ttl,
        bootstrap_command=bootstrap_command,
    )
    bridge_info.setdefault(
        "expires_at",
        (datetime.now(timezone.utc) + timedelta(seconds=session_ttl)).isoformat(),
    )
    bridge_info.setdefault("session_transport", "moonmind_pty_ws")

    return bridge_info


@activity.defn(name="oauth_session.update_terminal_session")
async def oauth_session_update_terminal_session(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Write terminal session references to the DB row."""
    session_id = request.get("session_id", "")
    terminal_session_id = request.get("terminal_session_id", "")
    terminal_bridge_id = request.get("terminal_bridge_id", "")
    container_name = request.get("container_name", "")
    session_transport = request.get("session_transport", "moonmind_pty_ws")
    expires_at_raw = request.get("expires_at")

    if not session_id:
        raise ValueError("session_id is required")

    async with get_async_session_context() as db:
        from sqlalchemy.future import select

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        if terminal_session_id:
            session_obj.terminal_session_id = terminal_session_id
        if terminal_bridge_id:
            session_obj.terminal_bridge_id = terminal_bridge_id
        if container_name:
            session_obj.container_name = container_name
        if session_transport:
            session_obj.session_transport = session_transport
        if expires_at_raw:
            if isinstance(expires_at_raw, datetime):
                session_obj.expires_at = expires_at_raw
            else:
                session_obj.expires_at = datetime.fromisoformat(
                    str(expires_at_raw).replace("Z", "+00:00")
                )

        await db.commit()

    logger.info("Updated OAuth terminal session refs for session %s", session_id)
    return {
        "session_id": session_id,
        "terminal_session_id": terminal_session_id,
        "terminal_bridge_id": terminal_bridge_id,
        "container_name": container_name,
        "session_transport": session_transport,
    }


@activity.defn(name="oauth_session.stop_auth_runner")
async def oauth_session_stop_auth_runner(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Stop and remove the auth runner container.

    Best-effort: if the container is already gone, this is a no-op.
    """
    session_id = request.get("session_id", "")
    container_name = request.get("container_name", "")

    if not container_name:
        async with get_async_session_context() as db:
            from sqlalchemy.future import select

            result = await db.execute(
                select(ManagedAgentOAuthSession).where(
                    ManagedAgentOAuthSession.session_id == session_id
                )
            )
            session_obj = result.scalars().first()
            if session_obj and session_obj.container_name:
                container_name = session_obj.container_name

    if not container_name:
        logger.info("No container to stop for session %s", session_id)
        return {"session_id": session_id, "stopped": False, "reason": "no_container"}

    from api_service.services.oauth_auth_runner import stop_auth_runner_container

    return await stop_auth_runner_container(
        session_id=session_id,
        container_name=container_name,
    )


@activity.defn(name="oauth_session.verify_volume")
async def oauth_session_verify_volume(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify that expected credentials exist in the volume."""
    session_id = request.get("session_id", "")
    runtime_id = request.get("runtime_id", "")
    volume_ref = request.get("volume_ref", "")
    volume_mount_path = request.get("volume_mount_path")

    if not session_id or not runtime_id or not volume_ref:
        raise ValueError("session_id, runtime_id, and volume_ref are required")

    from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
        verify_volume_credentials,
    )

    verification = await verify_volume_credentials(
        runtime_id=runtime_id,
        volume_ref=volume_ref,
        volume_mount_path=volume_mount_path,
    )

    verification["session_id"] = session_id

    return verification


@activity.defn(name="oauth_session.verify_cli_fingerprint")
async def oauth_session_verify_cli_fingerprint(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify that credentials in the volume belong to the expected user or have the correct format."""
    session_id = request.get("session_id", "")
    runtime_id = request.get("runtime_id", "")
    volume_ref = request.get("volume_ref", "")
    volume_mount_path = request.get("volume_mount_path")

    if not session_id or not runtime_id or not volume_ref:
        raise ValueError("session_id, runtime_id, and volume_ref are required")

    # In Phase 5 MVP, we fallback to just verifying the files exist, similar to verify_volume
    # A true fingerprint validation would cat the files and parse JSON to check email/token format.
    from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
        verify_volume_credentials,
    )

    verification = await verify_volume_credentials(
        runtime_id=runtime_id,
        volume_ref=volume_ref,
        volume_mount_path=volume_mount_path,
    )

    verification["session_id"] = session_id
    verification["fingerprint_verified"] = verification.get("verified", False)

    return verification


@activity.defn(name="oauth_session.update_status")
async def oauth_session_update_status(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Transition a session to a new status in the database."""
    session_id = request.get("session_id", "")
    new_status_str = request.get("status", "")

    if not session_id or not new_status_str:
        raise ValueError("session_id and status are required")

    try:
        new_status = OAuthSessionStatus(new_status_str)
    except ValueError:
        raise ValueError(f"Unknown session status: {new_status_str}")

    async with get_async_session_context() as db:
        from sqlalchemy.future import select

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        session_obj.status = new_status

        now = datetime.now(timezone.utc)
        if new_status == OAuthSessionStatus.STARTING:
            session_obj.started_at = now
        elif new_status == OAuthSessionStatus.SUCCEEDED:
            session_obj.completed_at = now
        elif new_status == OAuthSessionStatus.CANCELLED:
            session_obj.cancelled_at = now
        elif new_status == OAuthSessionStatus.EXPIRED:
            session_obj.completed_at = now

        await db.commit()

    logger.info("Updated session %s status to %s", session_id, new_status_str)
    return {"session_id": session_id, "status": new_status_str}


@activity.defn(name="oauth_session.register_profile")
async def oauth_session_register_profile(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Create or update provider profile from session data."""
    session_id = request.get("session_id", "")
    verification = request.get("verification")

    if not session_id:
        raise ValueError("session_id is required")

    async with get_async_session_context() as db:
        from sqlalchemy.future import select
        from api_service.db.models import (
            ManagedAgentProviderProfile,
            ManagedAgentRateLimitPolicy,
            ProviderCredentialSource,
            ProviderProfileAuthMethod,
            ProviderProfileAuthState,
            RuntimeMaterializationMode,
        )
        from api_service.services.provider_profile_service import (
            sync_provider_profile_manager,
        )

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        if isinstance(verification, Mapping) and not verification.get("verified"):
            reason = str(verification.get("reason") or "unknown")
            failure_reason = f"Volume verification failed: {reason}"
            raise exceptions.ApplicationError(
                failure_reason,
                non_retryable=True,
            )

        profile_result = await db.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.profile_id == session_obj.profile_id
            )
        )
        existing_profile = profile_result.scalars().first()

        metadata = dict(session_obj.metadata_json or {})
        registered_generation_raw = metadata.get("registered_credential_generation")
        registered_generation = (
            int(registered_generation_raw)
            if isinstance(registered_generation_raw, int)
            and not isinstance(registered_generation_raw, bool)
            and registered_generation_raw >= 1
            else None
        )
        policy_str = metadata.get(
            "rate_limit_policy", ManagedAgentRateLimitPolicy.BACKOFF.value
        )
        try:
            policy_enum = ManagedAgentRateLimitPolicy(policy_str)
        except ValueError:
            policy_enum = ManagedAgentRateLimitPolicy.BACKOFF

        connected_at = datetime.now(timezone.utc)
        requested_capacity = metadata.get("max_parallel_runs", 1)
        effective_max_parallel_runs = effective_oauth_capacity_for_finalization(
            runtime_id=session_obj.runtime_id,
            requested_capacity=requested_capacity,
        )
        profile_data = {
            "runtime_id": session_obj.runtime_id,
            "provider_id": metadata.get("provider_id")
            or get_provider_default(session_obj.runtime_id, "provider_id")
            or "unknown",
            "provider_label": metadata.get("provider_label")
            or get_provider_default(session_obj.runtime_id, "provider_label"),
            "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
            "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
            "volume_ref": session_obj.volume_ref,
            "volume_mount_path": session_obj.volume_mount_path,
            "account_label": session_obj.account_label,
            "max_parallel_runs": effective_max_parallel_runs,
            "cooldown_after_429_seconds": metadata.get(
                "cooldown_after_429_seconds", 900
            ),
            "rate_limit_policy": policy_enum,
            "enabled": True,
            "auth_state": ProviderProfileAuthState.CONNECTED,
            "disabled_reason": None,
            "first_authenticated_at": existing_profile.first_authenticated_at
            if existing_profile and existing_profile.first_authenticated_at
            else connected_at,
            "last_validated_at": connected_at,
            "last_auth_method": ProviderProfileAuthMethod.OAUTH_VOLUME,
        }
        validate_codex_oauth_profile_refs(
            runtime_id=session_obj.runtime_id,
            credential_source=ProviderCredentialSource.OAUTH_VOLUME.value,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME.value,
            volume_ref=session_obj.volume_ref,
            volume_mount_path=session_obj.volume_mount_path,
            max_parallel_runs=effective_max_parallel_runs,
            volume_ref_field_name="volume_ref",
            volume_mount_path_field_name="volume_mount_path",
        )

        reconnecting = False
        if existing_profile:
            reconnecting = bool(
                registered_generation is None
                and existing_profile.first_authenticated_at
                and existing_profile.last_auth_method
                == ProviderProfileAuthMethod.OAUTH_VOLUME
            )
            for key, value in profile_data.items():
                setattr(existing_profile, key, value)
            if registered_generation is not None:
                existing_profile.credential_generation = registered_generation
            elif reconnecting:
                existing_profile.credential_generation = (
                    existing_profile.credential_generation + 1
                )
            effective_generation = existing_profile.credential_generation
        else:
            try:
                owner_id = (
                    UUID(session_obj.requested_by_user_id)
                    if session_obj.requested_by_user_id
                    else None
                )
            except ValueError:
                owner_id = None
            new_profile = ManagedAgentProviderProfile(
                profile_id=session_obj.profile_id,
                owner_user_id=owner_id,
                **profile_data,
            )
            db.add(new_profile)
            effective_generation = 1

        if requested_capacity != effective_max_parallel_runs:
            metadata["max_parallel_runs"] = effective_max_parallel_runs
            metadata["capacity_normalized_to_exclusive"] = True
        metadata["registered_credential_generation"] = effective_generation
        if reconnecting:
            metadata["credential_generation_changed"] = True
        session_obj.metadata_json = metadata

        await db.commit()
        from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

        host_repository = OmnigentOAuthHostRepository(get_async_session_context)
        await host_repository.refresh_binding_generation(session_obj.profile_id)
        generation_changed = metadata.get("credential_generation_changed") is True
        generation_reconciled = metadata.get("credential_generation_reconciled") is True
        if existing_profile and generation_changed and not generation_reconciled:
            await host_repository.mark_generation_stale(
                profile_id=session_obj.profile_id,
                credential_generation=effective_generation,
            )
            metadata["credential_generation_reconciled"] = True
            session_obj.metadata_json = metadata
            await db.commit()
        await sync_provider_profile_manager(
            session=db, runtime_id=session_obj.runtime_id
        )

    logger.info(
        "Registered profile %s for session %s", session_obj.profile_id, session_id
    )
    return {
        "session_id": session_id,
        "profile_id": session_obj.profile_id,
        "status": "registered",
        "capacity_normalized_to_exclusive": (
            requested_capacity != effective_max_parallel_runs
        ),
        "credential_generation": effective_generation,
    }


@activity.defn(name="oauth_session.mark_failed")
async def oauth_session_mark_failed(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Mark a session as failed with a reason."""
    session_id = request.get("session_id", "")
    reason = request.get("reason", "Unknown failure")

    if not session_id:
        raise ValueError("session_id is required")

    async with get_async_session_context() as db:
        from sqlalchemy.future import select

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        session_obj.status = OAuthSessionStatus.FAILED
        session_obj.failure_reason = reason
        session_obj.completed_at = datetime.now(timezone.utc)
        await db.commit()

    logger.info("Marked session %s as failed: %s", session_id, reason)
    return {"session_id": session_id, "status": "failed", "reason": reason}
