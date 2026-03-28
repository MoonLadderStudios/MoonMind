"""OAuth session Temporal activities.

Provides the activities invoked by the ``MoonMind.OAuthSession`` workflow:
  - ``oauth_session.ensure_volume``       — verify / create Docker volume
  - ``oauth_session.start_auth_runner``   — launch tmate container
  - ``oauth_session.stop_auth_runner``    — tear down tmate container
  - ``oauth_session.update_status``       — transition session status in DB
  - ``oauth_session.mark_failed``         — mark session as failed with reason
  - ``oauth_session.update_session_urls`` — store extracted tmate urls in DB
  - ``oauth_session.verify_volume``       — call provider volume verifier
  - ``oauth_session.register_profile``    — create or update auth profile
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

from temporalio import activity

from api_service.db.base import get_async_session_context
from api_service.db.models import (
    ManagedAgentOAuthSession,
    OAuthSessionStatus,
)
from moonmind.workflows.temporal.runtime.tmate_session import _ENDPOINT_KEYS

logger = logging.getLogger(__name__)

# Container image used for auth runner sessions.  Falls back to the
# standard MoonMind worker image which already has tmate + CLI tools.
_DEFAULT_AUTH_RUNNER_IMAGE = "ghcr.io/moonladderstudios/moonmind:latest"

# Seconds to wait for tmate to become ready inside the container.
_TMATE_READY_TIMEOUT = 30


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
            "docker", "volume", "create", volume_ref,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "docker volume create failed for %s (rc=%d): %s",
                volume_ref, proc.returncode, err[:200],
            )
            return {"session_id": session_id, "volume_ref": volume_ref, "status": "create_failed"}
        logger.info("Ensured volume %s for session %s", volume_ref, session_id)
        return {"session_id": session_id, "volume_ref": volume_ref, "status": "ok"}
    except FileNotFoundError:
        logger.info(
            "Docker CLI not available — skipping volume create for session %s",
            session_id,
        )
        return {"session_id": session_id, "volume_ref": volume_ref, "status": "docker_unavailable"}
    except asyncio.TimeoutError:
        logger.warning("docker volume create timed out for session %s", session_id)
        return {"session_id": session_id, "volume_ref": volume_ref, "status": "timeout"}


@activity.defn(name="oauth_session.start_auth_runner")
async def oauth_session_start_auth_runner(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Launch a Docker container with tmate for interactive OAuth login.

    The container mounts the auth volume at the provider's expected path,
    starts tmate, and runs the provider bootstrap command.  Returns the
    tmate web/ssh URLs and container name so the workflow can store them
    and the user can connect.
    """
    session_id = request.get("session_id", "")
    runtime_id = request.get("runtime_id", "")
    volume_ref = request.get("volume_ref", "")
    volume_mount_path = request.get("volume_mount_path", "")
    session_ttl = int(request.get("session_ttl", 1800))

    from moonmind.workflows.temporal.runtime.providers.registry import get_provider
    from moonmind.config.settings import settings

    provider = get_provider(runtime_id)
    if provider is None:
        raise ValueError(f"No OAuth provider registered for runtime: {runtime_id}")

    mount_path = volume_mount_path or provider["default_mount_path"]
    vol = volume_ref or provider["default_volume_name"]
    bootstrap_cmd = provider["bootstrap_command"]
    container_name = f"mm-oauth-{session_id}"
    image = (
        getattr(settings.workflow, "job_image", None)
        or _DEFAULT_AUTH_RUNNER_IMAGE
    )

    # Build the entrypoint script that starts tmate and runs bootstrap
    bootstrap_script = (
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "# Start tmate in the background with a named session\n"
        "tmate -F -n main new-session -d -s oauth &\n"
        "TMATE_PID=$!\n"
        "# Wait for tmate to be ready\n"
        "for i in $(seq 1 {timeout}); do\n"
        "  if tmate -S /tmp/tmate.sock display -p '#{{tmate_ssh}}' 2>/dev/null | grep -q '^ssh '; then\n"
        "    break\n"
        "  fi\n"
        "  sleep 1\n"
        "done\n"
        "# Send bootstrap command into the tmate session\n"
        "tmate -S /tmp/tmate.sock send-keys -t oauth '{bootstrap}' Enter\n"
        "# Keep alive until tmate exits\n"
        "wait $TMATE_PID\n"
    ).format(
        timeout=_TMATE_READY_TIMEOUT,
        bootstrap=shlex.join(bootstrap_cmd),
    )

    # Docker run: detached container with tmate + auth volume
    docker_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-v", f"{vol}:{mount_path}",
        "-e", f"HOME={mount_path}",
        "--stop-timeout", str(min(session_ttl + 60, 3600)),
        image,
        "bash", "-c", bootstrap_script,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except FileNotFoundError:
        raise RuntimeError("Docker CLI not available on this worker")
    except asyncio.TimeoutError:
        raise RuntimeError("Docker container launch timed out")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to start auth runner container (rc={proc.returncode}): {err[:300]}")

    container_id = stdout.decode("utf-8", errors="replace").strip()[:12]
    logger.info(
        "Started auth runner container %s (%s) for session %s",
        container_name, container_id, session_id,
    )

    # Wait for tmate to become ready and extract URLs
    tmate_web_url = ""
    tmate_ssh_url = ""

    for attempt in range(_TMATE_READY_TIMEOUT):
        await asyncio.sleep(1)
        # Use the shared endpoint key mapping for consistency with TmateSessionManager.
        web_key = _ENDPOINT_KEYS["web_rw"]
        ssh_key = _ENDPOINT_KEYS["attach_rw"]
        # Try to extract tmate URLs from the container's tmate socket
        url_cmd = [
            "docker",
            "exec",
            container_name,
            "bash",
            "-c",
            (
                f"tmate -S /tmp/tmate.sock display -p '#{{{web_key}}}' 2>/dev/null && "
                "echo '---SEPARATOR---' && "
                f"tmate -S /tmp/tmate.sock display -p '#{{{ssh_key}}}' 2>/dev/null"
            ),
        ]
        try:
            url_proc = await asyncio.create_subprocess_exec(
                *url_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            url_stdout, _ = await asyncio.wait_for(url_proc.communicate(), timeout=5)
            if url_proc.returncode == 0:
                output = url_stdout.decode("utf-8", errors="replace").strip()
                parts = output.split("---SEPARATOR---")
                if len(parts) == 2:
                    web = parts[0].strip()
                    ssh = parts[1].strip()
                    if web and ssh and ssh.startswith("ssh "):
                        tmate_web_url = web
                        tmate_ssh_url = ssh
                        break
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug(
                "Polling for tmate URLs failed on attempt %d: %s",
                attempt + 1,
                exc,
            )
            continue

    if not tmate_ssh_url:
        logger.warning(
            "Tmate URLs not available after %ds for session %s — container is running but tmate may still be starting",
            _TMATE_READY_TIMEOUT, session_id,
        )

    # Store tmate URLs and container info in the DB
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=session_ttl)
    async with get_async_session_context() as db:
        from sqlalchemy.future import select

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if session_obj:
            session_obj.tmate_web_url = tmate_web_url or None
            session_obj.tmate_ssh_url = tmate_ssh_url or None
            session_obj.container_name = container_name
            session_obj.expires_at = expires_at
            await db.commit()

    logger.info(
        "Auth runner ready for session %s: web=%s container=%s",
        session_id, tmate_web_url or "(pending)", container_name,
    )

    return {
        "session_id": session_id,
        "container_name": container_name,
        "tmate_web_url": tmate_web_url,
        "tmate_ssh_url": tmate_ssh_url,
        "expires_at": expires_at.isoformat(),
    }


