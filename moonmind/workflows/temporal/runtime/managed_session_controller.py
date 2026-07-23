"""Docker-backed controller for managed runtime sessions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import posixpath
import re
import shlex
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlparse

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    ManagedSessionDockerCapabilityRequest,
    ManagedSessionEnsureDockerSidecarRequest,
    ManagedSessionEnsureDockerSidecarResponse,
    ManagedSessionRecordStatus,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
    canonical_managed_session_runtime_id,
)
from moonmind.workflows.codex_session_timeouts import (
    DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
)
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_ghcr_pull_credentials_for_launch,
    resolve_github_token_for_launch,
)
from moonmind.workflows.skills.workspace_links import cleanup_moonmind_skill_projections
from moonmind.utils.logging import SecretRedactor, scrub_github_tokens
from moonmind.workflow_docker_mode import normalize_workflow_docker_mode

from .github_auth_broker import (
    GitHubAuthBrokerManager,
    build_github_socket_path,
    render_gh_wrapper_script,
    render_git_credential_helper_script,
)
from .git_auth import build_github_token_git_environment
from .managed_session_observability import ManagedSessionObservabilityBridge
from .managed_session_store import (
    TERMINAL_MANAGED_SESSION_STATUSES,
    ManagedSessionStore,
)
from .managed_session_supervisor import ManagedSessionSupervisor

_RUNTIME_MODULE = "moonmind.workflows.temporal.runtime.codex_session_runtime"
_CONTAINER_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_.-]+")
_RESERVED_SESSION_ENV_PREFIX = "MOONMIND_SESSION_"
_MANAGED_SESSION_CONTAINER_UID = 1000
_EMPTY_ASSISTANT_FAILURE_CAUSE = "app_server_protocol_empty_turn"
_CLEAR_REQUEST_METADATA_KEY = "lastClearRequest"
_EMPTY_ASSISTANT_TURN_METADATA_KEY = "emptyAssistantTurn"
_MANAGED_SESSION_CONTAINER_GID = 1000
_MANAGED_SESSION_CONTAINER_USER = (
    f"{_MANAGED_SESSION_CONTAINER_UID}:{_MANAGED_SESSION_CONTAINER_GID}"
)
_SENSITIVE_ENV_KEY_PATTERN = re.compile(
    r"(?i)(?:token|secret|password|key|credential|auth)"
)
_GIT_COMMAND_LOCALE = {"LC_ALL": "C", "LANG": "C"}
_SESSION_STATE_FILENAME = ".moonmind-codex-session-state.json"
_CONTAINER_LOG_EXCERPT_TAIL_LINES = 40
_CONTAINER_LOG_EXCERPT_MAX_CHARS = 2000
_LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES = 4 * 1024
_SESSION_DOCKER_SOCKET_DIR = "/var/run/moonmind-docker"
_SESSION_DOCKER_SOCKET_PATH = f"{_SESSION_DOCKER_SOCKET_DIR}/docker.sock"
_SESSION_DOCKER_GRAPH_PATH = "/var/lib/docker"
_SESSION_DOCKER_CONFIG_DIRNAME = ".docker"
_SESSION_DOCKER_CONFIG_FILENAME = "config.json"
_DEFAULT_SESSION_DOCKER_SIDECAR_IMAGE = "docker:27-dind"
_SESSION_DOCKER_MODE_ENABLED_VALUES = {"docker-sidecar"}
_SESSION_DOCKER_MODE_DISABLED_VALUES = {"no-docker", "disabled", "none", "off"}
# Grace window before an orphaned managed-session container is eligible for
# reaping. A session writes its durable store record early in launch, but the
# window protects against reaping a container whose record is not yet active
# (mid-launch) or that is being relaunched.
_DEFAULT_SESSION_REAP_GRACE_SECONDS = 900.0
_DEFAULT_SESSION_REAP_MAX_AGE_SECONDS = 48 * 60 * 60
_MANAGED_SESSION_LABEL_KEY = "moonmind.session_id"
_MANAGED_SESSION_SIDECAR_VOLUME_KIND = "session-docker-sidecar-volume"
_MANAGED_SESSION_SIDECAR_VOLUME_NAME = re.compile(
    r"^moonmind-session-.+-docker-(graph|socket)$"
)
_FALSEY_ENV_VALUES = {"0", "false", "no", "off", ""}
_TERMINAL_OWNER_WORKFLOW_STATUSES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "CANCELED",
        "CANCELLED",
        "TERMINATED",
        "TIMED_OUT",
        "CONTINUED_AS_NEW",
    }
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ManagedSessionContainer:
    """A docker container carrying the managed-session label."""

    container_id: str
    session_id: str
    kind: str
    created_at: datetime | None


@dataclass(frozen=True)
class _ManagedSessionSidecarVolume:
    """A docker volume created for a managed-session docker sidecar."""

    name: str
    session_id: str
    role: str
    kind: str
    created_at: datetime | None


@dataclass(frozen=True)
class ManagedSessionReapResult:
    """Outcome of one orphaned managed-session container sweep."""

    scanned_containers: int = 0
    reaped_session_ids: tuple[str, ...] = ()
    reaped_containers: int = 0
    skipped_active: int = 0
    skipped_recent: int = 0
    forced_stale: int = 0
    scanned_volumes: int = 0
    reaped_volumes: int = 0
    skipped_active_volumes: int = 0
    skipped_recent_volumes: int = 0
    disabled: bool = False


class OwnerWorkflowStatusResolver(Protocol):
    async def __call__(self, workflow_id: str, /) -> Any:
        """Return a provider-specific workflow status for *workflow_id*."""


def _parse_docker_timestamp(value: str) -> datetime | None:
    """Parse a docker RFC3339(Nano) timestamp into an aware datetime.

    Returns ``None`` for the docker zero time or any unparseable value so the
    caller treats the container's age as unknown.
    """

    text = (value or "").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    text = text.replace("Z", "+00:00")
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", text)
    if not match:
        return None
    fractional = match.group(2) or ""
    if fractional:
        # datetime.fromisoformat accepts at most microsecond precision.
        fractional = fractional[:7]
    rebuilt = f"{match.group(1)}{fractional}{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(rebuilt)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _coerce_aware_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = _parse_docker_timestamp(value)
    else:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_owner_workflow_status(value: Any) -> str:
    """Normalize Temporal workflow status values for terminal-state checks."""

    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().upper()
    raw = getattr(value, "value", value)
    if isinstance(raw, str):
        normalized = raw.strip().upper()
    else:
        normalized = str(raw).strip().upper()
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return normalized


def _owner_workflow_status_is_terminal(value: Any) -> bool:
    return _normalize_owner_workflow_status(value) in _TERMINAL_OWNER_WORKFLOW_STATUSES

def _last_assistant_text_metadata(value: str) -> dict[str, Any]:
    normalized = str(value or "").strip()
    if not normalized:
        return {}
    encoded = normalized.encode("utf-8")
    if len(encoded) <= _LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES:
        return {"lastAssistantText": normalized}
    truncated = encoded[:_LAST_ASSISTANT_TEXT_METADATA_MAX_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return {
        "lastAssistantText": truncated,
        "lastAssistantTextTruncated": True,
        "lastAssistantTextOriginalChars": len(normalized),
    }


def _assistant_text_event_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    normalized = value.strip()
    if not normalized:
        return {}
    return {
        "assistantTextOmitted": True,
        "assistantTextSha256": hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest(),
        "assistantTextLengthChars": len(normalized),
    }


def _is_empty_assistant_turn_response(
    response: CodexManagedSessionTurnResponse,
) -> bool:
    if response.metadata.get("failureCause") == _EMPTY_ASSISTANT_FAILURE_CAUSE:
        return True
    retry_action = str(response.metadata.get("retryRecommendedAction") or "").strip()
    reason = str(response.metadata.get("reason") or "").strip()
    return retry_action == "clear_session" and "produced no assistant output" in reason


def _empty_assistant_turn_metadata(
    record_metadata: Mapping[str, Any],
    response: CodexManagedSessionTurnResponse,
) -> dict[str, Any]:
    existing = record_metadata.get(_EMPTY_ASSISTANT_TURN_METADATA_KEY)
    previous_count = 0
    if isinstance(existing, Mapping):
        try:
            previous_count = int(existing.get("consecutiveCount") or 0)
        except (TypeError, ValueError):
            previous_count = 0
    count = max(previous_count, 0) + 1
    return {
        "failureCause": _EMPTY_ASSISTANT_FAILURE_CAUSE,
        "consecutiveCount": count,
        "lastTurnId": response.turn_id,
        "lastSessionEpoch": response.session_state.session_epoch,
        "lastThreadId": response.session_state.thread_id,
        "retryRecommendedAction": "clear_session",
        "lastReason": str(response.metadata.get("reason") or "").strip()
        or "codex app-server turn produced no assistant output",
        "updatedAt": datetime.now(tz=UTC).isoformat(),
    }

def _managed_session_docker_network(
    request_environment: Mapping[str, str] | None = None,
) -> str | None:
    """Return the Docker network managed session containers should join."""

    for env_key in (
        "MOONMIND_MANAGED_SESSION_DOCKER_NETWORK",
        "MOONMIND_DOCKER_NETWORK",
    ):
        raw_value = os.environ.get(env_key)
        if raw_value is None:
            continue
        value = raw_value.strip()
        if value.lower() in {"", "none", "disabled", "off"}:
            return None
        return value

    moonmind_url = ""
    if request_environment is not None:
        moonmind_url = str(request_environment.get("MOONMIND_URL") or "").strip()
    if not moonmind_url:
        moonmind_url = os.environ.get("MOONMIND_URL", "").strip()
    if moonmind_url:
        hostname = (urlparse(moonmind_url).hostname or "").strip().lower()
        if hostname in {"api", "moonmind-api", "moonmind-api-1"}:
            return "local-network"
    return None

class CommandRunner(Protocol):
    async def __call__(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        run_as_uid: int | None = None,
        run_as_gid: int | None = None,
    ) -> tuple[int, str, str]:
        pass

async def _default_command_runner(
    command: tuple[str, ...],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    run_as_uid: int | None = None,
    run_as_gid: int | None = None,
) -> tuple[int, str, str]:
    subprocess_kwargs: dict[str, Any] = {}
    geteuid = getattr(os, "geteuid", None)
    if os.name == "posix" and callable(geteuid) and geteuid() == 0:
        if run_as_uid is not None or run_as_gid is not None:
            subprocess_kwargs["extra_groups"] = []
        if run_as_uid is not None:
            subprocess_kwargs["user"] = run_as_uid
        if run_as_gid is not None:
            subprocess_kwargs["group"] = run_as_gid
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
        **subprocess_kwargs,
    )
    stdout, stderr = await process.communicate(
        input_text.encode("utf-8") if input_text is not None else None
    )
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )

def _normalize_absolute_posix_path(value: str, *, field_name: str) -> PurePosixPath:
    normalized = PurePosixPath(posixpath.normpath(value))
    if not normalized.is_absolute():
        raise RuntimeError(f"{field_name} must be an absolute path: {value}")
    return normalized

def _is_sensitive_env_key(key: str) -> bool:
    return bool(_SENSITIVE_ENV_KEY_PATTERN.search(key))

class DockerCodexManagedSessionController:
    """Launch and control managed runtime session containers via Docker CLI."""

    def __init__(
        self,
        *,
        workspace_volume_name: str,
        codex_volume_name: str,
        workspace_root: str,
        network_name: str | None = None,
        moonmind_url: str | None = None,
        session_store: ManagedSessionStore | None = None,
        session_supervisor: ManagedSessionSupervisor | Any | None = None,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        ready_poll_interval_seconds: float = 1.0,
        ready_poll_attempts: int = 30,
        turn_poll_interval_seconds: float = 1.0,
        turn_poll_timeout_seconds: float = (
            DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
        ),
        command_runner: CommandRunner = _default_command_runner,
        github_auth_brokers: GitHubAuthBrokerManager | Any | None = None,
        owner_workflow_status_resolver: OwnerWorkflowStatusResolver | None = None,
    ) -> None:
        self._workspace_volume_name = workspace_volume_name
        self._codex_volume_name = codex_volume_name
        self._workspace_root = workspace_root
        self._network_name = str(network_name or "").strip() or None
        self._moonmind_url = str(moonmind_url or "").strip() or None
        self._session_store = session_store
        self._session_supervisor = session_supervisor
        self._observability_bridge = (
            ManagedSessionObservabilityBridge(session_supervisor)
            if session_supervisor is not None
            else None
        )
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._ready_poll_interval_seconds = ready_poll_interval_seconds
        self._ready_poll_attempts = ready_poll_attempts
        self._turn_poll_interval_seconds = turn_poll_interval_seconds
        self._turn_poll_timeout_seconds = turn_poll_timeout_seconds
        self._command_runner = command_runner
        self._github_auth_brokers = github_auth_brokers or GitHubAuthBrokerManager()
        self._owner_workflow_status_resolver = owner_workflow_status_resolver

    @staticmethod
    def _managed_session_user_command_kwargs() -> dict[str, int]:
        geteuid = getattr(os, "geteuid", None)
        if os.name != "posix" or not callable(geteuid) or geteuid() != 0:
            return {}
        return {
            "run_as_uid": _MANAGED_SESSION_CONTAINER_UID,
            "run_as_gid": _MANAGED_SESSION_CONTAINER_GID,
        }

    def _docker_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return env

    @staticmethod
    def _managed_session_docker_mode(
        session_environment: Mapping[str, str],
    ) -> str:
        return (
            session_environment.get("MOONMIND_MANAGED_SESSION_DOCKER_MODE")
            or os.environ.get("MOONMIND_MANAGED_SESSION_DOCKER_MODE")
            or ""
        ).strip().lower()

    @staticmethod
    def _workflow_docker_mode_source(
        session_environment: Mapping[str, str],
    ) -> str | None:
        raw_mode = session_environment.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        if raw_mode is None:
            raw_mode = os.environ.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        if raw_mode is None:
            return None
        return str(raw_mode).strip()

    def _apply_unrestricted_docker_session_environment(
        self,
        session_environment: dict[str, str],
    ) -> bool:
        raw_mode = self._workflow_docker_mode_source(session_environment)
        workflow_docker_mode = normalize_workflow_docker_mode(raw_mode)
        if workflow_docker_mode != "unrestricted":
            return False

        managed_session_docker_mode = self._managed_session_docker_mode(
            session_environment
        )
        if (
            raw_mode is not None
            and managed_session_docker_mode in _SESSION_DOCKER_MODE_DISABLED_VALUES
        ):
            raise RuntimeError(
                "MM-784 per-runtime Docker policy denied: "
                "MOONMIND_MANAGED_SESSION_DOCKER_MODE="
                f"{managed_session_docker_mode} cannot receive unrestricted "
                "Docker proxy access"
            )

        session_environment["MOONMIND_WORKFLOW_DOCKER_MODE"] = "unrestricted"
        if self._docker_host:
            session_environment.setdefault("DOCKER_HOST", self._docker_host)
            session_environment.setdefault("SYSTEM_DOCKER_HOST", self._docker_host)
        return True

    def _unrestricted_docker_proxy_network(
        self,
        *,
        session_environment: Mapping[str, str],
        docker_network: str | None,
    ) -> str | None:
        mode = normalize_workflow_docker_mode(
            session_environment.get("MOONMIND_WORKFLOW_DOCKER_MODE")
            or os.environ.get("MOONMIND_WORKFLOW_DOCKER_MODE")
        )
        if mode != "unrestricted":
            return None
        docker_host = str(
            session_environment.get("DOCKER_HOST") or self._docker_host or ""
        ).strip()
        if "docker-proxy" not in docker_host:
            return None
        proxy_network = (
            os.environ.get("MOONMIND_DOCKER_PROXY_NETWORK")
            or os.environ.get("MOONMIND_DOCKER_PROXY_NETWORK_NAME")
            or "moonmind_docker-proxy-network"
        ).strip()
        if not proxy_network or proxy_network == docker_network:
            return None
        return proxy_network

    @staticmethod
    def _session_name_slug(session_id: str) -> str:
        sanitized = _CONTAINER_NAME_SANITIZER.sub("-", session_id).strip("-")
        if not sanitized:
            sanitized = "managed-session"
        return sanitized

    def _container_name(self, session_id: str) -> str:
        return f"mm-codex-session-{self._session_name_slug(session_id)}"

    def _sidecar_agent_container_name(self, session_id: str) -> str:
        return f"moonmind-session-{self._session_name_slug(session_id)}-agent"

    def _sidecar_container_name(self, session_id: str) -> str:
        return f"moonmind-session-{self._session_name_slug(session_id)}-docker"

    def _sidecar_socket_volume_name(self, session_id: str) -> str:
        return f"moonmind-session-{self._session_name_slug(session_id)}-docker-socket"

    def _sidecar_graph_volume_name(self, session_id: str) -> str:
        return f"moonmind-session-{self._session_name_slug(session_id)}-docker-graph"

    def _session_docker_sidecar_enabled(
        self,
        session_environment: Mapping[str, str],
        docker_capability: ManagedSessionDockerCapabilityRequest | None = None,
    ) -> bool:
        if docker_capability is not None:
            if not docker_capability.allowed or docker_capability.activation == "denied":
                return False
            if docker_capability.mode == "sidecar-dind-rootless":
                raise RuntimeError(
                    "dockerCapability.mode=sidecar-dind-rootless is not "
                    "materialized by the Docker session launcher yet"
                )
            return True
        raw_mode = self._managed_session_docker_mode(session_environment)
        if raw_mode in _SESSION_DOCKER_MODE_DISABLED_VALUES:
            return False
        if raw_mode == "docker-sidecar-rootless":
            raise RuntimeError(
                "MOONMIND_MANAGED_SESSION_DOCKER_MODE=docker-sidecar-rootless "
                "is not materialized by the Docker session launcher yet"
            )
        if raw_mode in _SESSION_DOCKER_MODE_ENABLED_VALUES:
            return True
        if raw_mode:
            allowed = sorted(
                _SESSION_DOCKER_MODE_ENABLED_VALUES
                | _SESSION_DOCKER_MODE_DISABLED_VALUES
                | {"docker-sidecar-rootless"}
            )
            raise RuntimeError(
                "Unsupported MOONMIND_MANAGED_SESSION_DOCKER_MODE "
                f"{raw_mode!r}; expected one of {', '.join(allowed)}"
            )
        workflow_source = self._workflow_docker_mode_source(session_environment)
        if workflow_source is None:
            return False
        workflow_mode = normalize_workflow_docker_mode(workflow_source)
        return workflow_mode != "disabled"

    @staticmethod
    def _docker_capability_for_launch(
        request: LaunchCodexManagedSessionRequest,
        *,
        sidecar_enabled: bool,
    ) -> ManagedSessionDockerCapabilityRequest | None:
        capability = request.docker_capability
        if capability is not None:
            return capability
        if sidecar_enabled:
            return ManagedSessionDockerCapabilityRequest()
        return None

    @staticmethod
    def _docker_activation_at_launch(
        capability: ManagedSessionDockerCapabilityRequest | None,
    ) -> bool:
        return capability is not None and capability.allowed

    def _session_docker_sidecar_image(self) -> str:
        return (
            os.environ.get("MOONMIND_MANAGED_SESSION_DOCKER_SIDECAR_IMAGE")
            or _DEFAULT_SESSION_DOCKER_SIDECAR_IMAGE
        ).strip() or _DEFAULT_SESSION_DOCKER_SIDECAR_IMAGE

    @staticmethod
    def _image_is_pinned(image: str) -> bool:
        text = str(image or "").strip()
        if not text:
            return False
        if "@sha256:" in text:
            return True
        last_segment = text.rsplit("/", 1)[-1]
        if ":" not in last_segment:
            return False
        tag = last_segment.rsplit(":", 1)[-1].strip().lower()
        return bool(tag) and tag != "latest"

    @staticmethod
    def _sidecar_volume_labels(
        *,
        session_id: str,
        role: str,
        agent_run_id: str,
        session_epoch: int,
    ) -> dict[str, str]:
        return {
            "moonmind.session_id": session_id,
            "moonmind.kind": _MANAGED_SESSION_SIDECAR_VOLUME_KIND,
            "moonmind.volume_role": role,
            "moonmind.agent_run_id": agent_run_id,
            "moonmind.session_epoch": str(session_epoch),
        }

    async def _create_volume(
        self,
        volume_name: str,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        command: list[str] = [self._docker_binary, "volume", "create"]
        for key, value in (labels or {}).items():
            command.extend(["--label", f"{key}={value}"])
        command.append(volume_name)
        await self._run(tuple(command))

    async def _remove_volume(self, volume_name: str, *, ignore_failure: bool) -> bool:
        try:
            await self._run((self._docker_binary, "volume", "rm", "-f", volume_name))
            return True
        except RuntimeError:
            if not ignore_failure:
                raise
            return False

    async def _create_docker_sidecar_volumes(
        self,
        *,
        session_id: str,
        session_epoch: int,
        agent_run_id: str,
    ) -> None:
        await self._create_volume(
            self._sidecar_socket_volume_name(session_id),
            labels=self._sidecar_volume_labels(
                session_id=session_id,
                role="docker-socket",
                agent_run_id=agent_run_id,
                session_epoch=session_epoch,
            ),
        )
        await self._create_volume(
            self._sidecar_graph_volume_name(session_id),
            labels=self._sidecar_volume_labels(
                session_id=session_id,
                role="docker-graph",
                agent_run_id=agent_run_id,
                session_epoch=session_epoch,
            ),
        )

    @staticmethod
    def _docker_name_conflict(exc: RuntimeError, container_name: str) -> bool:
        detail = str(exc).lower()
        name = str(container_name or "").strip().lower()
        has_name_conflict = (
            "conflict" in detail
            and "container name" in detail
            and "already in use" in detail
        )
        if not has_name_conflict:
            return False
        if not name:
            return False
        if name in detail:
            return True
        return "[redacted]" in detail

    async def _cleanup_docker_sidecar_resources(
        self,
        session_id: str,
        *,
        ignore_failure: bool,
        remove_volumes_if_container_missing: bool = True,
    ) -> None:
        removed_container = await self._remove_container(
            self._sidecar_container_name(session_id),
            ignore_failure=ignore_failure,
        )
        if not removed_container and not remove_volumes_if_container_missing:
            return
        await self._remove_volume(
            self._sidecar_graph_volume_name(session_id),
            ignore_failure=ignore_failure,
        )
        await self._remove_volume(
            self._sidecar_socket_volume_name(session_id),
            ignore_failure=ignore_failure,
        )

    async def _launch_docker_sidecar(
        self,
        *,
        session_id: str,
        session_epoch: int,
        agent_run_id: str,
        docker_network: str | None,
    ) -> str:
        image = self._session_docker_sidecar_image()
        if not self._image_is_pinned(image):
            raise RuntimeError(
                "MOONMIND_MANAGED_SESSION_DOCKER_SIDECAR_IMAGE must be pinned "
                "to a non-latest tag or digest"
            )
        sidecar_name = self._sidecar_container_name(session_id)
        socket_volume = self._sidecar_socket_volume_name(session_id)
        graph_volume = self._sidecar_graph_volume_name(session_id)
        command = [
            self._docker_binary,
            "run",
            "-d",
            "--name",
            sidecar_name,
            "--privileged",
            "--label",
            "moonmind.kind=session-docker-sidecar",
            "--label",
            f"moonmind.session_id={session_id}",
            "--label",
            f"moonmind.session_epoch={session_epoch}",
            "--label",
            f"moonmind.agent_run_id={agent_run_id}",
            "--label",
            "moonmind.workload_mode=docker-sidecar",
            "-e",
            "DOCKER_TLS_CERTDIR=",
            "--mount",
            self._volume_mount(self._workspace_volume_name, self._workspace_root),
            "--mount",
            self._volume_mount(socket_volume, _SESSION_DOCKER_SOCKET_DIR),
            "--mount",
            self._volume_mount(graph_volume, _SESSION_DOCKER_GRAPH_PATH),
        ]
        if docker_network:
            command.extend(["--network", docker_network])
        command.extend(
            [
                image,
                "dockerd",
                f"--host=unix://{_SESSION_DOCKER_SOCKET_PATH}",
                f"--data-root={_SESSION_DOCKER_GRAPH_PATH}",
                f"--group={_MANAGED_SESSION_CONTAINER_GID}",
            ]
        )
        for attempt in range(2):
            await self._create_docker_sidecar_volumes(
                session_id=session_id,
                session_epoch=session_epoch,
                agent_run_id=agent_run_id,
            )
            try:
                stdout, _stderr = await self._run(command)
                break
            except RuntimeError as exc:
                if attempt == 0 and self._docker_name_conflict(exc, sidecar_name):
                    await self._cleanup_docker_sidecar_resources(
                        session_id,
                        ignore_failure=True,
                    )
                    continue
                raise
        else:  # pragma: no cover - loop always exits by break or raise.
            raise RuntimeError("docker sidecar run did not start")
        sidecar_id = stdout.strip()
        if not sidecar_id:
            raise RuntimeError("docker sidecar run returned a blank container id")
        await self._wait_docker_sidecar_ready(sidecar_id)
        await self._prepare_docker_sidecar_workspace_volume(sidecar_id)
        return sidecar_id

    async def _prepare_docker_sidecar_workspace_volume(
        self,
        sidecar_id: str,
    ) -> None:
        """Map the inner daemon's workspace volume to the mounted outer workspace."""

        inner_docker_command = (
            self._docker_binary,
            "exec",
            "-e",
            f"DOCKER_HOST=unix://{_SESSION_DOCKER_SOCKET_PATH}",
            sidecar_id,
            "docker",
        )
        inspect_command = (
            *inner_docker_command,
            "volume",
            "inspect",
            "--format",
            "{{json .Options}}",
            self._workspace_volume_name,
        )
        returncode, stdout, stderr = await self._command_runner(
            inspect_command,
            env=self._docker_env(),
        )
        existing_options: dict[str, Any] | None = None
        if returncode == 0:
            try:
                decoded_options = json.loads(stdout.strip() or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "failed to inspect the Docker sidecar workspace volume"
                ) from exc
            existing_options = (
                decoded_options if isinstance(decoded_options, dict) else {}
            )
        elif "no such volume" not in (stderr or stdout).lower():
            rendered_command, rendered_detail = self._scrub_command_failure(
                inspect_command,
                stderr.strip() or stdout.strip(),
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: "
                f"{rendered_detail}"
            )

        expected_options = {
            "device": self._workspace_root,
            "o": "bind",
            "type": "none",
        }
        if existing_options == expected_options:
            return
        if existing_options is not None:
            await self._run(
                (
                    *inner_docker_command,
                    "volume",
                    "rm",
                    "-f",
                    self._workspace_volume_name,
                )
            )
        await self._run(
            (
                *inner_docker_command,
                "volume",
                "create",
                "--driver",
                "local",
                "--opt",
                "type=none",
                "--opt",
                "o=bind",
                "--opt",
                f"device={self._workspace_root}",
                self._workspace_volume_name,
            )
        )

    async def _prepare_docker_sidecar_socket_volume(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> None:
        await self._create_volume(
            self._sidecar_socket_volume_name(request.session_id),
            labels=self._sidecar_volume_labels(
                session_id=request.session_id,
                role="docker-socket",
                agent_run_id=request.agent_run_id,
                session_epoch=request.session_epoch,
            ),
        )

    def _session_docker_config_path(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> tuple[Path, PurePosixPath]:
        host_path = (
            Path(request.session_workspace_path)
            / _SESSION_DOCKER_CONFIG_DIRNAME
            / _SESSION_DOCKER_CONFIG_FILENAME
        )
        container_path = (
            PurePosixPath(request.session_workspace_path)
            / _SESSION_DOCKER_CONFIG_DIRNAME
            / _SESSION_DOCKER_CONFIG_FILENAME
        )
        return host_path, container_path

    def _cleanup_session_docker_config(
        self,
        session_workspace_path: str,
    ) -> None:
        docker_config_dir = (
            Path(session_workspace_path) / _SESSION_DOCKER_CONFIG_DIRNAME
        )
        if not self._is_within_workspace_root(docker_config_dir):
            return
        config_path = docker_config_dir / _SESSION_DOCKER_CONFIG_FILENAME
        try:
            if config_path.exists():
                config_path.unlink()
            docker_config_dir.rmdir()
        except FileNotFoundError:
            return
        except OSError:
            logger.debug(
                "Could not remove session Docker config at %s",
                docker_config_dir,
                exc_info=True,
            )

    async def _configure_session_ghcr_pull_auth(
        self,
        request: LaunchCodexManagedSessionRequest,
        session_environment: dict[str, str],
    ) -> dict[str, Any]:
        credential_environment = dict(request.environment)
        credential_environment.update(session_environment)
        credentials = await resolve_ghcr_pull_credentials_for_launch(
            credential_environment,
            github_credential=request.github_credential,
        )
        diagnostics: dict[str, Any] = {
            "pullAuth": "anonymous",
            "registry": "ghcr.io",
            "dockerConfig": "not_materialized",
        }
        if credentials is None:
            return diagnostics

        username, token = credentials
        host_config_path, container_config_path = self._session_docker_config_path(
            request
        )
        self._validate_workspace_path(
            str(host_config_path),
            field_name="dockerConfigPath",
        )
        auth_payload = base64.b64encode(
            f"{username}:{token}".encode("utf-8")
        ).decode("ascii")
        config_payload = {
            "auths": {
                "ghcr.io": {
                    "auth": auth_payload,
                }
            }
        }
        host_config_path.parent.mkdir(parents=True, exist_ok=True)
        # Docker requires plaintext registry auth in config.json; this file is
        # session-scoped, mode 0600, owned by the agent user, and cleaned up.
        # codeql[py/clear-text-storage-sensitive-data]
        config_json = json.dumps(config_payload, sort_keys=True) + "\n"
        # Docker requires plaintext registry auth in config.json; this file is
        # session-scoped, mode 0600, owned by the agent user, and cleaned up.
        # codeql[py/clear-text-storage-sensitive-data]
        host_config_path.write_text(
            config_json,  # codeql[py/clear-text-storage-sensitive-data] # lgtm[py/clear-text-storage-sensitive-data]
            encoding="utf-8",
        )
        geteuid = getattr(os, "geteuid", None)
        if os.name == "posix" and callable(geteuid) and geteuid() == 0:
            try:
                os.chown(
                    host_config_path,
                    _MANAGED_SESSION_CONTAINER_UID,
                    _MANAGED_SESSION_CONTAINER_GID,
                )
            except OSError:
                logger.debug(
                    "Could not chown session Docker config at %s",
                    host_config_path,
                    exc_info=True,
                )
        try:
            host_config_path.chmod(0o600)
        except OSError:
            logger.debug(
                "Could not chmod session Docker config at %s",
                host_config_path,
                exc_info=True,
            )
        session_environment.pop("GHCR_PULL_USER", None)
        session_environment.pop("GHCR_PULL_TOKEN", None)
        session_environment["DOCKER_CONFIG"] = str(container_config_path.parent)
        return {
            **diagnostics,
            "pullAuth": "authenticated",
            "dockerConfig": "session_workspace",
            "dockerConfigPath": str(container_config_path),
        }

    @staticmethod
    def _docker_manifest_probe_image_ref(
        request: LaunchCodexManagedSessionRequest,
    ) -> str | None:
        capability = request.docker_capability
        raw = (
            getattr(capability, "manifest_image_ref", None)
            if capability is not None
            else None
        )
        if not raw:
            raw = request.environment.get("MOONMIND_DOCKER_PREFLIGHT_IMAGE_REF")
        text = str(raw or "").strip()
        return text or None

    @staticmethod
    def _image_ref_is_pinned_digest(image_ref: str) -> bool:
        return "@sha256:" in str(image_ref or "").strip()

    async def _preflight_docker_manifest_probe(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        pull_auth_diagnostics: Mapping[str, Any],
    ) -> dict[str, Any]:
        image_ref = self._docker_manifest_probe_image_ref(request)
        if not image_ref:
            return {"status": "skipped", "reason": "no_manifest_image_ref"}
        if not self._image_ref_is_pinned_digest(image_ref):
            raise RuntimeError(
                "Docker preflight image ref must be pinned to a digest"
            )
        docker_config_path, _container_config_path = self._session_docker_config_path(
            request
        )
        docker_config_dir = docker_config_path.parent
        command = [
            self._docker_binary,
            "manifest",
            "inspect",
            image_ref,
        ]
        extra_env: dict[str, str] | None = None
        if pull_auth_diagnostics.get("pullAuth") == "authenticated":
            extra_env = {"DOCKER_CONFIG": str(docker_config_dir)}
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            env={**self._docker_env(), **(extra_env or {})},
        )
        detail = (stderr.strip() or stdout.strip())[:500]
        if returncode != 0:
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                detail,
                extra_env=extra_env,
            )
            raise RuntimeError(
                "Docker preflight manifest probe failed: "
                f"{rendered_command}: {rendered_detail}"
            )
        return {
            "status": "passed",
            "imageRef": image_ref,
            "pullAuth": str(pull_auth_diagnostics.get("pullAuth") or "anonymous"),
        }

    async def _wait_docker_sidecar_ready(self, sidecar_id: str) -> None:
        command = (
            self._docker_binary,
            "exec",
            "-e",
            f"DOCKER_HOST=unix://{_SESSION_DOCKER_SOCKET_PATH}",
            sidecar_id,
            "docker",
            "info",
            "--format",
            "{{json .ServerVersion}}",
        )
        last_error = ""
        attempts = max(1, self._ready_poll_attempts)
        for attempt in range(attempts):
            returncode, stdout, stderr = await self._command_runner(
                command,
                env=self._docker_env(),
            )
            if returncode == 0 and stdout.strip():
                return
            last_error = stderr.strip() or stdout.strip() or f"exit code {returncode}"
            if attempt + 1 < attempts:
                await asyncio.sleep(self._ready_poll_interval_seconds)
        raise RuntimeError(f"Docker sidecar daemon did not become ready: {last_error}")

    def _validate_workspace_path(self, value: str, *, field_name: str) -> None:
        workspace_root = _normalize_absolute_posix_path(
            self._workspace_root,
            field_name="workspace_root",
        )
        candidate = _normalize_absolute_posix_path(value, field_name=field_name)
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise RuntimeError(
                f"{field_name} must stay within workspace_root {workspace_root}: {candidate}"
            ) from exc

    def _is_within_workspace_root(self, path: Path) -> bool:
        workspace_root = Path(self._workspace_root).expanduser().resolve()
        candidate = path.expanduser().resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return False
        return True

    def _validate_launch_request(self, request: LaunchCodexManagedSessionRequest) -> None:
        self._validate_workspace_path(request.workspace_path, field_name="workspacePath")
        self._validate_workspace_path(
            request.session_workspace_path,
            field_name="sessionWorkspacePath",
        )
        self._validate_workspace_path(
            request.artifact_spool_path,
            field_name="artifactSpoolPath",
        )
        _normalize_absolute_posix_path(
            request.codex_home_path,
            field_name="codexHomePath",
        )
        reserved_keys = sorted(
            key
            for key in request.environment
            if key.startswith(_RESERVED_SESSION_ENV_PREFIX)
        )
        if reserved_keys:
            raise RuntimeError(
                "launch_session environment cannot override reserved session keys: "
                + ", ".join(reserved_keys)
            )
        auth_volume_path = str(
            request.environment.get("MANAGED_AUTH_VOLUME_PATH") or ""
        ).strip()
        if auth_volume_path:
            normalized_auth_volume_path = _normalize_absolute_posix_path(
                auth_volume_path,
                field_name="environment.MANAGED_AUTH_VOLUME_PATH",
            )
            normalized_codex_home_path = _normalize_absolute_posix_path(
                request.codex_home_path,
                field_name="codexHomePath",
            )
            if normalized_auth_volume_path == normalized_codex_home_path:
                raise RuntimeError(
                    "environment.MANAGED_AUTH_VOLUME_PATH must not equal codexHomePath"
                )

    @staticmethod
    def _volume_mount(volume_name: str, target_path: str) -> str:
        return f"type=volume,src={volume_name},dst={target_path}"

    @staticmethod
    def _write_executable_script(path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    @staticmethod
    def _render_gh_wrapper_script(*, socket_path: str) -> str:
        return render_gh_wrapper_script(socket_path=socket_path)

    @staticmethod
    def _render_git_credential_helper_script(*, socket_path: str) -> str:
        return render_git_credential_helper_script(socket_path=socket_path)

    @staticmethod
    def _format_git_config_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @classmethod
    def _persist_brokered_github_config(
        cls,
        session_environment: dict[str, str],
        *,
        workspace_path: str,
        support_root: Path,
        github_socket_path: str,
    ) -> list[Path]:
        """Persist broker-backed git/gh config visible inside the session container."""

        bin_dir = support_root / "bin"
        git_config_path = support_root / "gitconfig"
        git_helper_path = bin_dir / "git-credential-moonmind"
        gh_wrapper_path = bin_dir / "gh"
        touched_paths: list[Path] = [
            support_root,
            bin_dir,
            git_config_path,
            git_helper_path,
        ]

        support_root.mkdir(parents=True, exist_ok=True)
        bin_dir.mkdir(parents=True, exist_ok=True)
        cls._write_executable_script(
            git_helper_path,
            cls._render_git_credential_helper_script(socket_path=github_socket_path),
        )
        touched_paths.append(gh_wrapper_path)
        cls._write_executable_script(
            gh_wrapper_path,
            cls._render_gh_wrapper_script(socket_path=github_socket_path),
        )

        git_config_lines = [
            "# moonmind-managed-git-config\n",
        ]
        existing_global_git_config = str(
            session_environment.get("GIT_CONFIG_GLOBAL") or ""
        ).strip()
        if (
            existing_global_git_config
            and Path(existing_global_git_config) != git_config_path
        ):
            git_config_lines.extend(
                [
                    "[include]\n",
                    (
                        "\tpath = "
                        f"{cls._format_git_config_value(existing_global_git_config)}\n"
                    ),
                ]
            )
        git_config_lines.extend(
            [
                "[safe]\n",
                f"\tdirectory = {cls._format_git_config_value(str(workspace_path))}\n",
                "[credential]\n",
                f"\thelper = !{shlex.quote(str(git_helper_path))}\n",
            ]
        )
        git_config_path.write_text("".join(git_config_lines), encoding="utf-8")
        git_config_path.chmod(0o600)

        existing_path = str(session_environment.get("PATH") or "").strip()
        system_paths = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        session_environment["PATH"] = (
            f"{bin_dir}{os.pathsep}{existing_path}"
            if existing_path
            else f"{bin_dir}{os.pathsep}{system_paths}"
        )
        session_environment["GIT_CONFIG_GLOBAL"] = str(git_config_path)
        session_environment.setdefault("GIT_TERMINAL_PROMPT", "0")

        repo_git_config_path = Path(workspace_path) / ".git" / "config"
        if repo_git_config_path.exists():
            marker = "# moonmind-credential-helper"
            existing_config = repo_git_config_path.read_text(encoding="utf-8")
            if marker not in existing_config:
                credential_section = (
                    f"\n{marker}\n"
                    "[credential]\n"
                    f"\thelper = !{shlex.quote(str(git_helper_path))}\n"
                )
                repo_git_config_path.write_text(
                    existing_config + credential_section,
                    encoding="utf-8",
                )
                touched_paths.append(repo_git_config_path)

        return touched_paths

    async def _configure_session_github_auth(
        self,
        request: LaunchCodexManagedSessionRequest,
        session_environment: dict[str, str],
    ) -> dict[str, str]:
        token = await resolve_github_token_for_launch(
            request.environment,
            github_credential=request.github_credential,
        )
        token = str(token or "").strip()
        if not token:
            return {}

        support_root = Path(request.session_workspace_path) / ".moonmind"
        socket_path = self._build_github_socket_path(
            run_id=request.session_id,
            support_root=str(support_root),
        )
        socket_dir = Path(socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)
        await self._github_auth_brokers.start(
            run_id=request.session_id,
            token=token,
            socket_path=socket_path,
        )
        touched_paths = self._persist_brokered_github_config(
            session_environment,
            workspace_path=request.workspace_path,
            support_root=support_root,
            github_socket_path=socket_path,
        )
        touched_paths.append(socket_dir)
        touched_paths.append(Path(socket_path))
        self._normalize_container_path_owners(touched_paths)
        # Codex shell tools can invoke nested `bash -lc` commands that bypass
        # the workspace-local gh wrapper. Bind the token through Docker's
        # inherited environment (`-e GITHUB_TOKEN`) so it is not rendered into
        # the docker command line or the launch payload.
        return {"GITHUB_TOKEN": token}

    @staticmethod
    def _build_github_socket_path(
        *,
        run_id: str,
        support_root: str | None,
        socket_root: str | None = None,
    ) -> str:
        """Keep broker sockets on a short path to avoid AF_UNIX length limits."""
        return build_github_socket_path(
            run_id=run_id,
            support_root=support_root,
            socket_root=socket_root,
        )

    @staticmethod
    def _record_status_from_handle_status(status: str) -> ManagedSessionRecordStatus:
        normalized = str(status or "").strip().lower()
        if normalized in {"launching", "ready", "busy", "terminating", "terminated", "failed"}:
            return normalized
        if normalized in {"clearing", "interrupted"}:
            return "ready"
        return "ready"

    @staticmethod
    def _record_status_from_turn_status(status: str) -> ManagedSessionRecordStatus:
        normalized = str(status or "").strip().lower()
        if normalized in {"accepted", "running"}:
            return "busy"
        if normalized in {"completed", "interrupted"}:
            return "ready"
        if normalized == "failed":
            return "failed"
        return "busy"

    @staticmethod
    def _handle_status_from_record_status(
        status: ManagedSessionRecordStatus | str,
    ) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"launching", "ready", "busy", "terminating", "terminated"}:
            return normalized
        if normalized in {"degraded", "failed"}:
            return "failed"
        return "ready"

    @staticmethod
    def _request_matches_record(
        request: LaunchCodexManagedSessionRequest,
        record: CodexManagedSessionRecord,
    ) -> bool:
        return (
            request.agent_run_id == record.agent_run_id
            and request.session_id == record.session_id
            and request.session_epoch == record.session_epoch
            and request.thread_id == record.thread_id
            and request.workspace_path == record.workspace_path
            and request.session_workspace_path == record.session_workspace_path
            and request.artifact_spool_path == record.artifact_spool_path
            and (request.image_ref == record.image_ref)
        )

    @staticmethod
    def _turn_error_message(response: CodexManagedSessionTurnResponse) -> str | None:
        if response.status != "failed":
            return None
        for key in ("errorMessage", "reason", "error"):
            value = response.metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _merge_capability_metadata(
        metadata: Mapping[str, Any],
        capability_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata)
        merged.update(dict(capability_metadata))
        existing_capabilities = metadata.get("capabilities")
        next_capabilities = capability_metadata.get("capabilities")
        if isinstance(existing_capabilities, Mapping):
            capabilities = dict(existing_capabilities)
            if isinstance(next_capabilities, Mapping):
                capabilities.update(dict(next_capabilities))
            merged["capabilities"] = capabilities
        elif isinstance(next_capabilities, Mapping):
            merged["capabilities"] = dict(next_capabilities)
        return merged

    async def _run_docker_capability_probe(
        self,
        *,
        container_id: str,
        args: tuple[str, ...],
        docker_host: str | None = None,
    ) -> tuple[bool, str]:
        command: list[str] = [
            self._docker_binary,
            "exec",
        ]
        if docker_host:
            command.extend(("-e", f"DOCKER_HOST={docker_host}"))
        command.extend((container_id, "docker", *args))
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            env=self._docker_env(),
        )
        detail = (stdout.strip() or stderr.strip())[:500]
        return returncode == 0, detail

    async def _evaluate_docker_capability_once(
        self,
        *,
        container_id: str,
        request: LaunchCodexManagedSessionRequest,
        capability: ManagedSessionDockerCapabilityRequest,
    ) -> dict[str, Any]:
        docker_host = (
            capability.docker_host
            or request.environment.get("DOCKER_HOST")
            or ""
        )
        checks: dict[str, str] = {}
        version_ok, version_text = await self._run_docker_capability_probe(
            container_id=container_id,
            args=("version", "--format", "{{.Server.Version}}"),
            docker_host=docker_host,
        )
        checks["dockerVersion"] = "passed" if version_ok else "failed"
        info_ok, _info_text = await self._run_docker_capability_probe(
            container_id=container_id,
            args=("info",),
            docker_host=docker_host,
        )
        checks["dockerInfo"] = "passed" if info_ok else "failed"
        compose_available = False
        if capability.compose_support:
            compose_ok, _compose_text = await self._run_docker_capability_probe(
                container_id=container_id,
                args=("compose", "version"),
                docker_host=docker_host,
            )
            checks["dockerCompose"] = "passed" if compose_ok else "failed"
            compose_available = compose_ok

        available = version_ok and info_ok and (
            compose_available if capability.compose_support else True
        )
        docker_status: dict[str, Any] = {
            "available": available,
            "mode": capability.mode,
            "dockerHost": docker_host,
            "composeAvailable": compose_available,
            "daemon": {
                "ready": available,
                "version": version_text if version_ok else "",
            },
            "checks": checks,
        }
        if not available:
            docker_status.update(
                {
                    "reason": "sidecar_not_ready",
                    "message": (
                        "Docker daemon did not become ready before session launch."
                    ),
                }
            )
        return {"capabilities": {"docker": docker_status}}

    async def _evaluate_docker_capability(
        self,
        *,
        container_id: str,
        request: LaunchCodexManagedSessionRequest,
    ) -> dict[str, Any]:
        capability = request.docker_capability
        if capability is None:
            return {}
        deadline = time.monotonic() + capability.timeout_seconds
        last_status: dict[str, Any] = {}
        while True:
            last_status = await self._evaluate_docker_capability_once(
                container_id=container_id,
                request=request,
                capability=capability,
            )
            docker_status = (
                last_status.get("capabilities", {}).get("docker", {})
                if isinstance(last_status, dict)
                else {}
            )
            if docker_status.get("available") is True:
                return last_status
            if time.monotonic() >= deadline:
                break
            if capability.interval_seconds > 0:
                await asyncio.sleep(capability.interval_seconds)
            else:
                await asyncio.sleep(0)

        if capability.activation == "on_launch":
            raise RuntimeError("sidecar_not_ready: Docker capability is required")
        return last_status

    async def ensure_docker_sidecar(
        self,
        request: ManagedSessionEnsureDockerSidecarRequest,
    ) -> ManagedSessionEnsureDockerSidecarResponse:
        record: CodexManagedSessionRecord | None = None
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is None:
                raise RuntimeError(
                    f"managed session record not found: {request.session_id}"
                )
            if record.session_epoch != request.session_epoch:
                raise RuntimeError(
                    "sessionEpoch does not match the durable managed session record"
                )
            if record.container_id != request.container_id:
                raise RuntimeError(
                    "containerId does not match the durable managed session record"
                )
            if request.thread_id is not None and record.thread_id != request.thread_id:
                raise RuntimeError(
                    "threadId does not match the durable managed session record"
                )
            metadata = dict(record.metadata)
            docker_status = metadata.get("capabilities", {}).get("docker", {})
            if (
                isinstance(docker_status, Mapping)
                and docker_status.get("allowed") is False
            ):
                return ManagedSessionEnsureDockerSidecarResponse(
                    state="not_allowed",
                    dockerHost=None,
                    mode=str(docker_status.get("mode") or "sidecar-dind"),
                    composeAvailable=False,
                    daemon={"ready": False, "version": ""},
                    metadata={"reason": "docker_not_allowed"},
                )
            docker_network = str(metadata.get("dockerNetwork") or "").strip() or None
            agent_run_id = record.agent_run_id
        else:
            metadata = {}
            docker_network = self._network_name
            agent_run_id = request.session_id

        if not await self._container_exists(request.container_id):
            raise RuntimeError("managed session container is not running")

        sidecar_name = self._sidecar_container_name(request.session_id)
        if not await self._container_exists(sidecar_name):
            await self._launch_docker_sidecar(
                session_id=request.session_id,
                session_epoch=request.session_epoch,
                agent_run_id=agent_run_id,
                docker_network=docker_network,
            )
        else:
            await self._run((self._docker_binary, "start", sidecar_name))
            await self._wait_docker_sidecar_ready(sidecar_name)
            await self._prepare_docker_sidecar_workspace_volume(sidecar_name)

        probe_capability = ManagedSessionDockerCapabilityRequest(
            allowed=True,
            activation="on_launch",
            mode="sidecar-dind",
            dockerHost=f"unix://{_SESSION_DOCKER_SOCKET_PATH}",
            composeSupport=request.compose_required,
        )
        probe_request = LaunchCodexManagedSessionRequest(
            agentRunId=agent_run_id,
            sessionId=request.session_id,
            sessionEpoch=request.session_epoch,
            threadId=request.thread_id or (record.thread_id if record else "thread"),
            workspacePath=record.workspace_path if record else self._workspace_root,
            sessionWorkspacePath=(
                record.session_workspace_path if record else self._workspace_root
            ),
            artifactSpoolPath=(
                record.artifact_spool_path if record else self._workspace_root
            ),
            codexHomePath="/home/app/.codex",
            imageRef=record.image_ref if record else "managed-session",
            environment={"DOCKER_HOST": f"unix://{_SESSION_DOCKER_SOCKET_PATH}"},
            dockerCapability=probe_capability,
        )
        capability_metadata = await self._evaluate_docker_capability(
            container_id=request.container_id,
            request=probe_request,
        )
        docker_status = capability_metadata.get("capabilities", {}).get("docker", {})
        if (
            not isinstance(docker_status, Mapping)
            or docker_status.get("available") is not True
        ):
            return ManagedSessionEnsureDockerSidecarResponse(
                state="failed",
                dockerHost=f"unix://{_SESSION_DOCKER_SOCKET_PATH}",
                mode="sidecar-dind",
                composeAvailable=False,
                daemon={"ready": False, "version": ""},
                metadata={"capabilities": {"docker": docker_status}},
            )

        response = ManagedSessionEnsureDockerSidecarResponse(
            state="ready",
            dockerHost=str(docker_status.get("dockerHost") or ""),
            mode=str(docker_status.get("mode") or "sidecar-dind"),
            composeAvailable=bool(docker_status.get("composeAvailable")),
            daemon=docker_status.get("daemon") or {"ready": True, "version": ""},
            metadata={
                "capabilities": {
                    "docker": {**dict(docker_status), "state": "ready"}
                }
            },
        )
        if record is not None and self._session_store is not None:
            next_metadata = self._merge_capability_metadata(
                metadata,
                {
                    "capabilities": {
                        "docker": {
                            **dict(docker_status),
                            "allowed": True,
                            "activation": "on_demand",
                            "state": "ready",
                        }
                    }
                },
            )
            self._session_store.save(
                record.model_copy(
                    update={
                        "metadata": next_metadata,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        return response

    @staticmethod
    def _locator_from_session_state(
        session_state,
    ) -> CodexManagedSessionLocator:
        return CodexManagedSessionLocator(
            sessionId=session_state.session_id,
            sessionEpoch=session_state.session_epoch,
            containerId=session_state.container_id,
            threadId=session_state.thread_id,
        )

    def _record_from_launch(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        handle: CodexManagedSessionHandle,
    ) -> CodexManagedSessionRecord:
        now = datetime.now(tz=UTC)
        return CodexManagedSessionRecord(
            sessionId=request.session_id,
            sessionEpoch=handle.session_state.session_epoch,
            agentRunId=request.agent_run_id,
            containerId=handle.session_state.container_id,
            threadId=handle.session_state.thread_id,
            activeTurnId=handle.session_state.active_turn_id,
            runtimeId=(
                "claude_code"
                if request.runtime_family == "claude_code"
                else "codex_cli"
            ),
            imageRef=handle.image_ref or request.image_ref,
            controlUrl=handle.control_url or f"docker-exec://{handle.session_state.container_id}",
            status=self._record_status_from_handle_status(handle.status),
            workspacePath=request.workspace_path,
            sessionWorkspacePath=request.session_workspace_path,
            artifactSpoolPath=request.artifact_spool_path,
            metadata=dict(request.metadata),
            startedAt=now,
            updatedAt=now,
        )

    @staticmethod
    def _with_runtime_family(response: Any, request: Any) -> Any:
        runtime_family = getattr(request, "runtime_family", None)
        if not runtime_family:
            return response
        if getattr(response, "runtime_family", None) == runtime_family:
            return response
        return response.model_copy(update={"runtime_family": runtime_family})

    @staticmethod
    def _matches_locator(
        record: CodexManagedSessionRecord,
        locator: CodexManagedSessionLocator,
    ) -> None:
        if record.session_epoch != locator.session_epoch:
            raise RuntimeError("sessionEpoch does not match the durable managed session record")
        if record.container_id != locator.container_id:
            raise RuntimeError("containerId does not match the durable managed session record")
        if record.thread_id != locator.thread_id:
            raise RuntimeError("threadId does not match the durable managed session record")

    def _require_record(
        self,
        locator: CodexManagedSessionLocator,
    ) -> CodexManagedSessionRecord | None:
        if self._session_store is None:
            return None
        record = self._session_store.load(locator.session_id)
        if record is None:
            raise RuntimeError(f"managed session record not found: {locator.session_id}")
        self._matches_locator(record, locator)
        return record

    @staticmethod
    def _command_secrets(
        command: Sequence[str],
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> list[str]:
        secrets: list[str] = []

        def _append_assignment(assignment: str) -> None:
            if "=" not in assignment:
                return
            key, value = assignment.split("=", 1)
            if _is_sensitive_env_key(key) and value:
                secrets.append(value)

        for index, part in enumerate(command):
            if part in {"-e", "--env"} and index + 1 < len(command):
                _append_assignment(command[index + 1])
                continue
            if part.startswith("--env="):
                _append_assignment(part[len("--env="):])

        for key, value in (extra_env or {}).items():
            if _is_sensitive_env_key(str(key)) and value:
                secrets.append(str(value))

        return secrets

    @classmethod
    def _scrub_command_failure(
        cls,
        command: Sequence[str],
        detail: str,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        redactor = SecretRedactor.from_environ(
            placeholder="[REDACTED]",
            extra_secrets=cls._command_secrets(command, extra_env=extra_env),
        )
        rendered_command = scrub_github_tokens(redactor.scrub(" ".join(command)))
        rendered_detail = scrub_github_tokens(redactor.scrub(detail))
        return rendered_command, rendered_detail

    @staticmethod
    def _transport_output_snippet(text: str, *, max_chars: int = 500) -> str:
        return json.dumps(text[:max_chars], ensure_ascii=True)

    def _raise_transport_failure(
        self,
        command: Sequence[str],
        *,
        action: str,
        container_id: str,
        session_id: str | None = None,
        reason: str,
        stdout: str | None = None,
        stderr: str,
        extra_env: Mapping[str, str] | None = None,
        cause: Exception | None = None,
    ) -> None:
        target_label = (
            f"managed-session action {action} for session {session_id} "
            f"in container {container_id}"
            if session_id
            else f"managed-session action {action} in container {container_id}"
        )
        rendered_detail = (
            "stdout was blank"
            if stdout is None
            else f"stdout={self._transport_output_snippet(stdout)}"
        )
        stderr_text = stderr.strip()
        if stderr_text:
            rendered_detail += (
                f"; stderr: {self._transport_output_snippet(stderr_text)}"
            )
        rendered_command, scrubbed_detail = self._scrub_command_failure(
            command,
            rendered_detail,
            extra_env=extra_env,
        )
        error = RuntimeError(
            f"{target_label} {reason} via {rendered_command}: {scrubbed_detail}"
        )
        if cause is not None:
            raise error from cause
        raise error

    async def _remove_container(
        self,
        container_identifier: str,
        *,
        ignore_failure: bool,
    ) -> bool:
        try:
            await self._run((self._docker_binary, "rm", "-f", container_identifier))
            return True
        except RuntimeError:
            if not ignore_failure:
                raise
            return False

    async def _connect_container_network(
        self,
        *,
        container_id: str,
        network_name: str,
    ) -> None:
        await self._run(
            (
                self._docker_binary,
                "network",
                "connect",
                network_name,
                container_id,
            )
        )

    async def _run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        env = self._docker_env()
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            input_text=input_text,
            env=env,
        )
        if returncode != 0:
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                stderr.strip() or stdout.strip(),
                extra_env=extra_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: {rendered_detail}"
            )
        return stdout, stderr

    async def _run_host_command(
        self,
        command: Sequence[str],
        *,
        extra_env: Mapping[str, str] | None = None,
        run_as_managed_session_user: bool = False,
    ) -> tuple[str, str]:
        env = None
        if extra_env:
            env = {str(key): str(value) for key, value in extra_env.items()}
        command_kwargs = (
            self._managed_session_user_command_kwargs()
            if run_as_managed_session_user
            else {}
        )
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            env=env,
            **command_kwargs,
        )
        if returncode != 0:
            rendered_command, rendered_detail = self._scrub_command_failure(
                command,
                stderr.strip() or stdout.strip(),
                extra_env=extra_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: {rendered_detail}"
            )
        return stdout, stderr

    @staticmethod
    async def _git_host_environment(
        request: LaunchCodexManagedSessionRequest | None = None,
    ) -> dict[str, str]:
        env = dict(_GIT_COMMAND_LOCALE)
        request_env = request.environment if request is not None else {}
        try:
            token = (
                await resolve_github_token_for_launch(
                    request_env,
                    github_credential=(
                        request.github_credential if request is not None else None
                    ),
                )
                if request is not None
                else None
            )
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        token = str(token or "").strip()
        if token:
            # Avoid depending on persistent per-user gh setup in the worker. The
            # clone happens before the managed agent container starts.
            return build_github_token_git_environment(
                token,
                base_env=env,
                terminal_prompt=str(request_env.get("GIT_TERMINAL_PROMPT") or "0"),
            )

        terminal_prompt = str(request_env.get("GIT_TERMINAL_PROMPT") or "").strip()
        if terminal_prompt:
            env["GIT_TERMINAL_PROMPT"] = terminal_prompt
        return env

    async def _run_git_host_command(
        self,
        command: Sequence[str],
        *,
        request: LaunchCodexManagedSessionRequest | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        if git_env is None:
            git_env = await self._git_host_environment(request)
        return await self._run_host_command(
            command,
            extra_env=git_env,
            run_as_managed_session_user=True,
        )

    @staticmethod
    def _workspace_git_command(
        workspace_path: Path,
        *args: str,
    ) -> list[str]:
        resolved_workspace = str(workspace_path.resolve())
        return [
            "git",
            "-c",
            f"safe.directory={resolved_workspace}",
            "-C",
            resolved_workspace,
            *args,
        ]

    async def _git_command_result(
        self,
        command: Sequence[str],
        *,
        request: LaunchCodexManagedSessionRequest | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        command_kwargs = self._managed_session_user_command_kwargs()
        if git_env is None:
            git_env = await self._git_host_environment(request)
        return await self._command_runner(
            tuple(command),
            env=git_env,
            **command_kwargs,
        )

    async def _workspace_is_git_repository(self, *, workspace_path: Path) -> bool:
        returncode, stdout, _stderr = await self._git_command_result(
            self._workspace_git_command(
                workspace_path,
                "rev-parse",
                "--is-inside-work-tree",
            )
        )
        return returncode == 0 and stdout.strip() == "true"

    async def _remove_workspace_path(self, *, workspace_path: Path) -> None:
        if not workspace_path.exists():
            return
        if workspace_path.is_dir():
            shutil.rmtree(workspace_path)
            return
        workspace_path.unlink()

    async def _clone_workspace(
        self,
        *,
        workspace_path: Path,
        request: LaunchCodexManagedSessionRequest,
        repository: str,
        git_env: Mapping[str, str],
    ) -> None:
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        await self._remove_workspace_path(workspace_path=workspace_path)
        self._normalize_container_path_owner(workspace_path.parent)

        from .launcher import ManagedRuntimeLauncher

        source = ManagedRuntimeLauncher._resolve_repository_source(repository)
        branch = str(
            request.workspace_spec.get("startingBranch")
            or request.workspace_spec.get("branch")
            or ""
        ).strip()
        branch = ManagedRuntimeLauncher._normalize_clone_branch(branch)

        clone_command = ["git", "clone"]
        if branch:
            clone_command.extend(["--branch", branch, "--single-branch"])
        clone_command.extend([source, str(workspace_path)])
        await self._run_git_host_command(
            clone_command,
            request=request,
            git_env=git_env,
        )
        self._normalize_container_path_ownership((workspace_path,))

    @staticmethod
    def _branch_missing_checkout_failure(detail: str) -> bool:
        normalized = detail.lower()
        return (
            "did not match any file(s) known to git" in normalized
            or "pathspec" in normalized
        )

    @staticmethod
    def _remote_branch_missing_failure(detail: str) -> bool:
        normalized = detail.lower()
        return (
            "couldn't find remote ref" in normalized
            or "remote ref does not exist" in normalized
        )

    async def _ensure_workspace_paths(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> None:
        workspace_path = Path(request.workspace_path)
        session_workspace_path = Path(request.session_workspace_path)
        artifact_spool_path = Path(request.artifact_spool_path)
        support_root_paths: list[Path] = [
            session_workspace_path,
            artifact_spool_path,
        ]
        recursive_support_paths: list[Path] = []

        session_workspace_path.mkdir(parents=True, exist_ok=True)
        artifact_spool_path.mkdir(parents=True, exist_ok=True)
        self._collect_managed_support_paths(
            request=request,
            owned_root_paths=support_root_paths,
            recursively_owned_paths=recursive_support_paths,
        )
        self._normalize_container_path_owners(support_root_paths)
        self._normalize_container_path_ownership(recursive_support_paths)

        repository = str(
            request.workspace_spec.get("repository")
            or request.workspace_spec.get("repo")
            or ""
        ).strip()
        git_env = (
            await self._git_host_environment(request)
            if repository
            else None
        )
        if workspace_path.exists():
            self._normalize_container_path_ownership([workspace_path])
            if repository:
                if not await self._workspace_is_git_repository(workspace_path=workspace_path):
                    await self._clone_workspace(
                        workspace_path=workspace_path,
                        request=request,
                        repository=repository,
                        git_env=git_env,
                    )
                await self._ensure_target_branch(
                    workspace_path=workspace_path,
                    request=request,
                    git_env=git_env,
                )
            elif (
                request.workspace_spec.get("targetBranch")
                and await self._workspace_is_git_repository(
                    workspace_path=workspace_path
                )
            ):
                await self._ensure_target_branch(
                    workspace_path=workspace_path,
                    request=request,
                    git_env=await self._git_host_environment(request),
                )
            return

        if not repository:
            workspace_path.parent.mkdir(parents=True, exist_ok=True)
            workspace_path.mkdir(parents=True, exist_ok=True)
            self._normalize_container_path_ownership([workspace_path])
            return

        await self._clone_workspace(
            workspace_path=workspace_path,
            request=request,
            repository=repository,
            git_env=git_env,
        )
        await self._ensure_target_branch(
            workspace_path=workspace_path,
            request=request,
            git_env=git_env,
        )

    async def ensure_repo_artifacts_writable_by_runtime_user(
        self,
        workspace_path: str,
        /,
    ) -> None:
        """Normalize repo-local artifacts created after session launch."""

        resolved_workspace = Path(workspace_path).expanduser().resolve()
        if not self._is_within_workspace_root(resolved_workspace):
            raise RuntimeError(
                "managed session workspace path must be within the configured "
                f"workspace root: {resolved_workspace}"
            )

        artifacts_path = resolved_workspace / "artifacts"
        try:
            artifacts_stat = artifacts_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(artifacts_stat.st_mode):
            raise RuntimeError(
                f"repo-local artifacts path must not be a symlink: {artifacts_path}"
            )
        if not stat.S_ISDIR(artifacts_stat.st_mode):
            raise RuntimeError(
                f"repo-local artifacts path must be a directory: {artifacts_path}"
            )
        self._normalize_container_directory_ownership_no_follow(artifacts_path)

    def _collect_managed_support_paths(
        self,
        *,
        request: LaunchCodexManagedSessionRequest,
        owned_root_paths: list[Path],
        recursively_owned_paths: list[Path],
    ) -> None:
        codex_home_path = Path(request.codex_home_path)
        if not self._is_within_workspace_root(codex_home_path):
            return

        runtime_support_path = codex_home_path.parent
        if not runtime_support_path.exists():
            runtime_support_path.mkdir(parents=True, exist_ok=True)
        owned_root_paths.append(runtime_support_path)

        if not codex_home_path.exists():
            codex_home_path.mkdir(parents=True, exist_ok=True)
        recursively_owned_paths.append(codex_home_path)

    async def _ensure_target_branch(
        self,
        *,
        workspace_path: Path,
        request: LaunchCodexManagedSessionRequest,
        git_env: Mapping[str, str],
    ) -> None:
        target_branch = str(request.workspace_spec.get("targetBranch") or "").strip()
        if not target_branch:
            return

        checkout_command = tuple(
            self._workspace_git_command(
                workspace_path,
                "checkout",
                target_branch,
            )
        )
        returncode, stdout, stderr = await self._git_command_result(
            checkout_command,
            request=request,
            git_env=git_env,
        )
        if returncode == 0:
            return

        failure_detail = stderr or stdout
        if not self._branch_missing_checkout_failure(failure_detail):
            rendered_command, rendered_detail = self._scrub_command_failure(
                checkout_command,
                stderr.strip() or stdout.strip(),
                extra_env=git_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {returncode}: "
                f"{rendered_detail}"
            )

        fetch_command = self._workspace_git_command(
            workspace_path,
            "fetch",
            "origin",
            f"+refs/heads/{target_branch}:refs/remotes/origin/{target_branch}",
        )
        fetch_returncode, fetch_stdout, fetch_stderr = await self._git_command_result(
            fetch_command,
            request=request,
            git_env=git_env,
        )
        if fetch_returncode == 0:
            await self._run_git_host_command(
                self._workspace_git_command(
                    workspace_path,
                    "checkout",
                    "-B",
                    target_branch,
                    f"origin/{target_branch}",
                ),
                request=request,
                git_env=git_env,
            )
            return

        fetch_detail = fetch_stderr or fetch_stdout
        if not self._remote_branch_missing_failure(fetch_detail):
            rendered_command, rendered_detail = self._scrub_command_failure(
                fetch_command,
                fetch_stderr.strip() or fetch_stdout.strip(),
                extra_env=git_env,
            )
            raise RuntimeError(
                f"{rendered_command} failed with exit code {fetch_returncode}: "
                f"{rendered_detail}"
            )

        await self._run_git_host_command(
            self._workspace_git_command(
                workspace_path,
                "checkout",
                "-b",
                target_branch,
            ),
            request=request,
            git_env=git_env,
        )

    @staticmethod
    def _can_normalize_container_path_ownership() -> bool:
        geteuid = getattr(os, "geteuid", None)
        return os.name == "posix" and callable(geteuid) and geteuid() == 0

    @staticmethod
    def _normalize_container_path_owner(path: Path) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        if path.exists():
            DockerCodexManagedSessionController._chown_path(path)

    @staticmethod
    def _normalize_container_path_owners(paths: Sequence[Path]) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        for path in paths:
            if path.exists():
                DockerCodexManagedSessionController._chown_path(path)

    @staticmethod
    def _normalize_container_path_ownership(paths: Sequence[Path]) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        roots = DockerCodexManagedSessionController._deduplicate_ownership_roots(paths)
        for path in roots:
            if not path.exists():
                continue
            DockerCodexManagedSessionController._chown_path(path)
            for root, dirnames, filenames in os.walk(path):
                root_path = Path(root)
                for dirname in dirnames:
                    DockerCodexManagedSessionController._chown_path(root_path / dirname)
                for filename in filenames:
                    DockerCodexManagedSessionController._chown_path(root_path / filename)

    @staticmethod
    def _normalize_container_directory_ownership_no_follow(path: Path) -> None:
        if (
            not DockerCodexManagedSessionController
            ._can_normalize_container_path_ownership()
        ):
            return
        directory_flag = getattr(os, "O_DIRECTORY", None)
        no_follow_flag = getattr(os, "O_NOFOLLOW", None)
        if directory_flag is None or no_follow_flag is None:
            raise RuntimeError(
                "managed session artifact ownership repair requires "
                "O_DIRECTORY and O_NOFOLLOW support"
            )
        try:
            root_fd = os.open(path, os.O_RDONLY | directory_flag | no_follow_flag)
        except OSError as exc:
            raise RuntimeError(
                "repo-local artifacts path could not be opened without following "
                f"symlinks: {path}"
            ) from exc
        try:
            if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                raise RuntimeError(
                    f"repo-local artifacts path must be a directory: {path}"
                )
            os.fchown(
                root_fd,
                _MANAGED_SESSION_CONTAINER_UID,
                _MANAGED_SESSION_CONTAINER_GID,
            )
            for _root, dirnames, filenames, directory_fd in os.fwalk(
                ".",
                topdown=True,
                follow_symlinks=False,
                dir_fd=root_fd,
            ):
                for name in (*dirnames, *filenames):
                    os.chown(
                        name,
                        _MANAGED_SESSION_CONTAINER_UID,
                        _MANAGED_SESSION_CONTAINER_GID,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
        finally:
            os.close(root_fd)

    @staticmethod
    def _deduplicate_ownership_roots(paths: Sequence[Path]) -> list[Path]:
        deduplicated: list[Path] = []
        roots = sorted({Path(item) for item in paths}, key=lambda item: len(item.parts))
        for path in roots:
            if any(path == root or path.is_relative_to(root) for root in deduplicated):
                continue
            deduplicated.append(path)
        return deduplicated

    @staticmethod
    def _chown_path(path: Path) -> None:
        os.chown(
            path,
            _MANAGED_SESSION_CONTAINER_UID,
            _MANAGED_SESSION_CONTAINER_GID,
            follow_symlinks=False,
        )

    async def _invoke_json(
        self,
        *,
        container_id: str,
        action: str,
        payload: Mapping[str, Any],
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        command = [
            self._docker_binary,
            "exec",
            "-i",
        ]
        if extra_env:
            for key, value in extra_env.items():
                command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                container_id,
                "python3",
                "-m",
                _RUNTIME_MODULE,
                "invoke",
                action,
            ]
        )
        env = self._docker_env()
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        returncode, stdout, stderr = await self._command_runner(
            tuple(command),
            input_text=json.dumps(payload),
            env=env,
        )
        session_id = str(payload.get("sessionId") or "").strip() or None
        stdout_text = stdout.strip()
        if returncode != 0:
            if stdout_text:
                try:
                    response_payload = json.loads(stdout_text)
                except json.JSONDecodeError as exc:
                    self._raise_transport_failure(
                        command,
                        action=action,
                        container_id=container_id,
                        session_id=session_id,
                        reason=f"failed with exit code {returncode} and returned invalid JSON",
                        stdout=stdout_text,
                        stderr=stderr,
                        extra_env=extra_env,
                        cause=exc,
                    )
                if isinstance(response_payload, dict):
                    error_payload = response_payload.get("error")
                    if isinstance(error_payload, str) and error_payload.strip():
                        raise RuntimeError(error_payload.strip())
                    if error_payload not in (None, ""):
                        raise RuntimeError(str(error_payload))
                self._raise_transport_failure(
                    command,
                    action=action,
                    container_id=container_id,
                    session_id=session_id,
                    reason=f"failed with exit code {returncode}",
                    stdout=stdout_text,
                    stderr=stderr,
                    extra_env=extra_env,
                )
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason=f"failed with exit code {returncode}",
                stdout=None,
                stderr=stderr,
                extra_env=extra_env,
            )
        if not stdout_text:
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason="returned no JSON output",
                stdout=None,
                stderr=stderr,
                extra_env=extra_env,
            )
        try:
            response_payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason="returned invalid JSON",
                stdout=stdout_text,
                stderr=stderr,
                extra_env=extra_env,
                cause=exc,
            )
        if not isinstance(response_payload, dict):
            self._raise_transport_failure(
                command,
                action=action,
                container_id=container_id,
                session_id=session_id,
                reason=(
                    f"returned a {type(response_payload).__name__} payload instead "
                    "of a JSON object"
                ),
                stdout=stdout_text,
                stderr=stderr,
                extra_env=extra_env,
            )
        return response_payload

    def _load_runtime_state_payload(
        self,
        *,
        session_workspace_path: str,
    ) -> dict[str, Any] | None:
        state_path = Path(session_workspace_path) / _SESSION_STATE_FILENAME
        if not state_path.is_file():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _recover_send_turn_response(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse | None:
        if self._session_store is None:
            return None
        record = self._session_store.load(request.session_id)
        if record is None:
            return None
        try:
            self._matches_locator(record, request)
        except RuntimeError:
            return None
        state_payload = self._load_runtime_state_payload(
            session_workspace_path=record.session_workspace_path,
        )
        if state_payload is None:
            return None
        if str(state_payload.get("sessionId") or "").strip() != request.session_id:
            return None
        if int(state_payload.get("sessionEpoch") or 0) != request.session_epoch:
            return None
        if str(state_payload.get("containerId") or "").strip() != request.container_id:
            return None
        if str(state_payload.get("logicalThreadId") or "").strip() != request.thread_id:
            return None

        active_turn_id = str(state_payload.get("activeTurnId") or "").strip() or None
        if not active_turn_id:
            return None
        turn_id = active_turn_id
        status = str(state_payload.get("lastTurnStatus") or "").strip().lower()
        if not status:
            status = "running" if active_turn_id else ""
        if status not in {"accepted", "running"}:
            return None

        metadata: dict[str, Any] = {}
        assistant_text = str(state_payload.get("lastAssistantText") or "").strip()
        if assistant_text:
            metadata["assistantText"] = assistant_text
        error_text = str(state_payload.get("lastTurnError") or "").strip()
        if error_text:
            metadata["reason"] = error_text

        return CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
                "activeTurnId": active_turn_id,
            },
            turnId=turn_id,
            status=status,
            metadata=metadata,
        )

    @staticmethod
    def _active_session_observations(metadata: Mapping[str, Any]) -> list[Any]:
        """Return typed runtime observations plus the authoritative intervention journal.

        The intervention journal is a runtime-neutral producer contract, distinct from
        terminal response metadata. Entries retain their source identity and authority
        fields and are deduplicated against the general observation stream.
        """
        combined: list[Any] = []
        seen_source_ids: set[str] = set()
        for field in ("observabilityEvents", "interventionJournal"):
            values = metadata.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                item = dict(value)
                item_metadata = item.get("metadata")
                metadata_mapping = (
                    item_metadata if isinstance(item_metadata, Mapping) else {}
                )
                source_id = ""
                if metadata_mapping:
                    source_id = str(
                        metadata_mapping.get("sourceEventId")
                        or metadata_mapping.get("idempotencyKey")
                        or ""
                    ).strip()
                event_state = str(
                    item.get("kind")
                    or item.get("type")
                    or metadata_mapping.get("outcome")
                    or ""
                ).strip()
                dedupe_id = f"{source_id}:{event_state}" if source_id else ""
                if dedupe_id and dedupe_id in seen_source_ids:
                    continue
                if dedupe_id:
                    seen_source_ids.add(dedupe_id)
                combined.append(item)
        return combined

    async def _wait_for_terminal_turn_response(
        self,
        *,
        request: SendCodexManagedSessionTurnRequest,
        initial_response: CodexManagedSessionTurnResponse,
        observation_sink: Callable[
            [list[Any], str, CodexManagedSessionLocator], Awaitable[None]
        ] | None = None,
    ) -> CodexManagedSessionTurnResponse:
        turn_id = initial_response.turn_id
        locator_payload = self._locator_from_session_state(
            initial_response.session_state
        ).model_dump(by_alias=True)
        deadline = time.monotonic() + self._turn_poll_timeout_seconds
        while True:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="session_status",
                payload=locator_payload,
            )
            handle = CodexManagedSessionHandle.model_validate(payload)
            metadata = dict(handle.metadata)
            turn_id = str(metadata.get("lastTurnId") or turn_id).strip() or turn_id
            observations = self._active_session_observations(metadata)
            if observation_sink is not None and observations:
                await observation_sink(
                    observations,
                    turn_id,
                    self._locator_from_session_state(handle.session_state),
                )
            last_turn_status = str(metadata.get("lastTurnStatus") or "").strip().lower()
            assistant_text = str(metadata.get("lastAssistantText") or "").strip()
            reason = str(metadata.get("lastTurnError") or "").strip()

            if handle.status == "busy" and handle.session_state.active_turn_id:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "timed out waiting for terminal managed-session turn status "
                        f"after {self._turn_poll_timeout_seconds} seconds"
                    )
                await asyncio.sleep(self._turn_poll_interval_seconds)
                continue

            if handle.status == "failed" or last_turn_status == "failed":
                metadata = {"reason": reason or "turn execution failed"}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="failed",
                    metadata=metadata,
                )

            if handle.status == "interrupted" or last_turn_status == "interrupted":
                metadata = {"reason": reason or "interrupt requested"}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="interrupted",
                    metadata=metadata,
                )

            if handle.status == "ready" and not handle.session_state.active_turn_id:
                metadata = {"assistantText": assistant_text} if assistant_text else {}
                return CodexManagedSessionTurnResponse(
                    sessionState=handle.session_state,
                    turnId=turn_id,
                    status="completed",
                    metadata=metadata,
                )

            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "timed out waiting for terminal managed-session turn status "
                    f"after {self._turn_poll_timeout_seconds} seconds"
                )
            await asyncio.sleep(self._turn_poll_interval_seconds)

    async def _wait_ready(self, *, container_id: str) -> None:
        command = (
            self._docker_binary,
            "exec",
            container_id,
            "python3",
            "-m",
            _RUNTIME_MODULE,
            "ready",
        )
        last_error: Exception | None = None
        for _attempt in range(self._ready_poll_attempts):
            try:
                stdout, _stderr = await self._run(command)
                payload = json.loads(stdout.strip() or "{}")
            except (RuntimeError, json.JSONDecodeError) as exc:
                last_error = exc
                if self._ready_probe_suggests_container_exited(exc):
                    logs = await self._container_logs_excerpt(container_id)
                    log_detail = f"; logs: {logs}" if logs else ""
                    raise RuntimeError(
                        f"managed session container {container_id} exited before "
                        f"ready: {exc}{log_detail}"
                    ) from exc
            else:
                if payload.get("ready") is True:
                    return
            if self._ready_poll_interval_seconds > 0:
                await asyncio.sleep(self._ready_poll_interval_seconds)
        details = f": {last_error}" if last_error is not None else ""
        raise RuntimeError(
            f"managed session container {container_id} did not become ready{details}"
        )

    @staticmethod
    def _ready_probe_suggests_container_exited(exc: Exception) -> bool:
        detail = str(exc).lower()
        return any(
            marker in detail
            for marker in (
                "container is not running",
                "is not running",
                "not running",
                "exited",
                "is stopped",
                "cannot exec in a stopped",
                "no such container",
                "no such object",
                "not found",
                "container not found",
            )
        )

    async def _container_logs_excerpt(self, container_id: str) -> str:
        command = (
            self._docker_binary,
            "logs",
            "--tail",
            str(_CONTAINER_LOG_EXCERPT_TAIL_LINES),
            container_id,
        )
        returncode, stdout, stderr = await self._command_runner(
            command,
            env=self._docker_env(),
        )
        if returncode != 0:
            return ""
        detail = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        if not detail:
            return ""
        _rendered_command, scrubbed_detail = self._scrub_command_failure(
            command,
            detail,
        )
        return scrubbed_detail[-_CONTAINER_LOG_EXCERPT_MAX_CHARS:]

    async def _persist_handle_transition(
        self,
        *,
        locator: CodexManagedSessionLocator,
        status: str,
        active_turn_id: str | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CodexManagedSessionRecord | None:
        if self._session_store is None:
            return None
        existing = self._session_store.load(locator.session_id)
        if existing is None:
            return None
        next_metadata = dict(existing.metadata)
        if metadata:
            next_metadata = self._merge_capability_metadata(next_metadata, metadata)
        return await self._session_store.update(
            locator.session_id,
            session_epoch=locator.session_epoch,
            container_id=locator.container_id,
            thread_id=locator.thread_id,
            active_turn_id=active_turn_id,
            status=self._record_status_from_handle_status(status),
            metadata=next_metadata,
            updated_at=datetime.now(tz=UTC),
            error_message=None,
        )

    async def _emit_session_event(
        self,
        *,
        record: CodexManagedSessionRecord,
        text: str,
        kind: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self._session_supervisor is None:
            return
        try:
            self._session_supervisor.emit_session_event(
                record=record,
                text=text,
                kind=kind,
                turn_id=turn_id,
                active_turn_id=active_turn_id,
                metadata=dict(metadata or {}),
            )
        except Exception:
            logger.warning(
                "Managed session event publication failed for session %s kind %s",
                record.session_id,
                kind,
                exc_info=True,
            )

    async def _container_exists(self, container_id: str) -> bool:
        returncode, stdout, stderr = await self._command_runner(
            (
                self._docker_binary,
                "inspect",
                "-f",
                "{{.Id}}",
                container_id,
            ),
            env=self._docker_env(),
        )
        if returncode == 0:
            return True
        error_output = f"{stdout}\n{stderr}".lower()
        if "no such object" in error_output or "no such container" in error_output:
            return False
        details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        raise RuntimeError(
            f"failed to inspect managed session container {container_id}: {details}"
        )

    async def _container_uses_current_image(
        self,
        *,
        container_id: str,
        image_ref: str,
    ) -> bool:
        """Return whether a live session uses the image currently behind its ref.

        Session records intentionally preserve the operator-facing image ref,
        which may be mutable (for example ``:latest``). Comparing only that
        string lets a pre-deployment container survive after the local tag has
        moved to a corrected runtime image. Docker's container ``.Image`` and
        image ``.Id`` values are immutable content identities, so they are the
        authority for safe reuse.
        """

        container_result = await self._command_runner(
            (
                self._docker_binary,
                "inspect",
                "-f",
                "{{.Image}}",
                container_id,
            ),
            env=self._docker_env(),
        )
        container_returncode, container_stdout, container_stderr = container_result
        if container_returncode != 0:
            error_output = f"{container_stdout}\n{container_stderr}".lower()
            if "no such object" in error_output or "no such container" in error_output:
                return False
            details = (
                container_stderr.strip()
                or container_stdout.strip()
                or f"exit code {container_returncode}"
            )
            raise RuntimeError(
                "failed to inspect managed session container image: " + details
            )

        image_result = await self._command_runner(
            (
                self._docker_binary,
                "image",
                "inspect",
                "-f",
                "{{.Id}}",
                image_ref,
            ),
            env=self._docker_env(),
        )
        image_returncode, image_stdout, image_stderr = image_result
        if image_returncode != 0:
            error_output = f"{image_stdout}\n{image_stderr}".lower()
            if "no such image" in error_output or "no such object" in error_output:
                # A missing local ref must not authorize reuse. Relaunching lets
                # Docker acquire the configured image through the normal path.
                return False
            details = (
                image_stderr.strip()
                or image_stdout.strip()
                or f"exit code {image_returncode}"
            )
            raise RuntimeError(
                "failed to inspect configured managed session image: " + details
            )

        container_image_id = container_stdout.strip()
        current_image_id = image_stdout.strip()
        return bool(
            container_image_id
            and current_image_id
            and container_image_id == current_image_id
        )

    @staticmethod
    def _build_generic_managed_agent_env(
        request: LaunchCodexManagedSessionRequest,
    ) -> dict[str, str]:
        """Build the generic, project-agnostic managed-agent env vars (MM-861).

        Every managed agent session — including workflow-scoped managed Codex
        sessions launched here — receives the same generic workspace
        coordinates so a managed agent behaves like a developer machine with the
        repository checked out, identical across project types:

        - ``MOONMIND_REPO_DIR``      -> the checked-out repository directory
        - ``MOONMIND_RUN_ROOT``      -> the per-run/session workspace root
        - ``MOONMIND_ARTIFACTS_DIR`` -> the durable artifact spool area
        - ``CI``                     -> ``"1"``

        Values are derived from the resolved session paths (all absolute POSIX
        paths inside the container) so they always point at the real
        directories ``_ensure_workspace_paths`` provisions.
        """
        run_root = str(PurePosixPath(request.artifact_spool_path).parent)
        return {
            "MOONMIND_REPO_DIR": request.workspace_path,
            "MOONMIND_RUN_ROOT": run_root,
            "MOONMIND_ARTIFACTS_DIR": request.artifact_spool_path,
            "CI": "1",
        }

    async def launch_session(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        self._validate_launch_request(request)
        if self._session_store is not None:
            existing_record = self._session_store.load(request.session_id)
            if (
                existing_record is not None
                and self._request_matches_record(request, existing_record)
                and await self._container_exists(existing_record.container_id)
                and await self._container_uses_current_image(
                    container_id=existing_record.container_id,
                    image_ref=request.image_ref,
                )
            ):
                return CodexManagedSessionHandle(
                    runtimeFamily=request.runtime_family,
                    sessionState=existing_record.session_state(),
                    status=self._handle_status_from_record_status(
                        existing_record.status
                    ),
                    imageRef=existing_record.image_ref,
                    controlUrl=existing_record.control_url,
                )
        await self._ensure_workspace_paths(request)
        session_environment = dict(request.environment)
        session_environment.pop("GITHUB_TOKEN", None)
        # MM-861: expose the generic, project-agnostic workspace env vars to the
        # managed session container. These are MoonMind-owned and set
        # authoritatively (last-wins over caller/profile passthrough) so values
        # are identical across project types and cannot be spoofed by the
        # incoming environment.
        session_environment.update(self._build_generic_managed_agent_env(request))
        session_environment["CODEX_HOME"] = request.codex_home_path
        session_environment["CODEX_CONFIG_HOME"] = request.codex_home_path
        session_environment["CODEX_CONFIG_PATH"] = str(
            PurePosixPath(request.codex_home_path) / "config.toml"
        )
        if request.workflow_id:
            session_environment.setdefault(
                "MOONMIND_TASK_WORKFLOW_ID",
                request.workflow_id,
            )
        session_environment.setdefault("MOONMIND_AGENT_RUN_ID", request.agent_run_id)
        runtime_id = canonical_managed_session_runtime_id(request.runtime_family)
        if runtime_id is None:
            raise ValueError(
                f"unsupported managed-session runtime family: {request.runtime_family}"
            )
        session_environment["MOONMIND_RUNTIME_ID"] = runtime_id
        if self._moonmind_url:
            existing_moonmind_url = session_environment.get("MOONMIND_URL")
            if existing_moonmind_url is None or not str(existing_moonmind_url).strip():
                session_environment["MOONMIND_URL"] = self._moonmind_url
        # Repository container work is submitted through MoonMind's authenticated,
        # asynchronous MCP surface.  Keep the logical workspace identity separate
        # from the host path that the trusted container-job worker resolves.
        if session_environment.get("MOONMIND_URL"):
            session_environment["MOONMIND_CONTAINER_JOBS_MCP_URL"] = (
                session_environment["MOONMIND_URL"].rstrip("/") + "/mcp"
            )
            session_environment["MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND"] = (
                "managed_runtime"
            )
            session_environment["MOONMIND_CONTAINER_JOBS_RUNTIME_ID"] = (
                runtime_id
            )
            session_environment["MOONMIND_CONTAINER_JOBS_SESSION_ID"] = (
                request.session_id
            )
        docker_sidecar_enabled = self._session_docker_sidecar_enabled(
            session_environment,
            request.docker_capability,
        )
        docker_capability = self._docker_capability_for_launch(
            request,
            sidecar_enabled=docker_sidecar_enabled,
        )
        docker_activate_at_launch = self._docker_activation_at_launch(
            docker_capability
        )
        if docker_sidecar_enabled:
            session_environment["DOCKER_HOST"] = f"unix://{_SESSION_DOCKER_SOCKET_PATH}"
            session_environment.pop("SYSTEM_DOCKER_HOST", None)
            if docker_capability is not None and docker_capability.activation == "on_demand":
                session_environment["MOONMIND_DOCKER_ACTIVATION_COMMAND"] = "true"
        else:
            session_environment.pop("DOCKER_HOST", None)
            session_environment.pop("SYSTEM_DOCKER_HOST", None)
            session_environment.pop("MOONMIND_DOCKER_ACTIVATION_COMMAND", None)
        docker_pull_diagnostics: dict[str, Any] = {
            "pullAuth": "anonymous",
            "registry": "ghcr.io",
            "dockerConfig": "not_materialized",
            "manifestProbe": {"status": "skipped", "reason": "docker_sidecar_disabled"},
        }
        if docker_sidecar_enabled:
            docker_pull_diagnostics = await self._configure_session_ghcr_pull_auth(
                request,
                session_environment,
            )
            try:
                manifest_probe = await self._preflight_docker_manifest_probe(
                    request=request,
                    pull_auth_diagnostics=docker_pull_diagnostics,
                )
            except Exception:
                self._cleanup_session_docker_config(request.session_workspace_path)
                raise
            docker_pull_diagnostics = {
                **docker_pull_diagnostics,
                "manifestProbe": manifest_probe,
            }
        container_name = (
            self._sidecar_agent_container_name(request.session_id)
            if docker_sidecar_enabled
            else self._container_name(request.session_id)
        )
        await self._remove_container(
            self._container_name(request.session_id),
            ignore_failure=True,
        )
        await self._remove_container(
            self._sidecar_agent_container_name(request.session_id),
            ignore_failure=True,
        )
        if docker_sidecar_enabled:
            await self._cleanup_docker_sidecar_resources(
                request.session_id,
                ignore_failure=True,
            )
            await self._prepare_docker_sidecar_socket_volume(request)
        run_command = [
            self._docker_binary,
            "run",
            "-d",
            "--init",
            "--name",
            container_name,
            "--user",
            _MANAGED_SESSION_CONTAINER_USER,
            "--label",
            "moonmind.kind=managed-session",
            "--label",
            f"moonmind.session_id={request.session_id}",
            "--label",
            f"moonmind.session_epoch={request.session_epoch}",
            "--label",
            f"moonmind.agent_run_id={request.agent_run_id}",
            "--label",
            "moonmind.workload_mode="
            f"{'docker-sidecar' if docker_sidecar_enabled else 'no-docker'}",
            "--mount",
            self._volume_mount(self._workspace_volume_name, self._workspace_root),
        ]
        github_broker_started = False
        container_secret_environment: dict[str, str] = {}
        try:
            container_secret_environment = await self._configure_session_github_auth(
                request,
                session_environment,
            )
            github_broker_started = "GIT_CONFIG_GLOBAL" in session_environment
        except Exception:
            await self._github_auth_brokers.stop(request.session_id)
            raise
        docker_network = self._network_name or _managed_session_docker_network(
            session_environment
        )
        # Managed sessions never join the system Docker proxy network. Explicit
        # legacy sidecars remain isolated on the ordinary session network until
        # their dedicated removal change lands.
        unrestricted_proxy_network = None
        if docker_network:
            run_command.extend(["--network", docker_network])
        if docker_sidecar_enabled:
            socket_volume = self._sidecar_socket_volume_name(request.session_id)
            run_command.extend(
                [
                    "--mount",
                    self._volume_mount(socket_volume, _SESSION_DOCKER_SOCKET_DIR),
                ]
            )
        run_command.extend(
            [
                "-e",
                f"MOONMIND_SESSION_WORKSPACE_PATH={request.workspace_path}",
                "-e",
                f"MOONMIND_SESSION_WORKSPACE_STATE_PATH={request.session_workspace_path}",
                "-e",
                f"MOONMIND_SESSION_ARTIFACT_SPOOL_PATH={request.artifact_spool_path}",
                "-e",
                f"MOONMIND_SESSION_CODEX_HOME_PATH={request.codex_home_path}",
                "-e",
                f"MOONMIND_SESSION_IMAGE_REF={request.image_ref}",
                "-e",
                f"MOONMIND_SESSION_CONTROL_URL=docker-exec://{container_name}",
                "-e",
                "MOONMIND_SESSION_TURN_COMPLETION_TIMEOUT_SECONDS="
                f"{request.turn_completion_timeout_seconds}",
            ]
        )
        auth_volume_path = str(
            request.environment.get("MANAGED_AUTH_VOLUME_PATH") or ""
        ).strip()
        if auth_volume_path:
            run_command.extend(
                [
                    "--mount",
                    self._volume_mount(self._codex_volume_name, auth_volume_path),
                ]
            )
        for key, value in sorted(session_environment.items()):
            run_command.extend(["-e", f"{key}={value}"])
        for key in sorted(container_secret_environment):
            run_command.extend(["-e", key])
        run_command.extend(
            [
                request.image_ref,
                "python3",
                "-m",
                _RUNTIME_MODULE,
                "serve",
            ]
        )
        container_id = ""

        async def _cleanup_failed_launch(container_identifier: str) -> None:
            if container_identifier:
                await self._remove_container(
                    container_identifier,
                    ignore_failure=True,
                )
            if docker_sidecar_enabled:
                await self._cleanup_docker_sidecar_resources(
                    request.session_id,
                    ignore_failure=True,
                )
                self._cleanup_session_docker_config(request.session_workspace_path)
            if github_broker_started:
                await self._github_auth_brokers.stop(request.session_id)

        try:
            if docker_activate_at_launch:
                await self._launch_docker_sidecar(
                    session_id=request.session_id,
                    session_epoch=request.session_epoch,
                    agent_run_id=request.agent_run_id,
                    docker_network=docker_network,
                )
            try:
                stdout, _stderr = await self._run(
                    run_command,
                    extra_env=container_secret_environment or None,
                )
            except RuntimeError as exc:
                if not self._docker_name_conflict(exc, container_name):
                    raise
                await self._remove_container(container_name, ignore_failure=True)
                stdout, _stderr = await self._run(
                    run_command,
                    extra_env=container_secret_environment or None,
                )
            container_id = stdout.strip()
            if not container_id:
                raise RuntimeError("docker run returned a blank container id")
            if unrestricted_proxy_network:
                await self._connect_container_network(
                    container_id=container_id,
                    network_name=unrestricted_proxy_network,
                )
        except asyncio.CancelledError:
            await asyncio.shield(_cleanup_failed_launch(container_id or container_name))
            raise
        except Exception:
            await _cleanup_failed_launch(container_id or container_name)
            raise
        try:
            await self._wait_ready(container_id=container_id)
            capability_request = request.model_copy(
                update={
                    "environment": session_environment,
                    "docker_capability": docker_capability,
                }
            )
            if docker_activate_at_launch:
                docker_capability_metadata = await self._evaluate_docker_capability(
                    container_id=container_id,
                    request=capability_request,
                )
            elif docker_capability is not None and docker_capability.allowed:
                docker_capability_metadata = {
                    "capabilities": {
                        "docker": {
                            "allowed": True,
                            "available": False,
                            "activation": docker_capability.activation,
                            "state": "not_started",
                            "mode": docker_capability.mode,
                            "dockerHost": session_environment["DOCKER_HOST"],
                            "composeAvailable": False,
                            "daemon": {"ready": False, "version": ""},
                        }
                    }
                }
            elif docker_capability is not None:
                docker_capability_metadata = {
                    "capabilities": {
                        "docker": {
                            "allowed": False,
                            "available": False,
                            "activation": "denied",
                            "state": "not_allowed",
                            "mode": docker_capability.mode,
                            "dockerHost": None,
                            "composeAvailable": False,
                            "daemon": {"ready": False, "version": ""},
                        }
                    }
                }
            else:
                docker_capability_metadata = {}
            docker_capability_metadata = self._merge_capability_metadata(
                docker_capability_metadata,
                {
                    "capabilities": {
                        "containerJobs": {
                            "available": bool(session_environment.get("MOONMIND_URL")),
                            "transport": "moonmind-mcp",
                            "backendKind": "docker-engine",
                            "workspace": {
                                "kind": "managed_runtime",
                                "runtimeId": runtime_id,
                                "agentRunId": request.agent_run_id,
                                "relativePath": "repo",
                            },
                            "tools": [
                                "container.submit",
                                "container.status",
                                "container.logs",
                                "container.artifacts",
                                "container.cancel",
                            ],
                        }
                    }
                },
            )
            docker_capability_metadata = self._merge_capability_metadata(
                {
                    "capabilities": {
                        "dockerPull": docker_pull_diagnostics,
                    },
                },
                docker_capability_metadata,
            )
            launch_metadata = self._merge_capability_metadata(
                request.metadata,
                docker_capability_metadata,
            )
            container_request = request.model_copy(
                update={
                    "environment": session_environment,
                    "metadata": launch_metadata,
                }
            )
            container_payload = container_request.model_dump(
                by_alias=True,
                exclude={"workspace_spec"},
            )
            payload = await self._invoke_json(
                container_id=container_id,
                action="launch_session",
                payload=container_payload,
                extra_env={"MOONMIND_SESSION_CONTAINER_ID": container_id},
            )
        except asyncio.CancelledError:
            await asyncio.shield(_cleanup_failed_launch(container_id))
            raise
        except Exception:
            await _cleanup_failed_launch(container_id)
            raise
        handle = self._with_runtime_family(
            CodexManagedSessionHandle.model_validate(payload),
            request,
        )
        if docker_capability_metadata:
            handle = handle.model_copy(
                update={
                    "metadata": self._merge_capability_metadata(
                        handle.metadata,
                        docker_capability_metadata,
                    )
                }
            )
        if self._session_store is not None:
            record_metadata = dict(launch_metadata)
            if docker_sidecar_enabled:
                record_metadata["dockerSidecarEnabled"] = True
                record_metadata["dockerActivation"] = (
                    docker_capability.activation if docker_capability else "on_demand"
                )
                record_metadata["dockerNetwork"] = docker_network
            record_request = request.model_copy(update={"metadata": record_metadata})
            record = self._record_from_launch(request=record_request, handle=handle)
            self._session_store.save(record)
            if self._session_supervisor is not None:
                await self._session_supervisor.start(record)
                if self._observability_bridge is not None:
                    self._observability_bridge.emit_session_available(
                        record=record,
                        resumed=False,
                        metadata=handle.metadata,
                    )
        return handle

    async def session_status(
        self,
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="session_status",
            payload=request.model_dump(by_alias=True),
        )
        handle = self._with_runtime_family(
            CodexManagedSessionHandle.model_validate(payload),
            request,
        )
        record = await self._persist_handle_transition(
            locator=self._locator_from_session_state(handle.session_state),
            status=handle.status,
            active_turn_id=handle.session_state.active_turn_id,
            metadata=handle.metadata,
        )
        if record is not None:
            handle = handle.model_copy(
                update={
                    "metadata": self._merge_capability_metadata(
                        handle.metadata,
                        record.metadata,
                    )
                }
            )
            if self._observability_bridge is not None:
                self._observability_bridge.emit_session_available(
                    record=record,
                    resumed=True,
                    metadata=handle.metadata,
                    active_turn_id=handle.session_state.active_turn_id,
                )
        return handle

    async def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
        *,
        observation_sink: Callable[
            [list[Any], str, CodexManagedSessionLocator], Awaitable[None]
        ] | None = None,
    ) -> CodexManagedSessionTurnResponse:
        try:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="send_turn",
                payload=request.model_dump(
                    by_alias=True, exclude={"bridge_publication"}
                ),
            )
            response = self._with_runtime_family(
                CodexManagedSessionTurnResponse.model_validate(payload),
                request,
            )
        except RuntimeError:
            recovered = self._recover_send_turn_response(request)
            if recovered is None:
                raise
            response = self._with_runtime_family(recovered, request)

        initial_observations = response.metadata.get("observabilityEvents")
        if observation_sink is not None and isinstance(initial_observations, list):
            await observation_sink(
                initial_observations,
                response.turn_id,
                self._locator_from_session_state(response.session_state),
            )

        terminal_response = response
        if response.status in {"accepted", "running"}:
            terminal_response = await self._wait_for_terminal_turn_response(
                request=request,
                initial_response=response,
                observation_sink=observation_sink,
            )
            terminal_response = self._with_runtime_family(terminal_response, request)

        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                record_metadata = dict(record.metadata)
                assistant_text = terminal_response.metadata.get("assistantText")
                if isinstance(assistant_text, str) and assistant_text.strip():
                    record_metadata.update(_last_assistant_text_metadata(assistant_text))
                    record_metadata.pop(_EMPTY_ASSISTANT_TURN_METADATA_KEY, None)
                empty_assistant_turn = _is_empty_assistant_turn_response(
                    terminal_response
                )
                if empty_assistant_turn:
                    record_metadata[_EMPTY_ASSISTANT_TURN_METADATA_KEY] = (
                        _empty_assistant_turn_metadata(
                            record_metadata,
                            terminal_response,
                        )
                    )
                updated_record = await self._session_store.update(
                    request.session_id,
                    session_epoch=terminal_response.session_state.session_epoch,
                    container_id=terminal_response.session_state.container_id,
                    thread_id=terminal_response.session_state.thread_id,
                    active_turn_id=terminal_response.session_state.active_turn_id,
                    status=self._record_status_from_turn_status(terminal_response.status),
                    updated_at=datetime.now(tz=UTC),
                    error_message=self._turn_error_message(terminal_response),
                    metadata=record_metadata,
                )
                if self._session_supervisor is not None:
                    if self._observability_bridge is not None:
                        self._observability_bridge.emit_user_message_submitted(
                            record=updated_record,
                            turn_id=response.turn_id,
                            instructions=request.instructions,
                            reason=request.reason,
                        )
                        self._observability_bridge.emit_turn_started(
                            record=updated_record,
                            turn_id=response.turn_id,
                            reason=request.reason,
                        )
                        self._observability_bridge.emit_native_observations(
                            record=updated_record,
                            observations=terminal_response.metadata.get(
                                "observabilityEvents"
                            ),
                            default_turn_id=response.turn_id,
                        )
                    if terminal_response.status == "completed":
                        if self._observability_bridge is not None:
                            self._observability_bridge.emit_assistant_output(
                                record=updated_record,
                                turn_id=terminal_response.turn_id,
                                assistant_text=terminal_response.metadata.get(
                                    "assistantText"
                                ),
                                reason=request.reason,
                            )
                            self._observability_bridge.emit_turn_completed(
                                record=updated_record,
                                turn_id=terminal_response.turn_id,
                                assistant_text=terminal_response.metadata.get(
                                    "assistantText"
                                ),
                                reason=request.reason,
                            )
                    elif terminal_response.status == "failed":
                        if self._observability_bridge is not None:
                            self._observability_bridge.emit_turn_failed(
                                record=updated_record,
                                turn_id=terminal_response.turn_id,
                                response_metadata=terminal_response.metadata,
                                reason=request.reason,
                            )
                    if empty_assistant_turn:
                        await self._emit_session_event(
                            record=updated_record,
                            kind="empty_assistant_turn_detected",
                            text=(
                                "Codex app-server completed a turn without "
                                "assistant output; session clear is recommended."
                            ),
                            turn_id=terminal_response.turn_id,
                            active_turn_id=(
                                terminal_response.session_state.active_turn_id
                            ),
                            metadata={
                                "action": "send_turn",
                                "failureCause": _EMPTY_ASSISTANT_FAILURE_CAUSE,
                                "retryRecommendedAction": "clear_session",
                                "reason": terminal_response.metadata.get("reason"),
                                "consecutiveCount": record_metadata[
                                    _EMPTY_ASSISTANT_TURN_METADATA_KEY
                                ]["consecutiveCount"],
                            },
                        )
        return terminal_response

    async def steer_turn(
        self,
        request: SteerCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="steer_turn",
            payload=request.model_dump(by_alias=True),
        )
        response = self._with_runtime_family(
            CodexManagedSessionTurnResponse.model_validate(payload),
            request,
        )
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                updated_record = await self._session_store.update(
                    request.session_id,
                    session_epoch=response.session_state.session_epoch,
                    container_id=response.session_state.container_id,
                thread_id=response.session_state.thread_id,
                active_turn_id=response.session_state.active_turn_id,
                status=self._record_status_from_turn_status(response.status),
                updated_at=datetime.now(tz=UTC),
                error_message=self._turn_error_message(response),
                )
                if self._session_supervisor is not None:
                    metadata = dict(request.metadata or {})
                    metadata["action"] = "steer_turn"
                    await self._emit_session_event(
                        record=updated_record,
                        kind="system_annotation",
                        text=f"Turn steered: {request.turn_id}.",
                        turn_id=request.turn_id,
                        active_turn_id=response.session_state.active_turn_id
                        or request.turn_id,
                        metadata=metadata,
                    )
        return response

    async def interrupt_turn(
        self,
        request: InterruptCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                self._matches_locator(record, request)
                if record.active_turn_id is None and record.status == "ready":
                    return CodexManagedSessionTurnResponse(
                        runtimeFamily=request.runtime_family,
                        sessionState=record.session_state(),
                        turnId=request.turn_id,
                        status="interrupted",
                        metadata={"reason": request.reason or "already interrupted"},
                    )
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="interrupt_turn",
            payload=request.model_dump(by_alias=True),
        )
        response = self._with_runtime_family(
            CodexManagedSessionTurnResponse.model_validate(payload),
            request,
        )
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None:
                updated_record = await self._session_store.update(
                    request.session_id,
                    session_epoch=response.session_state.session_epoch,
                    container_id=response.session_state.container_id,
                thread_id=response.session_state.thread_id,
                active_turn_id=response.session_state.active_turn_id,
                status=self._record_status_from_turn_status(response.status),
                updated_at=datetime.now(tz=UTC),
                error_message=self._turn_error_message(response),
                )
                if self._session_supervisor is not None and response.status == "interrupted":
                    await self._emit_session_event(
                        record=updated_record,
                        kind="turn_interrupted",
                        text=f"Turn interrupted: {response.turn_id}.",
                        turn_id=response.turn_id,
                        metadata={
                            "action": "interrupt_turn",
                            "reason": response.metadata.get("reason"),
                        },
                    )
        return response

    async def clear_session(
        self,
        request: CodexManagedSessionClearRequest,
    ) -> CodexManagedSessionHandle:
        session_store = self._session_store
        previous_record = None
        if session_store is not None:
            previous_record = session_store.load(request.session_id)
            if previous_record is not None:
                last_clear = previous_record.metadata.get(_CLEAR_REQUEST_METADATA_KEY)
                if (
                    request.request_id
                    and isinstance(last_clear, Mapping)
                    and last_clear.get("requestId") == request.request_id
                    and last_clear.get("status") == "completed"
                    and (
                        previous_record.session_epoch
                        == last_clear.get("newSessionEpoch")
                    )
                    and previous_record.thread_id == last_clear.get("newThreadId")
                    and previous_record.latest_reset_boundary_ref
                ):
                    return CodexManagedSessionHandle(
                        runtimeFamily=request.runtime_family,
                        sessionState=previous_record.session_state(),
                        status=self._handle_status_from_record_status(
                            previous_record.status
                        ),
                        imageRef=previous_record.image_ref,
                        controlUrl=previous_record.control_url,
                        metadata={
                            "idempotentReplay": True,
                            "requestId": request.request_id,
                            "latestResetBoundaryRef": (
                                previous_record.latest_reset_boundary_ref
                            ),
                        },
                    )
                if (
                    previous_record.session_epoch == request.session_epoch + 1
                    and previous_record.container_id == request.container_id
                    and previous_record.thread_id == request.new_thread_id
                    and previous_record.latest_reset_boundary_ref
                ):
                    return CodexManagedSessionHandle(
                        runtimeFamily=request.runtime_family,
                        sessionState=previous_record.session_state(),
                        status=self._handle_status_from_record_status(
                            previous_record.status
                        ),
                        imageRef=previous_record.image_ref,
                        controlUrl=previous_record.control_url,
                    )
                self._matches_locator(previous_record, request)
        payload = await self._invoke_json(
            container_id=request.container_id,
            action="clear_session",
            payload=request.model_dump(by_alias=True),
        )
        handle = self._with_runtime_family(
            CodexManagedSessionHandle.model_validate(payload),
            request,
        )
        if previous_record is not None:
            assert session_store is not None
            record_metadata = dict(previous_record.metadata)
            if request.request_id:
                record_metadata[_CLEAR_REQUEST_METADATA_KEY] = {
                    "requestId": request.request_id,
                    "status": "accepted",
                    "previousSessionEpoch": previous_record.session_epoch,
                    "newSessionEpoch": handle.session_state.session_epoch,
                    "previousThreadId": previous_record.thread_id,
                    "newThreadId": handle.session_state.thread_id,
                    "reason": request.reason,
                }
            updated_record = await session_store.update(
                request.session_id,
                session_epoch=handle.session_state.session_epoch,
                container_id=handle.session_state.container_id,
                thread_id=handle.session_state.thread_id,
                active_turn_id=handle.session_state.active_turn_id,
                image_ref=handle.image_ref or previous_record.image_ref,
                control_url=handle.control_url or previous_record.control_url,
                status=self._record_status_from_handle_status(handle.status),
                updated_at=datetime.now(tz=UTC),
                error_message=None,
                metadata=record_metadata,
            )
            if self._session_supervisor is not None:
                updated_record = await self._session_supervisor.publish_reset_artifacts(
                    previous_record=previous_record,
                    record=updated_record,
                    action="clear_session",
                    reason=request.reason,
                )
            if request.request_id:
                completed_metadata = dict(updated_record.metadata)
                raw_clear_metadata = completed_metadata.get(_CLEAR_REQUEST_METADATA_KEY)
                clear_metadata = (
                    dict(raw_clear_metadata)
                    if isinstance(raw_clear_metadata, Mapping)
                    else {}
                )
                clear_metadata.update(
                    {
                        "requestId": request.request_id,
                        "status": "completed",
                        "previousSessionEpoch": previous_record.session_epoch,
                        "newSessionEpoch": updated_record.session_epoch,
                        "previousThreadId": previous_record.thread_id,
                        "newThreadId": updated_record.thread_id,
                        "latestControlEventRef": (
                            updated_record.latest_control_event_ref
                        ),
                        "latestResetBoundaryRef": (
                            updated_record.latest_reset_boundary_ref
                        ),
                    }
                )
                completed_metadata[_CLEAR_REQUEST_METADATA_KEY] = clear_metadata
                await session_store.update(
                    request.session_id,
                    metadata=completed_metadata,
                    updated_at=datetime.now(tz=UTC),
                )
        else:
            await self._persist_handle_transition(
                locator=self._locator_from_session_state(handle.session_state),
                status=handle.status,
                active_turn_id=handle.session_state.active_turn_id,
            )
        return handle

    async def terminate_session(
        self,
        request: TerminateCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        record = None
        if self._session_store is not None:
            record = self._session_store.load(request.session_id)
            if record is not None and record.status == "terminated":
                self._matches_locator(record, request)
                await self._remove_container(record.container_id, ignore_failure=True)
                if record.metadata.get("dockerSidecarEnabled") is True:
                    await self._cleanup_docker_sidecar_resources(
                        request.session_id,
                        ignore_failure=True,
                    )
                    self._cleanup_session_docker_config(record.session_workspace_path)
                await self._github_auth_brokers.stop(request.session_id)
                self._cleanup_skill_projections_for_session(record)
                return CodexManagedSessionHandle(
                    runtimeFamily=request.runtime_family,
                    sessionState=record.session_state(),
                    status="terminated",
                    imageRef=record.image_ref,
                    controlUrl=record.control_url,
                )
        await self._remove_container(request.container_id, ignore_failure=True)
        if record is not None and record.metadata.get("dockerSidecarEnabled") is True:
            await self._cleanup_docker_sidecar_resources(
                request.session_id,
                ignore_failure=True,
            )
            self._cleanup_session_docker_config(record.session_workspace_path)
        elif record is None:
            await self._cleanup_docker_sidecar_resources(
                request.session_id,
                ignore_failure=True,
                remove_volumes_if_container_missing=False,
            )
        await self._github_auth_brokers.stop(request.session_id)
        self._cleanup_skill_projections_for_session(record)
        handle = CodexManagedSessionHandle(
            runtimeFamily=request.runtime_family,
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
                "activeTurnId": None,
            },
            status="terminated",
        )
        if self._session_store is not None:
            if self._session_supervisor is not None:
                record = self._session_store.load(request.session_id)
                if record is not None:
                    await self._session_store.update(
                        request.session_id,
                        active_turn_id=None,
                    )
                    refreshed = self._session_store.load(request.session_id) or record
                    await self._emit_session_event(
                        record=refreshed,
                        kind="session_terminated",
                        text=f"Session terminated: {request.session_id}.",
                        metadata={
                            "action": "terminate_session",
                            "reason": request.reason,
                        },
                    )
                await self._session_supervisor.finalize(
                    request.session_id,
                    status="terminated",
                )
            elif self._session_store.load(request.session_id) is not None:
                await self._session_store.update(
                    request.session_id,
                    status="terminated",
                    active_turn_id=None,
                    updated_at=datetime.now(tz=UTC),
                )
        return handle

    @staticmethod
    def _cleanup_skill_projections_for_session(
        record: CodexManagedSessionRecord | None,
    ) -> None:
        if record is None or not record.workspace_path:
            return
        workspace = Path(record.workspace_path)
        active_root = workspace.parent / "runtime" / "skills_active"
        try:
            cleanup_moonmind_skill_projections(
                run_root=workspace,
                skills_active_path=active_root,
                owned_roots=(active_root,),
            )
        except OSError:
            logger.debug(
                "Best-effort skill projection cleanup failed for session %s",
                record.session_id,
                exc_info=True,
            )

    async def fetch_session_summary(
        self,
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        record = self._require_record(request)
        if record is None:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="fetch_session_summary",
                payload=request.model_dump(by_alias=True),
            )
            return self._with_runtime_family(
                CodexManagedSessionSummary.model_validate(payload),
                request,
            )
        return CodexManagedSessionSummary(
            runtimeFamily=request.runtime_family,
            sessionState=record.session_state(),
            latestSummaryRef=record.latest_summary_ref,
            latestCheckpointRef=record.latest_checkpoint_ref,
            latestControlEventRef=record.latest_control_event_ref,
            latestResetBoundaryRef=record.latest_reset_boundary_ref,
            metadata={
                **dict(record.metadata),
                "status": record.status,
                "stdoutArtifactRef": record.stdout_artifact_ref,
                "stderrArtifactRef": record.stderr_artifact_ref,
                "diagnosticsRef": record.diagnostics_ref,
                "observabilityEventsRef": record.observability_events_ref,
                "errorMessage": record.error_message,
            },
        )

    async def publish_session_artifacts(
        self,
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        record = self._require_record(request)
        if record is None:
            payload = await self._invoke_json(
                container_id=request.container_id,
                action="publish_session_artifacts",
                payload=request.model_dump(by_alias=True),
            )
            return self._with_runtime_family(
                CodexManagedSessionArtifactsPublication.model_validate(payload),
                request,
            )
        if (
            self._session_supervisor is not None
            and not record.published_artifact_refs()
        ):
            record = await self._session_supervisor.publish_snapshot(request.session_id)
        return CodexManagedSessionArtifactsPublication(
            runtimeFamily=request.runtime_family,
            sessionState=record.session_state(),
            publishedArtifactRefs=record.published_artifact_refs(),
            latestSummaryRef=record.latest_summary_ref,
            latestCheckpointRef=record.latest_checkpoint_ref,
            latestControlEventRef=record.latest_control_event_ref,
            latestResetBoundaryRef=record.latest_reset_boundary_ref,
            metadata={
                **dict(record.metadata),
                **dict(request.metadata),
                "status": record.status,
                "stdoutArtifactRef": record.stdout_artifact_ref,
                "stderrArtifactRef": record.stderr_artifact_ref,
                "diagnosticsRef": record.diagnostics_ref,
                "observabilityEventsRef": record.observability_events_ref,
            },
        )

    async def _terminal_owner_workflow_status(
        self,
        record: CodexManagedSessionRecord,
    ) -> str | None:
        resolver = self._owner_workflow_status_resolver
        if resolver is None:
            return None
        try:
            status = await resolver(record.agent_run_id)
            if not _owner_workflow_status_is_terminal(status):
                return None
            return _normalize_owner_workflow_status(status)
        except Exception:
            logger.warning(
                "Managed session owner workflow status lookup failed for %s",
                record.session_id,
                exc_info=True,
            )
            return None

    async def _mark_session_terminated_by_reconcile(
        self,
        record: CodexManagedSessionRecord,
        *,
        reason: str,
        metadata: Mapping[str, Any],
    ) -> CodexManagedSessionRecord:
        if self._session_store is None:
            return record
        updated_metadata = {
            **dict(record.metadata or {}),
            **dict(metadata),
            "terminationSource": "managed_session_reconcile",
            "terminatedAt": datetime.now(tz=UTC).isoformat(),
        }
        updated = await self._session_store.update(
            record.session_id,
            status="terminated",
            active_turn_id=None,
            error_message=reason,
            metadata=updated_metadata,
            updated_at=datetime.now(tz=UTC),
        )
        await self._github_auth_brokers.stop(record.session_id)
        self._cleanup_skill_projections_for_session(updated)
        return updated

    async def reconcile(self) -> list[CodexManagedSessionRecord]:
        if self._session_store is None:
            return []
        reconciled: list[CodexManagedSessionRecord] = []
        for record in self._session_store.list_active():
            try:
                terminal_owner_status = await self._terminal_owner_workflow_status(
                    record
                )
                if terminal_owner_status is not None:
                    updated = await self._mark_session_terminated_by_reconcile(
                        record,
                        reason=(
                            "managed session owner workflow is terminal during "
                            f"reconcile: {terminal_owner_status}"
                        ),
                        metadata={
                            "ownerWorkflowId": record.agent_run_id,
                            "ownerWorkflowStatus": terminal_owner_status,
                        },
                    )
                    reconciled.append(updated)
                    logger.warning(
                        "Marked managed session %s terminated because owner "
                        "workflow %s is terminal: %s",
                        record.session_id,
                        record.agent_run_id,
                        terminal_owner_status,
                    )
                    continue
                container_exists = await self._container_exists(record.container_id)
                if not container_exists:
                    updated = await self._session_store.update(
                        record.session_id,
                        status="degraded",
                        error_message=(
                            "managed session container is missing during reconcile"
                        ),
                        updated_at=datetime.now(tz=UTC),
                    )
                    reconciled.append(updated)
                    continue
                if self._session_supervisor is not None:
                    await self._session_supervisor.start(record)
                reconciled.append(record)
            except Exception as exc:
                logger.warning(
                    "Managed session reconcile degraded session %s after reattach failure",
                    record.session_id,
                    exc_info=True,
                )
                updated = await self._session_store.update(
                    record.session_id,
                    status="degraded",
                    error_message=str(exc),
                    updated_at=datetime.now(tz=UTC),
                )
                reconciled.append(updated)
        return reconciled

    @staticmethod
    def _reap_enabled() -> bool:
        raw = os.environ.get("MOONMIND_MANAGED_SESSION_REAP_ENABLED")
        if raw is None:
            return True
        return raw.strip().lower() not in _FALSEY_ENV_VALUES

    @staticmethod
    def _reap_grace_seconds() -> float:
        raw = os.environ.get("MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS")
        if not raw:
            return _DEFAULT_SESSION_REAP_GRACE_SECONDS
        try:
            return max(0.0, float(raw))
        except ValueError:
            return _DEFAULT_SESSION_REAP_GRACE_SECONDS

    @staticmethod
    def _reap_max_age_seconds() -> float | None:
        raw = os.environ.get("MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS")
        if raw is None:
            return float(_DEFAULT_SESSION_REAP_MAX_AGE_SECONDS)
        normalized = raw.strip().lower()
        if normalized in _FALSEY_ENV_VALUES:
            return None
        try:
            value = float(normalized)
        except ValueError:
            return float(_DEFAULT_SESSION_REAP_MAX_AGE_SECONDS)
        if value <= 0:
            return None
        return value

    @staticmethod
    def _newest_container_created_at(
        containers: Sequence[_ManagedSessionContainer],
    ) -> datetime | None:
        return max(
            (
                container.created_at
                for container in containers
                if container.created_at is not None
            ),
            default=None,
        )

    def _stale_active_session_ids(
        self,
        *,
        active_records: Mapping[str, CodexManagedSessionRecord],
        by_session: Mapping[str, Sequence[_ManagedSessionContainer]],
        max_age_seconds: float | None,
        now: datetime,
    ) -> set[str]:
        if max_age_seconds is None:
            return set()
        stale: set[str] = set()
        for session_id, record in active_records.items():
            if record.status != "ready" or record.active_turn_id:
                continue
            newest_container_created_at = self._newest_container_created_at(
                by_session.get(session_id, ())
            )
            age_anchor = (
                newest_container_created_at or record.updated_at or record.started_at
            )
            age_anchor = _coerce_aware_datetime(age_anchor)
            if age_anchor is None:
                continue
            if (now - age_anchor).total_seconds() < max_age_seconds:
                continue
            stale.add(session_id)
        return stale

    async def _list_managed_session_containers(
        self,
    ) -> list[_ManagedSessionContainer]:
        returncode, stdout, stderr = await self._command_runner(
            (
                self._docker_binary,
                "ps",
                "-aq",
                "--filter",
                f"label={_MANAGED_SESSION_LABEL_KEY}",
            ),
            env=self._docker_env(),
        )
        if returncode != 0:
            details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
            raise RuntimeError(
                f"failed to list managed session containers: {details}"
            )
        container_ids = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not container_ids:
            return []
        template = (
            '{{printf "%s|%s|%s|%s" .Id '
            f'(index .Config.Labels "{_MANAGED_SESSION_LABEL_KEY}") '
            '(index .Config.Labels "moonmind.kind") '
            ".Created}}"
        )
        # Tolerate partial failures: a container can vanish between listing and
        # inspect. Parse whatever well-formed rows came back and let the next
        # sweep retry the rest, rather than failing the whole reconcile.
        _inspect_rc, inspect_out, _inspect_err = await self._command_runner(
            (
                self._docker_binary,
                "inspect",
                "--format",
                template,
                *container_ids,
            ),
            env=self._docker_env(),
        )
        containers: list[_ManagedSessionContainer] = []
        for line in inspect_out.splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            container_id, session_id, kind, created_raw = (
                part.strip() for part in parts
            )
            if not container_id or not session_id:
                continue
            containers.append(
                _ManagedSessionContainer(
                    container_id=container_id,
                    session_id=session_id,
                    kind=kind,
                    created_at=_parse_docker_timestamp(created_raw),
                )
            )
        return containers

    @staticmethod
    def _is_managed_session_sidecar_volume_name(volume_name: str) -> bool:
        return bool(_MANAGED_SESSION_SIDECAR_VOLUME_NAME.match(volume_name))

    @staticmethod
    def _sidecar_volume_role_from_name(volume_name: str) -> str:
        if volume_name.endswith("-docker-graph"):
            return "docker-graph"
        if volume_name.endswith("-docker-socket"):
            return "docker-socket"
        return ""

    @staticmethod
    def _docker_template_empty(value: str) -> str:
        normalized = str(value or "").strip()
        if normalized in {"<no value>", "<nil>", "nil", "None"}:
            return ""
        return normalized

    async def _list_managed_session_sidecar_volumes(
        self,
    ) -> list[_ManagedSessionSidecarVolume]:
        returncode, stdout, stderr = await self._command_runner(
            (
                self._docker_binary,
                "volume",
                "ls",
                "--format",
                "{{.Name}}",
            ),
            env=self._docker_env(),
        )
        if returncode != 0:
            details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
            raise RuntimeError(
                f"failed to list managed session sidecar volumes: {details}"
            )
        volume_names = [
            line.strip()
            for line in stdout.splitlines()
            if self._is_managed_session_sidecar_volume_name(line.strip())
        ]
        if not volume_names:
            return []
        inspect_rc, inspect_out, inspect_err = await self._command_runner(
            (
                self._docker_binary,
                "volume",
                "inspect",
                *volume_names,
            ),
            env=self._docker_env(),
        )
        if inspect_rc != 0:
            details = (
                inspect_err.strip()
                or inspect_out.strip()
                or f"exit code {inspect_rc}"
            )
            raise RuntimeError(
                f"failed to inspect managed session sidecar volumes: {details}"
            )

        inspected: dict[str, _ManagedSessionSidecarVolume] = {}
        try:
            volume_inspect_items = json.loads(inspect_out) if inspect_out.strip() else []
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "failed to parse managed session sidecar volume inspect output"
            ) from exc
        if not isinstance(volume_inspect_items, list):
            raise RuntimeError(
                "failed to inspect managed session sidecar volumes: expected JSON list"
            )

        for item in volume_inspect_items:
            if not isinstance(item, dict):
                continue
            name = item.get("Name")
            if not name or name not in volume_names:
                continue
            labels = item.get("Labels") or {}
            if not isinstance(labels, dict):
                labels = {}
            inspected[name] = _ManagedSessionSidecarVolume(
                name=name,
                session_id=str(labels.get("moonmind.session_id") or ""),
                role=str(
                    labels.get("moonmind.volume_role")
                    or self._sidecar_volume_role_from_name(name)
                ),
                kind=str(labels.get("moonmind.kind") or ""),
                created_at=_parse_docker_timestamp(str(item.get("CreatedAt") or "")),
            )

        volumes: list[_ManagedSessionSidecarVolume] = []
        for name in volume_names:
            volumes.append(
                inspected.get(
                    name,
                    _ManagedSessionSidecarVolume(
                        name=name,
                        session_id="",
                        role=self._sidecar_volume_role_from_name(name),
                        kind="",
                        created_at=None,
                    ),
                )
            )
        return volumes

    async def _list_active_docker_volume_mounts(self) -> set[str]:
        returncode, stdout, stderr = await self._command_runner(
            (self._docker_binary, "ps", "-q"),
            env=self._docker_env(),
        )
        if returncode != 0:
            details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
            raise RuntimeError(f"failed to list active docker containers: {details}")
        container_ids = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not container_ids:
            return set()
        template = (
            '{{range .Mounts}}{{if eq .Type "volume"}}{{println .Name}}{{end}}{{end}}'
        )
        inspect_rc, inspect_out, inspect_err = await self._command_runner(
            (
                self._docker_binary,
                "inspect",
                "--format",
                template,
                *container_ids,
            ),
            env=self._docker_env(),
        )
        if inspect_rc != 0:
            details = (
                inspect_err.strip()
                or inspect_out.strip()
                or f"exit code {inspect_rc}"
            )
            raise RuntimeError(f"failed to inspect active docker containers: {details}")
        return {line.strip() for line in inspect_out.splitlines() if line.strip()}

    async def collect_managed_runtime_cleanup_docker_references(self):
        """Return live Docker references that must protect retained state."""
        from moonmind.workflows.temporal.runtime.cleanup import DockerReferenceState

        returncode, stdout, stderr = await self._command_runner(
            (self._docker_binary, "ps", "-q"),
            env=self._docker_env(),
        )
        if returncode != 0:
            details = stderr.strip() or stdout.strip() or f"exit code {returncode}"
            return DockerReferenceState(
                failed=True,
                reason=f"docker reference scan failed: {details}",
            )
        container_ids = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not container_ids:
            return DockerReferenceState()
        inspect_rc, inspect_out, inspect_err = await self._command_runner(
            (self._docker_binary, "inspect", *container_ids),
            env=self._docker_env(),
        )
        if inspect_rc != 0:
            details = (
                inspect_err.strip()
                or inspect_out.strip()
                or f"exit code {inspect_rc}"
            )
            return DockerReferenceState(
                failed=True,
                reason=f"docker reference scan failed: {details}",
            )
        try:
            items = json.loads(inspect_out) if inspect_out.strip() else []
        except json.JSONDecodeError as exc:
            return DockerReferenceState(
                failed=True,
                reason=f"docker reference scan failed: {exc}",
            )
        active_container_refs: set[str] = set()
        active_mount_paths: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            for value in (item.get("Id"), item.get("Name")):
                if value:
                    active_container_refs.add(str(value).strip().lstrip("/"))
            config = item.get("Config") or {}
            labels = config.get("Labels") if isinstance(config, dict) else {}
            if isinstance(labels, dict):
                for key in (
                    "moonmind.session_id",
                    "moonmind.agent_run_id",
                    "moonmind.run_id",
                ):
                    value = labels.get(key)
                    if value:
                        active_container_refs.add(str(value))
            mounts = item.get("Mounts") or []
            if isinstance(mounts, list):
                for mount in mounts:
                    if not isinstance(mount, dict):
                        continue
                    for key in ("Source", "Destination", "Name"):
                        value = mount.get(key)
                        if value:
                            active_mount_paths.add(str(value))
        return DockerReferenceState(
            active_container_refs=frozenset(active_container_refs),
            active_mount_paths=frozenset(active_mount_paths),
        )

    def _active_sidecar_volume_names(self, active_session_ids: set[str]) -> set[str]:
        names: set[str] = set()
        for session_id in active_session_ids:
            names.add(self._sidecar_graph_volume_name(session_id))
            names.add(self._sidecar_socket_volume_name(session_id))
        return names

    async def _reap_orphan_sidecar_volumes(
        self,
        *,
        volumes: Sequence[_ManagedSessionSidecarVolume],
        active_session_ids: set[str],
        grace_seconds: float,
        now: datetime,
    ) -> tuple[int, int, int, int]:
        if not volumes:
            return (0, 0, 0, 0)
        active_mounts = await self._list_active_docker_volume_mounts()
        active_volume_names = self._active_sidecar_volume_names(active_session_ids)

        reaped_volumes = 0
        skipped_active = 0
        skipped_recent = 0
        for volume in volumes:
            if (
                volume.name in active_mounts
                or volume.name in active_volume_names
                or (
                    volume.session_id
                    and volume.session_id in active_session_ids
                )
            ):
                skipped_active += 1
                continue
            if volume.created_at is None:
                skipped_recent += 1
                continue
            if (now - volume.created_at).total_seconds() < grace_seconds:
                skipped_recent += 1
                continue
            try:
                removed = await self._remove_volume(volume.name, ignore_failure=True)
            except Exception:
                logger.warning(
                    "Failed to reap orphaned managed session sidecar volume %s",
                    volume.name,
                    exc_info=True,
                )
                continue
            if not removed:
                continue
            reaped_volumes += 1
            logger.info(
                "Reaped orphaned managed session sidecar volume %s",
                volume.name,
            )
        return (len(volumes), reaped_volumes, skipped_active, skipped_recent)

    async def reap_orphan_session_containers(self) -> ManagedSessionReapResult:
        """Remove managed-session containers that no longer back a live session.

        A managed session normally tears its containers down through
        ``terminate_session``. When the owning workflow is terminated or crashes
        the session child is abandoned (``ParentClosePolicy.ABANDON``), so the
        agent container and its docker-sidecar (plus volumes) can be left
        running indefinitely. This sweep removes containers whose session is not
        active in the durable store, guarded by a grace window so a freshly
        launched session is never reaped before its record is durable.
        """

        if not self._reap_enabled():
            return ManagedSessionReapResult(disabled=True)
        if self._session_store is None:
            # Without the durable store we cannot tell which sessions are live,
            # so refuse to remove anything rather than guess.
            return ManagedSessionReapResult(disabled=True)

        grace_seconds = self._reap_grace_seconds()
        max_age_seconds = self._reap_max_age_seconds()
        now = datetime.now(tz=UTC)

        containers = await self._list_managed_session_containers()
        by_session: dict[str, list[_ManagedSessionContainer]] = {}
        for container in containers:
            by_session.setdefault(container.session_id, []).append(container)
        volumes = await self._list_managed_session_sidecar_volumes()
        resource_session_ids = set(by_session)
        resource_session_ids.update(
            volume.session_id for volume in volumes if volume.session_id
        )
        all_records = {
            record.session_id: record for record in self._session_store.iter_all()
        }
        active_records = {
            session_id: record
            for session_id, record in all_records.items()
            if record.status not in TERMINAL_MANAGED_SESSION_STATUSES
        }
        stale_active_session_ids = self._stale_active_session_ids(
            active_records=active_records,
            by_session=by_session,
            max_age_seconds=max_age_seconds,
            now=now,
        )
        active_session_ids = set(active_records) - stale_active_session_ids
        # A terminal session-store status is not authoritative proof that the
        # owning Temporal workflow has finished. Provider failures and retry
        # cooldowns can make the record terminal while the workflow still owns
        # and polls the container. Protect those containers unless Temporal
        # positively confirms terminal ownership; lookup failures fail closed.
        for session_id in resource_session_ids:
            record = all_records.get(session_id)
            if record is None:
                continue
            if record.status not in TERMINAL_MANAGED_SESSION_STATUSES:
                continue
            terminal_owner_status = await self._terminal_owner_workflow_status(record)
            if terminal_owner_status is None:
                active_session_ids.add(session_id)

        skipped_active = 0
        skipped_recent = 0
        forced_stale = 0
        reaped_containers = 0
        reaped_session_ids: list[str] = []

        for session_id, session_containers in by_session.items():
            if session_id in active_session_ids:
                skipped_active += len(session_containers)
                continue
            newest = self._newest_container_created_at(session_containers)
            force_stale = session_id in stale_active_session_ids
            if (
                not force_stale
                and newest is not None
                and (now - newest).total_seconds() < grace_seconds
            ):
                skipped_recent += len(session_containers)
                continue
            if force_stale:
                record = active_records.get(session_id)
                if record is not None:
                    try:
                        await self._mark_session_terminated_by_reconcile(
                            record,
                            reason=(
                                "managed session exceeded reap max age without an "
                                "active turn"
                            ),
                            metadata={
                                "maxAgeSeconds": max_age_seconds,
                                "reapReason": "stale_active_session_max_age",
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to terminate stale active session %s during reap",
                            session_id,
                        )
                        continue
                forced_stale += 1
                logger.warning(
                    "Reaping stale active managed session %s after max age %s seconds",
                    session_id,
                    max_age_seconds,
                )
            removed_any = False
            for container in session_containers:
                if await self._remove_container(
                    container.container_id, ignore_failure=True
                ):
                    reaped_containers += 1
                    removed_any = True
            # Remove any sidecar container lingering by name and its volumes.
            await self._cleanup_docker_sidecar_resources(
                session_id,
                ignore_failure=True,
                remove_volumes_if_container_missing=True,
            )
            if removed_any:
                reaped_session_ids.append(session_id)
                logger.info(
                    "Reaped orphaned managed session containers for session %s",
                    session_id,
                )
        for session_id in sorted(stale_active_session_ids - set(by_session)):
            record = active_records.get(session_id)
            if record is not None:
                try:
                    await self._mark_session_terminated_by_reconcile(
                        record,
                        reason=(
                            "managed session exceeded reap max age without an "
                            "active turn"
                        ),
                        metadata={
                            "maxAgeSeconds": max_age_seconds,
                            "reapReason": "stale_active_session_max_age",
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to terminate stale active session %s "
                        "(no containers) during reap",
                        session_id,
                    )
                    continue
            forced_stale += 1
            logger.warning(
                "Marked stale active managed session %s terminated after max age "
                "%s seconds; no labeled containers were present",
                session_id,
                max_age_seconds,
            )

        (
            scanned_volumes,
            reaped_volumes,
            skipped_active_volumes,
            skipped_recent_volumes,
        ) = await self._reap_orphan_sidecar_volumes(
            volumes=volumes,
            active_session_ids=active_session_ids,
            grace_seconds=grace_seconds,
            now=now,
        )

        return ManagedSessionReapResult(
            scanned_containers=len(containers),
            reaped_session_ids=tuple(reaped_session_ids),
            reaped_containers=reaped_containers,
            skipped_active=skipped_active,
            skipped_recent=skipped_recent,
            forced_stale=forced_stale,
            scanned_volumes=scanned_volumes,
            reaped_volumes=reaped_volumes,
            skipped_active_volumes=skipped_active_volumes,
            skipped_recent_volumes=skipped_recent_volumes,
        )
