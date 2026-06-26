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
import shlex
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
    "auth.json",
    "config.toml",
)

_CLAUDE_CREDENTIAL_PATHS = (
    ".credentials.json",
    "credentials.json",
    "settings.json",
)

PROVIDER_CREDENTIAL_PATHS: dict[str, tuple[str, ...]] = {
    "gemini_cli": _GEMINI_CREDENTIAL_PATHS,
    "codex_cli": _CODEX_CREDENTIAL_PATHS,
    "claude_code": _CLAUDE_CREDENTIAL_PATHS,
}

def _verification_result(
    *,
    verified: bool,
    runtime_id: str,
    reason: str,
    found_count: int,
    missing_count: int,
    status: str | None = None,
) -> dict[str, Any]:
    """Build compact, secret-free verification metadata."""
    return {
        "verified": verified,
        "status": status or ("verified" if verified else "failed"),
        "runtime_id": runtime_id,
        "reason": reason,
        "credentials_found_count": found_count,
        "credentials_missing_count": missing_count,
    }

def _build_credential_check_command(
    *,
    runtime_id: str,
    mount_path: str,
    credential_paths: tuple[str, ...],
) -> str:
    if runtime_id == "codex_cli":
        auth_path = shlex.quote(f"{mount_path}/auth.json")
        config_path = shlex.quote(f"{mount_path}/config.toml")
        return " && ".join(
            (
                (
                    f"( test -s {auth_path} "
                    f"&& grep -Eq '\"(tokens|access_token|refresh_token|id_token|api_key|OPENAI_API_KEY)\"' {auth_path} "
                    '&& echo "VALID:auth.json" ) || echo "INVALID:auth.json"'
                ),
                (
                    f"( test -s {config_path} && echo \"FOUND:config.toml\" ) "
                    '|| echo "MISSING:config.toml"'
                ),
            )
        )

    if runtime_id == "claude_code":
        dot_credentials_path = shlex.quote(f"{mount_path}/.credentials.json")
        credentials_path = shlex.quote(f"{mount_path}/credentials.json")
        settings_path = shlex.quote(f"{mount_path}/settings.json")
        settings_evidence_pattern = (
            r'"hasCompletedOnboarding"[[:space:]]*:[[:space:]]*true|'
            r'"(userID|userEmail)"[[:space:]]*:[[:space:]]*"[^"]+"|'
            r'"(account|oauth|primaryApiKeyHelper|customApiKeyResponses)"'
            r'[[:space:]]*:[[:space:]]*(true|"[^"]+"|\{[^{}]*[^[:space:]{}][^{}]*\}|'
            r'\[[^][]*[^[:space:]\[\]][^][]*\])'
        )
        return " && ".join(
            (
                (
                    f"( test -s {dot_credentials_path} "
                    '&& echo "FOUND:.credentials.json" ) '
                    '|| echo "MISSING:.credentials.json"'
                ),
                (
                    f"( test -s {credentials_path} "
                    '&& echo "FOUND:credentials.json" ) '
                    '|| echo "MISSING:credentials.json"'
                ),
                (
                    f"( test -s {settings_path} "
                    f"&& tr '\\n' ' ' < {settings_path} "
                    f"| grep -Eiq {shlex.quote(settings_evidence_pattern)} "
                    '&& echo "QUALIFIED:settings.json" ) '
                    f"|| ( test -s {settings_path} "
                    '&& echo "UNQUALIFIED:settings.json" ) '
                    '|| echo "MISSING:settings.json"'
                ),
            )
        )

    return " && ".join(
        (
            f"( test -f {shlex.quote(f'{mount_path}/{path}')} "
            f'&& echo "FOUND:{path}" ) || echo "MISSING:{path}"'
        )
        for path in credential_paths
    )

async def verify_volume_credentials(
    runtime_id: str,
    volume_ref: str,
    volume_mount_path: str | None = None,
) -> dict[str, Any]:
    """Verify that auth credentials exist in the Docker volume.

    For MVP, this performs a ``docker run --rm`` with the volume mounted
    and checks for the existence of expected credential files.

    Returns compact, secret-free metadata: ``verified`` (bool), ``status``,
    ``reason``, and credential presence counts. It does not return credential
    file paths or raw provider output.

    If Docker is unavailable or the volume doesn't exist, returns
    ``verified=False`` with appropriate diagnostics.
    """
    credential_paths = PROVIDER_CREDENTIAL_PATHS.get(runtime_id, ())

    if not credential_paths:
        logger.warning(
            "No credential paths defined for runtime %s — skipping verification",
            runtime_id,
        )
        return _verification_result(
            verified=True,
            runtime_id=runtime_id,
            reason="no_credential_paths_defined",
            found_count=0,
            missing_count=0,
            status="skipped",
        )

    if not volume_ref:
        return _verification_result(
            verified=False,
            runtime_id=runtime_id,
            reason="no_volume_ref",
            found_count=0,
            missing_count=len(credential_paths),
        )

    mount_path = volume_mount_path or "/mnt/auth"

    check_commands = _build_credential_check_command(
        runtime_id=runtime_id,
        mount_path=mount_path,
        credential_paths=credential_paths,
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
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=30
        )
    except FileNotFoundError:
        logger.warning("Docker not available for volume verification")
        return _verification_result(
            verified=False,
            runtime_id=runtime_id,
            reason="docker_not_available",
            found_count=0,
            missing_count=len(credential_paths),
        )
    except asyncio.TimeoutError:
        logger.warning("Docker volume verification timed out")
        return _verification_result(
            verified=False,
            runtime_id=runtime_id,
            reason="timeout",
            found_count=0,
            missing_count=len(credential_paths),
        )
    except Exception as exc:
        logger.warning(
            "Volume verification failed with %s", type(exc).__name__
        )
        return _verification_result(
            verified=False,
            runtime_id=runtime_id,
            reason="verification_error",
            found_count=0,
            missing_count=len(credential_paths),
        )

    if process.returncode != 0:
        logger.warning(
            "Volume verification docker run failed (exit %d)",
            process.returncode,
        )
        return _verification_result(
            verified=False,
            runtime_id=runtime_id,
            reason=f"docker_exit_{process.returncode}",
            found_count=0,
            missing_count=len(credential_paths),
        )

    # Parse output
    output_text = stdout.decode("utf-8", errors="replace")
    found: list[str] = []
    missing: list[str] = []
    valid: list[str] = []
    invalid: list[str] = []

    for line in output_text.strip().splitlines():
        line = line.strip()
        if line.startswith("VALID:"):
            valid.append(line[6:])
            found.append(line[6:])
        elif line.startswith("QUALIFIED:"):
            found.append(line[10:])
        elif line.startswith("INVALID:"):
            invalid.append(line[8:])
        elif line.startswith("UNQUALIFIED:"):
            missing.append(line[12:])
        elif line.startswith("FOUND:"):
            found.append(line[6:])
        elif line.startswith("MISSING:"):
            missing.append(line[8:])

    if runtime_id == "codex_cli":
        verified = bool(valid) and not invalid
        reason = "ok" if verified else "codex_auth_invalid"
    else:
        # At least one credential file must exist
        verified = len(found) > 0
        reason = "ok" if verified else "no_credentials_found"

    logger.info(
        "Volume verification for %s: verified=%s, found=%d, missing=%d",
        runtime_id,
        verified,
        len(found),
        len(missing),
    )

    return _verification_result(
        verified=verified,
        runtime_id=runtime_id,
        reason=reason,
        found_count=len(found),
        missing_count=len(missing),
    )
