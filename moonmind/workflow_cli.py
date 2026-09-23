"""Thin authenticated workflow CLI client for MoonMind executions.

MoonLadderStudios/MoonMind#3939: ordinary authenticated ``run``/``status``/
``logs``/``download`` commands against the same ``/api/executions`` contract
the dashboard uses. Presets, defaults, model/profile selection, and
publication normalization stay server-owned; this module only builds canonical
inputs and parses bounded, secret-safe output.

Design rules (from the issue brief):

- No local provider, database, Docker, or native RAG/Manifest imports. Only
  stdlib plus ``httpx`` (already a runtime dependency).
- No Manifest command, alias, special submit path, or disabled stub.
- Secrets travel via ``MOONMIND_API_TOKEN`` / ``MOONMIND_API_TOKEN_FILE`` only,
  never as CLI arguments and never printed in URLs.
- Secure transport is required when a bearer credential is sent to a remote
  (non-loopback) endpoint; redirects never carry credentials across hosts.
- ``--repo <local path>`` is never interpreted as a server filesystem path.
- ``--profile`` is rejected: callers must name ``--agent-profile`` or
  ``--provider-profile`` explicitly.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx

from moonmind.utils.logging import redact_sensitive_text

DEFAULT_API_BASE = "http://127.0.0.1:7000"
TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
NON_TERMINAL_STATUSES = frozenset({"queued", "running", "awaiting_action", "waiting"})

# Documented exit codes for the workflow commands.
EXIT_OK = 0
EXIT_USAGE_ERROR = 1  # submission/auth/transport/usage errors
EXIT_WORK_FAILED = 2  # failed or canceled remote work
EXIT_STILL_RUNNING = 3  # bounded wait expired while work is still running

_MAX_OUTPUT_CHARS = 8000
_MAX_LOG_LINES = 200
# Bound for one CLI evidence download. Larger saved artifacts stay reachable
# through the Workflow Detail page; the CLI fails actionably instead of
# buffering unbounded bytes into the terminal host.
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Z]")
_FILENAME_PATTERN = re.compile(
    r"filename\*?\s*=\s*(?:\"([^\"]{1,255})\"|([^;,\s]{1,255}))", re.IGNORECASE
)
_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RETIRED_PARAM_KEYS = frozenset(
    {
        "manifest",
        "manifestartifactref",
        "rag",
        "followupretrieval",
        "follow_up_retrieval",
        "vector",
    }
)


class WorkflowCliError(RuntimeError):
    """Actionable failure with secret-safe detail for CLI display."""


def new_request_id() -> str:
    """Generate a stable-by-default idempotency key segment."""
    return uuid4().hex


def resolve_api_base(env: Mapping[str, str] | None = None) -> str:
    """Resolve the API base URL from the documented local default."""
    source = os.environ if env is None else env
    for key in ("MOONMIND_API_BASE", "MOONMIND_URL"):
        value = str(source.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_API_BASE


def resolve_bearer_token(env: Mapping[str, str] | None = None) -> str | None:
    """Read the bearer credential from protected input mechanisms only."""
    source = os.environ if env is None else env
    file_selector = str(source.get("MOONMIND_API_TOKEN_FILE") or "").strip()
    if file_selector:
        try:
            value = Path(file_selector).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise WorkflowCliError(
                "MOONMIND_API_TOKEN_FILE is unavailable; "
                "store the API token in a protected file and retry."
            ) from exc
        if not value:
            raise WorkflowCliError("MOONMIND_API_TOKEN_FILE is empty.")
        return value
    value = str(source.get("MOONMIND_API_TOKEN") or "").strip()
    return value or None


def is_loopback_url(base_url: str) -> bool:
    """Return True for loopback-only API base URLs."""
    try:
        host = (urlsplit(base_url).hostname or "").strip().lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1", "[::1]"}


def require_secure_transport(
    base_url: str, *, has_token: bool, env: Mapping[str, str] | None = None
) -> None:
    """Fail closed when a credential would travel over unsafe transport."""
    source = os.environ if env is None else env
    if not has_token:
        return
    try:
        scheme = (urlsplit(base_url).scheme or "").strip().lower()
    except ValueError as exc:
        raise WorkflowCliError(
            f"invalid API base URL: {redact_sensitive_text(base_url)}"
        ) from exc
    if scheme == "https":
        return
    if scheme == "http" and is_loopback_url(base_url):
        return
    allow_insecure = str(source.get("MOONMIND_ALLOW_INSECURE_REMOTE") or "").strip() == "1"
    if allow_insecure:
        return
    raise WorkflowCliError(
        "refusing to send an API token over insecure remote transport "
        f"(base {redact_sensitive_text(base_url)} is not https or loopback). "
        "Use https, target the documented local endpoint, or set "
        "MOONMIND_ALLOW_INSECURE_REMOTE=1 explicitly for a trusted network."
    )


def detail_url(base_url: str, workflow_id: str) -> str:
    """Build the dashboard detail URL without embedding credentials."""
    try:
        parts = urlsplit(base_url.rstrip("/"))
        if parts.username or parts.password:
            # Never copy URL userinfo (credentials) into terminal/JSON output.
            hostname = parts.hostname or ""
            netloc = hostname
            if parts.port is not None:
                netloc = f"{hostname}:{parts.port}"
            base_url = parts._replace(netloc=netloc).geturl().rstrip("/")
        else:
            base_url = base_url.rstrip("/")
    except ValueError:
        base_url = base_url.rstrip("/")
    return f"{base_url}/workflows/{workflow_id}"


def sanitize_terminal_text(value: str, *, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Strip terminal control sequences and bound untrusted log output."""
    text = _ANSI_PATTERN.sub("", value)
    text = _CONTROL_CHAR_PATTERN.sub("", text)
    text = text.replace("\r", "\n")
    redacted = redact_sensitive_text(text)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + "\n…[truncated]"
    return redacted