@activity.defn(name="oauth_session.update_session_urls")
async def oauth_session_update_session_urls(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Write extracted tmate web/ssh URLs to the session DB row."""
    session_id = request.get("session_id", "")
    tmate_web_url = request.get("tmate_web_url", "")
    tmate_ssh_url = request.get("tmate_ssh_url", "")

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

        if tmate_web_url:
            session_obj.tmate_web_url = tmate_web_url
        if tmate_ssh_url:
            session_obj.tmate_ssh_url = tmate_ssh_url

        await db.commit()

    logger.info("Updated tmate URLs for session %s", session_id)
    return {
        "session_id": session_id,
        "tmate_web_url": tmate_web_url,
        "tmate_ssh_url": tmate_ssh_url,
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
        # Look up the container name from the DB
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

    # Stop the container (with a short grace period)
    try:
        stop_proc = await asyncio.create_subprocess_exec(
            "docker", "stop", "-t", "5", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(stop_proc.communicate(), timeout=30)
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as exc:
        logger.warning("Failed to stop container %s: %s", container_name, exc)

    # Remove the container
    try:
        rm_proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(rm_proc.communicate(), timeout=15)
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as exc:
        logger.warning("Failed to remove container %s: %s", container_name, exc)

    logger.info("Stopped auth runner container %s for session %s", container_name, session_id)
    return {"session_id": session_id, "stopped": True, "container_name": container_name}


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

    from moonmind.workflows.temporal.runtime.providers.volume_verifiers import verify_volume_credentials

    verification = await verify_volume_credentials(
        runtime_id=runtime_id,
        volume_ref=volume_ref,
        volume_mount_path=volume_mount_path,
    )

    verification["session_id"] = session_id

    # Store verification results in the DB? The design says:
    # "returns pass/fail with details". The actual activity just returns it.

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

        # Set lifecycle timestamps based on status
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

    logger.info(
        "Updated session %s status to %s", session_id, new_status_str
    )
    return {"session_id": session_id, "status": new_status_str}


@activity.defn(name="oauth_session.register_profile")
async def oauth_session_register_profile(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Create or update auth profile from session data."""
    session_id = request.get("session_id", "")

    if not session_id:
        raise ValueError("session_id is required")

    async with get_async_session_context() as db:
        from sqlalchemy.future import select
        from api_service.db.models import ManagedAgentAuthProfile, ManagedAgentRateLimitPolicy, ManagedAgentAuthMode
        from api_service.services.auth_profile_service import sync_auth_profile_manager

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        profile_result = await db.execute(
            select(ManagedAgentAuthProfile).where(
                ManagedAgentAuthProfile.profile_id == session_obj.profile_id
            )
        )
        existing_profile = profile_result.scalars().first()

        metadata = session_obj.metadata_json or {}
        policy_str = metadata.get("rate_limit_policy", ManagedAgentRateLimitPolicy.BACKOFF.value)
        try:
            policy_enum = ManagedAgentRateLimitPolicy(policy_str)
        except ValueError:
            policy_enum = ManagedAgentRateLimitPolicy.BACKOFF

        profile_data = {
            "runtime_id": session_obj.runtime_id,
            "auth_mode": ManagedAgentAuthMode.OAUTH,
            "volume_ref": session_obj.volume_ref,
            "volume_mount_path": session_obj.volume_mount_path,
            "account_label": session_obj.account_label,
            "max_parallel_runs": metadata.get("max_parallel_runs", 1),
            "cooldown_after_429_seconds": metadata.get("cooldown_after_429_seconds", 900),
            "rate_limit_policy": policy_enum,
            "enabled": True,
        }

        if existing_profile:
            for key, value in profile_data.items():
                setattr(existing_profile, key, value)
        else:
            try:
                # The owner_user_id column is a UUID, not an integer.
                owner_id = UUID(session_obj.requested_by_user_id) if session_obj.requested_by_user_id else None
            except ValueError:
                owner_id = None
            new_profile = ManagedAgentAuthProfile(
                profile_id=session_obj.profile_id,
                owner_user_id=owner_id,
                **profile_data
            )
            db.add(new_profile)

        await db.commit()
        await sync_auth_profile_manager(session=db, runtime_id=session_obj.runtime_id)

    logger.info("Registered profile %s for session %s", session_obj.profile_id, session_id)
    return {"session_id": session_id, "profile_id": session_obj.profile_id, "status": "registered"}


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

    logger.info(
        "Marked session %s as failed: %s", session_id, reason
    )
    return {"session_id": session_id, "status": "failed", "reason": reason}
