"""Pre-flight validation for workflow automation runs.

This module validates prerequisites before starting a workflow run.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Optional

from moonmind.config.settings import settings
from moonmind.workflows.automation import models

logger = logging.getLogger(__name__)

_PREFLIGHT_OUTPUT_ERROR_MARKERS = re.compile(
    r"(?:not logged in|authentication required|unauthorized|401|403|login expired)",
    re.IGNORECASE,
)

@dataclass(slots=True)
class CodexPreflightResult:
    """Outcome of the Codex login status verification."""

    status: models.CodexPreflightStatus
    message: Optional[str] = None
    volume: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    failure_class: Optional[str] = None
    diagnostics_ref: Optional[str] = None

def _summarize_preflight_output(stdout: str, stderr: str) -> Optional[str]:
    """Return a one-line summary extracted from preflight output."""

    for line in (stderr or stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None

def _is_pinned_container_image(image: str) -> bool:
    """Return whether an image reference is pinned to a non-latest tag or digest."""

    normalized = image.strip()
    if not normalized:
        return False
    if "@" in normalized:
        return True
    last_segment = normalized.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return False
    tag = last_segment.rsplit(":", 1)[-1].strip().lower()
    return bool(tag) and tag != "latest"

def _configured_pinned_ue_image(
    env: Mapping[str, str],
    *,
    pinned_image: str | None = None,
) -> str | None:
    """Resolve the optional pinned Unreal Engine validation image."""

    candidates = (
        pinned_image,
        env.get("MOONMIND_UNREAL_ENGINE_IMAGE"),
        env.get("MOONMIND_UNREAL_DOCKER_IMAGE"),
        env.get("MOONMIND_PINNED_UE_IMAGE"),
        env.get("MOONMIND_PINNED_UNREAL_ENGINE_IMAGE"),
    )
    for candidate in candidates:
        image = str(candidate or "").strip()
        if image:
            return image
    return None

def _docker_sidecar_preflight_enabled(env: Mapping[str, str]) -> bool:
    mode = str(env.get("MOONMIND_MANAGED_SESSION_DOCKER_MODE") or "").strip().lower()
    if mode in {"no-docker", "disabled", "none", "off"}:
        return False
    return mode == "docker-sidecar" or bool(str(env.get("DOCKER_HOST") or "").strip())

def _run_docker_command(
    args: list[str],
    *,
    env: Mapping[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **{k: str(v) for k, v in env.items() if v is not None}},
    )


def _docker_preflight_os_error_result(
    exc: OSError,
    *,
    diagnostics_ref: str,
) -> CodexPreflightResult:
    message = "Docker sidecar preflight failed: docker CLI is not installed."
    if not isinstance(exc, FileNotFoundError):
        message = f"Docker sidecar preflight failed: {exc}"
    return CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message=message,
        failure_class="system_error",
        diagnostics_ref=diagnostics_ref,
    )


def run_docker_sidecar_preflight_check(
    *,
    env: Mapping[str, str] | None = None,
    timeout: int = 30,
    pinned_ue_image: str | None = None,
) -> CodexPreflightResult:
    """Validate Docker sidecar readiness and optional pinned UE image access."""

    source = env if env is not None else os.environ
    diagnostics_ref = "preflight://docker-sidecar"
    if not _docker_sidecar_preflight_enabled(source):
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.SKIPPED,
            message="Docker sidecar preflight skipped because sidecar Docker is not enabled.",
            diagnostics_ref=diagnostics_ref,
        )

    try:
        info = _run_docker_command(
            ["docker", "info"],
            env=source,
            timeout=timeout,
        )
    except OSError as exc:
        return _docker_preflight_os_error_result(
            exc,
            diagnostics_ref=diagnostics_ref,
        )
    except subprocess.TimeoutExpired:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=(
                "Docker sidecar preflight failed: docker info timed out after "
                f"{timeout} seconds."
            ),
            failure_class="system_error",
            diagnostics_ref=diagnostics_ref,
        )

    if info.returncode != 0:
        summary = _summarize_preflight_output(info.stdout, info.stderr)
        message = "Docker sidecar preflight failed: docker info did not succeed."
        if summary:
            message = f"{message} Details: {summary}"
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=message,
            exit_code=info.returncode,
            stdout=info.stdout,
            stderr=info.stderr,
            failure_class="system_error",
            diagnostics_ref=diagnostics_ref,
        )

    image = _configured_pinned_ue_image(source, pinned_image=pinned_ue_image)
    if not image:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.PASSED,
            message="Docker sidecar preflight passed; no pinned UE image probe configured.",
            exit_code=info.returncode,
            stdout=info.stdout,
            stderr=info.stderr,
            diagnostics_ref=diagnostics_ref,
        )
    if not _is_pinned_container_image(image):
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=(
                "Docker sidecar preflight failed: configured UE image must be pinned "
                f"to a non-latest tag or digest: {image}"
            ),
            failure_class="system_error",
            diagnostics_ref=diagnostics_ref,
        )

    try:
        manifest = _run_docker_command(
            ["docker", "manifest", "inspect", image],
            env=source,
            timeout=timeout,
        )
    except OSError as exc:
        return _docker_preflight_os_error_result(
            exc,
            diagnostics_ref=diagnostics_ref,
        )
    except subprocess.TimeoutExpired:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=(
                "Docker sidecar preflight failed: docker manifest inspect timed "
                f"out after {timeout} seconds for {image}."
            ),
            failure_class="system_error",
            diagnostics_ref=diagnostics_ref,
        )

    stdout = f"{info.stdout}\n{manifest.stdout}".strip()
    stderr = f"{info.stderr}\n{manifest.stderr}".strip()
    if manifest.returncode != 0:
        summary = _summarize_preflight_output(manifest.stdout, manifest.stderr)
        message = (
            "Docker sidecar preflight failed: pinned UE image manifest could not "
            f"be inspected: {image}."
        )
        if summary:
            message = f"{message} Details: {summary}"
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=message,
            exit_code=manifest.returncode,
            stdout=stdout,
            stderr=stderr,
            failure_class="system_error",
            diagnostics_ref=diagnostics_ref,
        )

    return CodexPreflightResult(
        status=models.CodexPreflightStatus.PASSED,
        message=f"Docker sidecar preflight passed for pinned UE image {image}.",
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        diagnostics_ref=diagnostics_ref,
    )

def _run_codex_preflight_check(
    *, timeout: int = 60, volume_name: str | None = None
) -> CodexPreflightResult:
    """Execute ``command -v rg && codex login status`` using the configured auth volume."""

    volume = volume_name or settings.workflow.codex_volume_name
    if not volume:
        logger.info(
            "Skipping Codex pre-flight check because no auth volume is configured",
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.SKIPPED,
            message="Codex auth volume not configured for this worker.",
        )

    try:
        import docker
        from docker.errors import APIError, DockerException
        from requests.exceptions import ReadTimeout
    except ImportError as exc:
        logger.warning("Docker SDK not available for preflight check: %s", exc)
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message="Docker SDK unavailable for Codex preflight check.",
            volume=volume,
        )

    image = (
        settings.workflow.codex_login_check_image
        or settings.workflow.job_image
    )
    stdout = ""
    stderr = ""
    exit_code: Optional[int] = None
    client = None

    try:
        client = docker.from_env()
        container = client.containers.run(
            image,
            command=["bash", "-lc", "command -v rg && codex login status"],
            environment={"HOME": "/home/app"},
            volumes={volume: {"bind": "/home/app/.codex", "mode": "ro"}},
            detach=True,
            auto_remove=True,
            tty=False,
        )
        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", 1))
        except ReadTimeout:
            try:
                container.stop(timeout=5)
            except DockerException as stop_exc:
                logger.debug(
                    "Codex pre-flight container stop failed after timeout: %s",
                    stop_exc,
                    extra={"codex_volume": volume},
                )
            message = (
                f"Codex login status check timed out after {timeout} seconds for "
                f"volume '{volume}'."
            )
            logger.warning(
                "Codex pre-flight check timed out",
                extra={"codex_volume": volume, "timeout_seconds": timeout},
            )
            return CodexPreflightResult(
                status=models.CodexPreflightStatus.FAILED,
                message=message,
                volume=volume,
            )
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False) or b""
            stderr_bytes = container.logs(stdout=False, stderr=True) or b""
        except (APIError, DockerException):
            stdout_bytes = b""
            stderr_bytes = b""
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except DockerException as exc:
        message = f"Unable to execute Codex login status for volume '{volume}': {exc}"
        logger.warning(
            "Codex pre-flight check failed to start",
            extra={"codex_volume": volume, "error": str(exc)},
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=message,
            volume=volume,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass

    summary = _summarize_preflight_output(stdout, stderr)
    if exit_code == 0:
        message = summary or "Codex login status check passed."
        logger.info(
            "Codex pre-flight check passed",
            extra={
                "codex_volume": volume,
                "codex_preflight_exit_code": exit_code,
                "codex_preflight_summary": summary,
            },
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.PASSED,
            message=message,
            volume=volume,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    remediation = (
        f"Codex login status failed for volume '{volume}'. "
        "Re-authenticate this shard using `codex login --device-auth` and retry."
    )
    if summary:
        remediation = f"{remediation} Details: {summary}"

    logger.warning(
        "Codex pre-flight check failed",
        extra={
            "codex_volume": volume,
            "codex_preflight_exit_code": exit_code,
            "codex_preflight_summary": summary,
        },
    )
    return CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message=remediation,
        volume=volume,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )

def run_codex_preflight_check(
    *, volume_name: str | None = None, timeout: int = 60
) -> CodexPreflightResult:
    """Public wrapper to execute the Codex login status check."""

    return _run_codex_preflight_check(timeout=timeout, volume_name=volume_name)

__all__ = [
    "CodexPreflightResult",
    "run_codex_preflight_check",
    "run_docker_sidecar_preflight_check",
]