def evidence_download_filename(
    ref: str, content_disposition: str | None = None
) -> str:
    """Derive a browser-safe download filename for one evidence ref.

    Prefers the server's ``content-disposition`` filename when present (the
    same name the Workflow Detail download would save as), otherwise derives
    the trailing segment of the artifact ref the way the server does. The
    result is always a bare filename: directory components, control
    characters, and blank values fall back to ``captured-evidence`` so a
    hostile ref can never steer the CLI write outside ``--out``.
    """
    candidate = ""
    if content_disposition:
        match = _FILENAME_PATTERN.search(content_disposition)
        if match:
            candidate = (match.group(1) or match.group(2) or "").strip()
            if candidate.lower().startswith("utf-8''"):
                candidate = candidate[7:]
            try:
                candidate = unquote(candidate)
            except ValueError:
                # Keep the raw candidate: unquote only fails on malformed
                # %-escapes, and the sanitization below still yields a safe
                # bare filename.
                pass
    if not candidate:
        candidate = (
            (ref or "").rstrip("/").rsplit("/", 1)[-1].removeprefix("artifact://").strip()
        )
    candidate = candidate.replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = _CONTROL_CHAR_PATTERN.sub("", candidate).strip().strip(".")
    if not candidate:
        return "captured-evidence"
    return candidate[:255]


def save_evidence_download(
    path: str | Path, content: bytes, *, overwrite: bool = False
) -> Path:
    """Save downloaded evidence bytes to an explicit destination file.

    An existing file is never silently replaced: without ``overwrite`` the
    call fails actionably so accepted saved work survives a repeated
    download. Missing parent directories are created.
    """
    target = Path(os.path.expanduser(str(path)))
    if not str(target).strip():
        raise WorkflowCliError("an --out file path is required.")
    if target.exists() and not overwrite:
        raise WorkflowCliError(
            f"refusing to overwrite existing file {redact_sensitive_text(str(target))}; "
            "pass --overwrite or choose another --out path. Saved work is never "
            "silently replaced."
        )
    try:
        parent = target.parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(content))
    except OSError as exc:
        raise WorkflowCliError(
            f"could not write download to {redact_sensitive_text(str(target))}: "
            f"{exc.strerror or type(exc).__name__}"
        ) from exc
    return target


