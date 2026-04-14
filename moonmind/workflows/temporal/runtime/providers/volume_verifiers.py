"""Volume verification for OAuth sessions.

Each provider stores auth credentials in a known location within a
Docker volume.  This module provides per-provider verification
functions that check whether the expected credential artifacts exist
after a user completes the OAuth flow.

Used by the finalize endpoint to confirm that the session actually
succeeded before committing the provider profile.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-provider credential paths (relative to mount root)
# ---------------------------------------------------------------------------

_GEMINI_CREDENTIAL_PATHS = (
    ".config/gemini/credentials.json",
    ".config/google-cloud-sdk/application_default_credentials.json",
)

_CODEX_CREDENTIAL_PATHS = (
    ".codex/auth.json",
    ".codex/config.json",
)

_CLAUDE_CREDENTIAL_PATHS = (
    ".claude/credentials.json",
    ".claude.json",
)

PROVIDER_CREDENTIAL_PATHS: dict[str, tuple[str, ...]] = {
    "gemini_cli": _GEMINI_CREDENTIAL_PATHS,
    "codex_cli": _CODEX_CREDENTIAL_PATHS,
    "claude_code": _CLAUDE_CREDENTIAL_PATHS,
}


async def verify_volume_credentials(
    runtime_id: str,
    volume_ref: str,
    volume_mount_path: str | None = None,
) -> dict[str, Any]:
    """Verify that auth credentials exist in the Docker volume.

    For MVP, this performs a ``docker run --rm`` with the volume mounted
    and checks for the existence of expected credential files.

    Returns a dict with ``verified`` (bool), ``found`` (list of found
    paths), and ``missing`` (list of missing paths).

    If Docker is unavailable or the volume doesn't exist, returns
    ``verified=False`` with appropriate diagnostics.
    """
    credential_paths = PROVIDER_CREDENTIAL_PATHS.get(runtime_id, ())

    if not credential_paths:
        logger.warning(
            "No credential paths defined for runtime %s — skipping verification",
            runtime_id,
        )
        return {
            "verified": True,
            "runtime_id": runtime_id,
            "reason": "no_credential_paths_defined",
            "found": [],
            "missing": [],
        }

    if not volume_ref:
        return {
            "verified": False,
            "runtime_id": runtime_id,
            "reason": "no_volume_ref",
            "found": [],
            "missing": list(credential_paths),
        }

    mount_path = volume_mount_path or "/mnt/auth"

    # Build a shell command that checks each credential path
    check_commands = " && ".join(
        f'( test -f "{mount_path}/{path}" && echo "FOUND:{path}" ) || echo "MISSING:{path}"'
        for path in credential_paths
    )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{volume_ref}:{mount_path}:ro",
        "alpine:3.19",
        "sh",
        "-c",
        check_commands,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=30
        )
    except FileNotFoundError:
        logger.warning("Docker not available for volume verification")
        return {
            "verified": False,
            "runtime_id": runtime_id,
            "reason": "docker_not_available",
            "found": [],
            "missing": list(credential_paths),
        }
    except asyncio.TimeoutError:
        logger.warning("Docker volume verification timed out")
        return {
            "verified": False,
            "runtime_id": runtime_id,
            "reason": "timeout",
            "found": [],
            "missing": list(credential_paths),
        }
    except Exception as exc:
        logger.warning("Volume verification failed: %s", exc)
        return {
            "verified": False,
            "runtime_id": runtime_id,
            "reason": f"error: {exc}",
            "found": [],
            "missing": list(credential_paths),
        }

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        logger.warning(
            "Volume verification docker run failed (exit %d): %s",
            process.returncode,
            stderr_text[:200],
        )
        return {
            "verified": False,
            "runtime_id": runtime_id,
            "reason": f"docker_exit_{process.returncode}",
            "found": [],
            "missing": list(credential_paths),
        }

    # Parse output
    output_text = stdout.decode("utf-8", errors="replace")
    found: list[str] = []
    missing: list[str] = []

    for line in output_text.strip().splitlines():
        line = line.strip()
        if line.startswith("FOUND:"):
            found.append(line[6:])
        elif line.startswith("MISSING:"):
            missing.append(line[8:])

    # At least one credential file must exist
    verified = len(found) > 0

    logger.info(
        "Volume verification for %s: verified=%s, found=%d, missing=%d",
        runtime_id,
        verified,
        len(found),
        len(missing),
    )

    return {
        "verified": verified,
        "runtime_id": runtime_id,
        "reason": "ok" if verified else "no_credentials_found",
        "found": found,
        "missing": missing,
    }