def validate_repository(value: str | None) -> str | None:
    """Accept only backend-admitted repository source types.

    A local filesystem path is never a valid server source. Local content
    requires an implemented authorized upload/workspace mechanism, which this
    thin CLI does not invent.
    """
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if lowered.startswith(("file://", "file:", "/")) or candidate.startswith(
        ("./", "../", "~", ".\\")
    ):
        raise WorkflowCliError(
            f"repository {redact_sensitive_text(candidate)!r} looks like a local "
            "path; the CLI never interprets --repository as a server filesystem "
            "path. Pass owner/repo or a backend-admitted URL, or omit it."
        )
    if "://" in candidate:
        scheme = candidate.split("://", 1)[0].lower()
        if scheme not in {"https"}:
            raise WorkflowCliError(
                f"repository {redact_sensitive_text(candidate)!r} uses unsupported "
                "scheme; pass an https URL or owner/repo."
            )
        return candidate
    if _OWNER_REPO_PATTERN.match(candidate):
        return candidate
    # A bare local directory name that exists on disk is almost certainly a
    # mistaken local path rather than a backend source.
    try:
        if Path(candidate).exists():
            raise WorkflowCliError(
                f"repository {redact_sensitive_text(candidate)!r} matches a local "
                "filesystem entry; the CLI never uploads local paths implicitly. "
                "Pass owner/repo or a backend-admitted URL, or omit it."
            )
    except OSError:
        # Best-effort local-path guard only: an unreadable filesystem must not
        # mask the backend-admitted source validation below.
        pass
    raise WorkflowCliError(
        f"repository {redact_sensitive_text(candidate)!r} is not a backend-admitted "
        "source; pass owner/repo or an https URL, or omit it."
    )


def parse_extra_params(items: Sequence[str] | None) -> dict[str, str]:
    """Parse repeatable ``--param key=value`` inputs with retired-key guards."""
    parsed: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise WorkflowCliError(
                f"invalid --param {redact_sensitive_text(item)!r}; use key=value."
            )
        key, _, raw_value = item.partition("=")
        key = key.strip()
        if not key:
            raise WorkflowCliError("invalid --param with an empty key; use key=value.")
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if normalized in _RETIRED_PARAM_KEYS or normalized.startswith("manifest"):
            raise WorkflowCliError(
                f"--param {redact_sensitive_text(key)!r} requests retired "
                "Manifest/RAG behavior; resubmit without retired requirements."
            )
        parsed[key] = raw_value
    return parsed


def build_execution_payload(
    *,
    instructions: str | None = None,
    preset: str | None = None,
    skill: str | None = None,
    title: str | None = None,
    repository: str | None = None,
    agent_profile: str | None = None,
    provider_profile: str | None = None,
    publish_mode: str | None = None,
    extra_params: Mapping[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build the canonical ``POST /api/executions`` payload.

    Preset expansion, defaults, model/profile resolution, and publication
    normalization remain server-owned; the CLI only names the preset/Skill and
    passes through small task fields.
    """
    preset_slug = (preset or "").strip()
    skill_name = (skill or "").strip()
    if preset_slug and skill_name:
        raise WorkflowCliError(
            "pass either --preset or --skill, not both; the preset slug and the "
            "Skill name are distinct selectors."
        )
    text = (instructions or "").strip()
    if not text and not preset_slug and not skill_name:
        raise WorkflowCliError(
            "provide --instructions, --preset, or --skill so the server has a "
            "plan source for MoonMind.UserWorkflow."
        )
    # A preset-only submission carries no instructions/skill plan source, which
    # the server rejects with 422 before preset expansion. Preserve the preset
    # provenance while giving the server an explicit goal so the request is
    # admitted and expanded server-side instead of rejected.
    if preset_slug and not text and not skill_name:
        text = f"Run preset {preset_slug}"
    repo = validate_repository(repository)
    task: dict[str, Any] = {}
    if text:
        task["instructions"] = text
        task["goal"] = text
    if preset_slug:
        task["taskTemplate"] = {"slug": preset_slug, "scope": "global"}
    if skill_name:
        task["steps"] = [{"skill": {"name": skill_name}}]
    if repo:
        task["repository"] = repo
    if agent_profile and agent_profile.strip():
        # Canonical agent-profile selector is {"profileId": ...} at the
        # top-level payload and runtime locations; keep the task copy for
        # backward compatibility with readers of the task envelope.
        task["agentProfile"] = {"profileId": agent_profile.strip()}
    if provider_profile and provider_profile.strip():
        # Canonical provider-profile aliases recognized by the executions
        # router and runtime selection; populate every alias so the explicit
        # selection reaches the worker instead of falling back to default.
        task["providerProfileRef"] = provider_profile.strip()
        task["profileId"] = provider_profile.strip()
        task["providerProfile"] = provider_profile.strip()
    if publish_mode is not None:
        normalized_publish = publish_mode.strip().lower()
        if normalized_publish not in {"auto", "none", "branch", "pr"}:
            raise WorkflowCliError(
                f"invalid --publish-mode {redact_sensitive_text(publish_mode)!r}; "
                "use auto, none, branch, or pr."
            )
        # Canonical publication contract is task.publish.mode (plus the
        # top-level aliases); keep the legacy task.publishMode copy.
        task["publishMode"] = normalized_publish
        task["publish"] = {"mode": normalized_publish}
    for key, value in dict(extra_params or {}).items():
        task.setdefault(key, value)
    initial_parameters: dict[str, Any] = {"task": task}
    # Mirror explicit selectors at the initialParameters level as well: the
    # executions router and runtime selection read provider/agent profiles and
    # publication intent from task, runtime, and top-level locations, and the
    # thin CreateExecutionRequest path never normalizes task-only fields.
    if task.get("agentProfile") is not None:
        initial_parameters["agentProfile"] = dict(task["agentProfile"])
    for alias in ("providerProfileRef", "profileId", "providerProfile"):
        if task.get(alias) is not None:
            initial_parameters[alias] = task[alias]
    if task.get("publish") is not None:
        initial_parameters["publish"] = dict(task["publish"])
    if task.get("publishMode") is not None:
        initial_parameters["publishMode"] = task["publishMode"]
    payload: dict[str, Any] = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": initial_parameters,
    }
    if title and title.strip():
        payload["title"] = title.strip()
    key = (idempotency_key or "").strip() or new_request_id()
    payload["idempotencyKey"] = key
    return payload


@dataclass(slots=True)
class ExecutionSummary:
    workflow_id: str
    status: str
    state: str
    title: str
    run_id: str | None = None


def summarize_execution(payload: Mapping[str, Any]) -> ExecutionSummary:
    """Parse a bounded execution projection without trusting extra fields."""
    workflow_id = str(payload.get("workflowId") or payload.get("workflow_id") or "").strip()
    if not workflow_id:
        raise WorkflowCliError("the server response contained no workflowId.")
    status = str(payload.get("status") or payload.get("dashboardStatus") or "unknown").strip()
    state = str(payload.get("state") or status).strip()
    title = str(payload.get("title") or "").strip()
    run_id = payload.get("runId") or payload.get("run_id")
    return ExecutionSummary(
        workflow_id=workflow_id,
        status=status or "unknown",
        state=state or status or "unknown",
        title=title,
        run_id=str(run_id).strip() if run_id else None,
    )


@dataclass(slots=True)
class EvidenceDownload:
    """One downloaded captured-evidence artifact: filename plus raw bytes."""

    filename: str
    content: bytes
    content_type: str | None = None


@dataclass(slots=True)
class WorkflowApiClient:
    """Small synchronous client for the public executions API."""

    base_url: str
    bearer_token: str | None = None
    timeout_seconds: float = 30.0
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        headers = {"accept": "application/json"}
        if self.bearer_token:
            headers["authorization"] = f"Bearer {self.bearer_token}"
        # Redirects are intentionally not followed automatically so a bearer
        # credential is never forwarded across hosts. A 3xx becomes an
        # actionable error naming the location without echoing secrets.
        # trust_env=False keeps credentialed loopback requests from being
        # routed through an environment-configured HTTP(S) proxy (which would
        # disclose the bearer token to that proxy); explicit proxy use stays
        # opt-in via transport configuration, not ambient env vars.
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers=headers,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _check_redirect(self, response: httpx.Response, *, action: str) -> None:
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            raise WorkflowCliError(
                f"the server redirected the {action} request"
                f"{f' to {redact_sensitive_text(location)}' if location else ''}; "
                "credentials are not forwarded across redirects/hosts. "
                "Re-run against the deployment's supported API base URL."
            )

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        self._check_redirect(response, action=action)
        if response.status_code < 400:
            return
        detail = ""
        code = ""
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        if isinstance(error_payload, Mapping):
            raw_detail = error_payload.get("detail")
            if isinstance(raw_detail, Mapping):
                code = str(raw_detail.get("code") or "")
                message = raw_detail.get("message") or raw_detail.get("reason") or ""
                detail = str(message or "").strip()
            elif raw_detail is not None:
                detail = str(raw_detail).strip()
        redacted_detail = redact_sensitive_text(detail)[:500]
        if response.status_code == 401:
            hint = (
                "authentication failed (wrong owner, expired, or missing token). "
                "Set MOONMIND_API_TOKEN or MOONMIND_API_TOKEN_FILE for the "
                "deployment's supported auth mechanism; 'disabled' local mode "
                "needs no token on the documented loopback endpoint."
            )
            if "expired" in (detail + code).lower():
                hint = (
                    "the API token expired; refresh the deployment credential and "
                    "retry with the same --request-id to reconcile."
                )
            raise WorkflowCliError(
                f"{action} was not authorized (HTTP 401)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. {hint}"
            )
        if response.status_code == 403:
            raise WorkflowCliError(
                f"{action} was forbidden (HTTP 403)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. "
                "The credential is valid but this owner may not access the workflow."
            )
        if response.status_code == 404:
            if action == "evidence download":
                raise WorkflowCliError(
                    f"evidence download found no such captured evidence (HTTP 404)"
                    f"{f': ' + redacted_detail if redacted_detail else ''}. "
                    "Check the workflow ID and pick an authorized artifact ref "
                    "from the captured-evidence read "
                    "(`moonmind workflow logs --json <workflow-id>`)."
                )
            raise WorkflowCliError(
                f"{action} found no such workflow (HTTP 404)"
                f"{f': ' + redacted_detail if redacted_detail else ''}."
            )
        if response.status_code == 409:
            raise WorkflowCliError(
                f"{action} conflicted (HTTP 409)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. "
                "Retrying the same --request-id returns the admitted workflow; "
                "a new intentional request needs a fresh --request-id."
            )
        if response.status_code == 410:
            raise WorkflowCliError(
                f"{action} used a retired path (HTTP 410)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. "
                "Retired Manifest/RAG workflows are readable only; they cannot "
                "be launched or resumed."
            )
        if response.status_code == 422:
            raise WorkflowCliError(
                f"{action} was rejected (HTTP 422)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. "
                "The CLI submits canonical inputs only; presets, defaults, and "
                "publication normalization remain server-owned."
            )
        if response.status_code == 503:
            raise WorkflowCliError(
                f"{action} is unavailable (HTTP 503)"
                f"{f': ' + redacted_detail if redacted_detail else ''}. "
                "The deployment cannot admit or serve work right now."
            )
        raise WorkflowCliError(
            f"{action} failed (HTTP {response.status_code})"
            f"{f': ' + redacted_detail if redacted_detail else ''}."
        )

    def submit_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Submit one workflow; transport loss stays retryable, not fatal.

        A lost POST acknowledgment must be reconciled by retrying the same
        idempotency key — never by minting a fresh request for the same
        intent — so at-most-one workflow is admitted per intent.
        """
        assert self._client is not None
        request_key = str(dict(payload).get("idempotencyKey") or "").strip()
        try:
            response = self._client.post("/api/executions", json=dict(payload))
        except httpx.RequestError as exc:
            raise WorkflowCliError(
                "the submission POST lost its acknowledgment "
                f"({type(exc).__name__}); retry the identical command with the "
                "same --request-id to reconcile before minting a new request."
                + (
                    f" The generated request-id for this attempt was {request_key}; "
                    "re-run with --request-id "
                    f"{request_key} to reconcile."
                    if request_key
                    else ""
                )
            ) from exc
        self._raise_for_status(response, action="workflow submission")
        try:
            body = response.json()
        except ValueError as exc:
            raise WorkflowCliError(
                "workflow submission returned invalid JSON; retry with the same "
                "--request-id to reconcile before minting a new request."
            ) from exc
        if not isinstance(body, Mapping):
            raise WorkflowCliError(
                "workflow submission returned an unexpected body; retry with the "
                "same --request-id to reconcile."
            )
        return dict(body)

    def describe_execution(self, workflow_id: str) -> dict[str, Any]:
        workflow_id = workflow_id.strip()
        if not workflow_id:
            raise WorkflowCliError("a workflow ID is required.")
        assert self._client is not None
        try:
            response = self._client.get(f"/api/executions/{workflow_id}")
        except httpx.RequestError as exc:
            raise WorkflowCliError(
                f"status read failed: {type(exc).__name__}; the remote workflow "
                "is unaffected — retry the read."
            ) from exc
        self._raise_for_status(response, action="status read")
        try:
            body = response.json()
        except ValueError as exc:
            raise WorkflowCliError("status read returned invalid JSON.") from exc
        if not isinstance(body, Mapping):
            raise WorkflowCliError("status read returned an unexpected body.")
        return dict(body)

    def captured_evidence(self, workflow_id: str) -> dict[str, Any] | None:
        """Return terminal evidence or None when auxiliary logs are absent."""
        workflow_id = workflow_id.strip()
        if not workflow_id:
            raise WorkflowCliError("a workflow ID is required.")
        assert self._client is not None
        try:
            response = self._client.get(f"/api/executions/{workflow_id}/captured-evidence")
        except httpx.RequestError as exc:
            raise WorkflowCliError(
                f"evidence read failed: {type(exc).__name__}; the remote workflow "
                "is unaffected — retry the read."
            ) from exc
        if response.status_code == 404:
            return None
        self._raise_for_status(response, action="evidence read")
        try:
            body = response.json()
        except ValueError as exc:
            raise WorkflowCliError("evidence read returned invalid JSON.") from exc
        return dict(body) if isinstance(body, Mapping) else None

    def step_ledger(self, workflow_id: str) -> dict[str, Any] | None:
        """Return the step ledger projection or None when unavailable."""
        workflow_id = workflow_id.strip()
        if not workflow_id:
            raise WorkflowCliError("a workflow ID is required.")
        assert self._client is not None
        try:
            response = self._client.get(f"/api/executions/{workflow_id}/steps")
        except httpx.RequestError as exc:
            raise WorkflowCliError(
                f"step read failed: {type(exc).__name__}; the remote workflow "
                "is unaffected — retry the read."
            ) from exc
        if response.status_code in {404, 422}:
            return None
        self._raise_for_status(response, action="step read")
        try:
            body = response.json()
        except ValueError as exc:
            raise WorkflowCliError("step read returned invalid JSON.") from exc
        return dict(body) if isinstance(body, Mapping) else None

    def download_captured_evidence(self, workflow_id: str, ref: str) -> EvidenceDownload:
        """Download one saved captured-evidence artifact's bytes.

        MoonLadderStudios/MoonMind#3926: the CLI shares the Workflow Detail
        page's download contract
        (``GET /api/executions/{workflowId}/captured-evidence/download?ref=``)
        so the default installation-to-session-to-download journey is
        completable from either surface. The server authorizes the caller
        against the Workflow and confirms the ref is one of that Workflow's
        authorized evidence refs; an unknown workflow or unauthorized ref is
        a 404, never workflow failure.
        """
        workflow_id = workflow_id.strip()
        if not workflow_id:
            raise WorkflowCliError("a workflow ID is required.")
        clean_ref = (ref or "").strip()
        if not clean_ref:
            raise WorkflowCliError(
                "an artifact ref is required; pick one from the captured-evidence "
                "read (`moonmind workflow logs --json <workflow-id>`)."
            )
        assert self._client is not None
        try:
            response = self._client.get(
                f"/api/executions/{workflow_id}/captured-evidence/download",
                params={"ref": clean_ref},
            )
        except httpx.RequestError as exc:
            raise WorkflowCliError(
                f"evidence download failed: {type(exc).__name__}; the remote workflow "
                "is unaffected — retry the download."
            ) from exc
        self._raise_for_status(response, action="evidence download")
        payload = response.content
        if len(payload) > _MAX_DOWNLOAD_BYTES:
            raise WorkflowCliError(
                f"evidence download is {len(payload)} bytes, above the CLI bound "
                f"({_MAX_DOWNLOAD_BYTES} bytes); download the artifact from the "
                "Workflow Detail page instead."
            )
        content_type = response.headers.get("content-type")
        return EvidenceDownload(
            filename=evidence_download_filename(
                clean_ref, response.headers.get("content-disposition")
            ),
            content=payload,
            content_type=content_type.strip() if content_type else None,
        )


def format_execution_json(payload: Mapping[str, Any]) -> str:
    """Render stable machine-readable output with secrets redacted."""
    from moonmind.utils.logging import redact_sensitive_payload

    redacted = redact_sensitive_payload(dict(payload))
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}
    return json.dumps(redacted, indent=2, sort_keys=True)[:_MAX_OUTPUT_CHARS]


def render_log_lines(
    evidence: Mapping[str, Any] | None,
    ledger: Mapping[str, Any] | None,
    *,
    max_lines: int = _MAX_LOG_LINES,
) -> tuple[list[str], str | None]:
    """Render bounded log lines; auxiliary failure never masks terminal work."""
    lines: list[str] = []
    if isinstance(evidence, Mapping):
        summary = evidence.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append(sanitize_terminal_text(summary.strip(), max_chars=2000))
        items = evidence.get("items")
        if isinstance(items, list):
            for item in items:
                if len(lines) >= max_lines:
                    break
                if not isinstance(item, Mapping):
                    continue
                label = str(item.get("label") or item.get("kind") or "evidence").strip()
                ref = str(item.get("artifactRef") or item.get("artifact_ref") or "").strip()
                if ref:
                    lines.append(f"{label}: {ref}"[:500])
        unavailable = evidence.get("unavailable_reason") or evidence.get("unavailableReason")
        if not lines and isinstance(unavailable, str) and unavailable.strip():
            return [], sanitize_terminal_text(unavailable.strip(), max_chars=1000)
    if isinstance(ledger, Mapping):
        steps = ledger.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if len(lines) >= max_lines:
                    break
                if not isinstance(step, Mapping):
                    continue
                title = str(
                    step.get("title") or step.get("logicalStepId") or step.get("id") or ""
                ).strip()
                state = str(step.get("state") or step.get("status") or "").strip()
                if title or state:
                    lines.append(f"{title} [{state}]"[:500] if state else title[:500])
    if not lines:
        return [], "terminal logs are unavailable for this workflow."
    sanitized = [sanitize_terminal_text(line, max_chars=1000) for line in lines]
    return sanitized[:max_lines], None


def wait_for_terminal(
    client: WorkflowApiClient,
    workflow_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Poll the authorized read contract until terminal or bounded timeout."""
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    last: dict[str, Any] = {}
    while True:
        last = client.describe_execution(workflow_id)
        summary = summarize_execution(last)
        if summary.status in TERMINAL_STATUSES:
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(max(0.2, min(poll_seconds, deadline - time.monotonic())))


def exit_code_for_status(status: str, *, timed_out: bool = False) -> int:
    """Map observation outcomes to the documented exit codes."""
    normalized = (status or "").strip().lower()
    if normalized in {"completed"}:
        return EXIT_OK
    if normalized in {"failed", "canceled"}:
        return EXIT_WORK_FAILED
    if timed_out:
        return EXIT_STILL_RUNNING
    if normalized in NON_TERMINAL_STATUSES:
        return EXIT_STILL_RUNNING
    return EXIT_USAGE_ERROR
