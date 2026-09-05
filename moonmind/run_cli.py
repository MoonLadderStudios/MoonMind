"""Public-contract first-run workflow CLI (MoonLadderStudios/MoonMind#3926).

Thin HTTP client over the same public ``/api/*`` contracts the dashboard UI
uses to submit and inspect workflows:

- ``POST /api/executions`` (task/workflow envelope; omitted profile refs
  resolve to the server default, i.e. the credentialless route)
- ``GET /api/executions/{workflow_id}?source=temporal`` (status)
- ``GET /api/executions/{workflow_id}/captured-evidence`` (terminal evidence)
- ``GET /api/omnigent/bootstrap/readiness`` (admission/readiness)

This module creates no second execution path: it posts the identical envelope
the UI posts, preserves ``Authorization`` and caller-supplied idempotency keys,
and never silently switches to paid credentials or a weaker runtime. Failures
are actionable (what to check next) instead of silent substitutions.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx


EXECUTIONS_PATH = "/api/executions"
BOOTSTRAP_READINESS_PATH = "/api/omnigent/bootstrap/readiness"
CAPTURED_EVIDENCE_PATH_TEMPLATE = "/api/executions/{workflow_id}/captured-evidence"

DEFAULT_API_BASE_URL = "http://localhost:7000"

_PROFILE_SENTINELS = frozenset({"", "auto", "default"})

TERMINAL_EXECUTION_STATES = frozenset(
    {
        "completed",
        "failed",
        "canceled",
        "cancelled",
        "terminated",
        "timed_out",
        "continued_as_new",
    }
)


class RunCliError(RuntimeError):
    """Actionable CLI failure; message already explains the next step."""


def normalize_profile_ref(value: Any) -> str | None:
    """Normalize an omitted/documented-default profile choice to ``None``.

    ``None``, empty string, ``"auto"`` and ``"default"`` (any case) all mean
    "let the server resolve the default", which is the credentialless route
    for a default deployment. Any other value is passed through unchanged
    (stripped) as an explicit override.
    """

    if value is None:
        return None
    normalized = str(value).strip()
    if normalized.lower() in _PROFILE_SENTINELS:
        return None
    return normalized or None


def build_workflow_submit_payload(
    *,
    instructions: str,
    title: str | None = None,
    provider_profile_ref: Any = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Build the UI-equivalent ``POST /api/executions`` workflow envelope.

    Returns ``(payload, effective_idempotency_key)``. The payload uses the
    ``{"type": "workflow", "payload": {"workflow": {...}}}`` shape the UI
    submits; omitted profile refs are left absent so the server applies the
    same default admission as the dashboard. A missing idempotency key is
    generated so retries are safe; a caller-supplied key is preserved verbatim
    (stripped).
    """

    cleaned_instructions = str(instructions or "").strip()
    if not cleaned_instructions:
        raise RunCliError(
            "workflow instructions must not be empty; pass --prompt/--instructions "
            "with a bounded first-run task"
        )
    workflow: dict[str, Any] = {"instructions": cleaned_instructions}
    cleaned_title = str(title or "").strip()
    if cleaned_title:
        workflow["title"] = cleaned_title
    resolved_profile = normalize_profile_ref(provider_profile_ref)
    if resolved_profile is not None:
        workflow["providerProfileRef"] = resolved_profile
    effective_key = str(idempotency_key or "").strip() or uuid4().hex
    workflow["idempotencyKey"] = effective_key
    return ({"type": "workflow", "payload": {"workflow": workflow}}, effective_key)


def resolve_api_base_url(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the API base URL from flag, env, or the local default."""

    source = os.environ if env is None else env
    raw = str(explicit or "").strip()
    if not raw:
        raw = str(
            source.get("MOONMIND_API_BASE_URL")
            or source.get("MOONMIND_URL")
            or DEFAULT_API_BASE_URL
        ).strip()
    if not raw:
        raw = DEFAULT_API_BASE_URL
    return raw.rstrip("/")


def resolve_bearer_token(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the API bearer token without ever inventing credentials."""

    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    source = os.environ if env is None else env
    for key in (
        "MOONMIND_API_TOKEN",
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN",
        "MOONMIND_API_BEARER_TOKEN",
    ):
        candidate = str(source.get(key) or "").strip()
        if candidate:
            return candidate
    token_file = str(
        source.get("MOONMIND_API_TOKEN_FILE")
        or source.get("MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE")
        or ""
    ).strip()
    if token_file:
        try:
            with open(token_file, encoding="utf-8") as handle:
                candidate = handle.read().strip()
        except OSError as exc:
            raise RunCliError(
                f"API token file is unavailable: {token_file}: {exc}"
            ) from exc
        if not candidate:
            raise RunCliError(f"API token file is empty: {token_file}")
        return candidate
    return None


def is_terminal_state(state: Any) -> bool:
    """Return whether an execution ``state``/``status`` value is terminal."""

    return str(state or "").strip().lower() in TERMINAL_EXECUTION_STATES


def _actionable_detail(payload: Any) -> str:
    if isinstance(payload, Mapping):
        detail = payload.get("detail")
        if isinstance(detail, Mapping):
            code = str(detail.get("code") or "").strip()
            message = str(detail.get("message") or "").strip()
            if code and message:
                return f"{code}: {message}"
            return message or code
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        for key in ("message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


class RunApiClient:
    """Minimal synchronous client for the UI's public execution contracts."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_base = str(base_url or "").strip().rstrip("/") or DEFAULT_API_BASE_URL
        headers = {"accept": "application/json"}
        normalized_token = str(bearer_token or "").strip()
        if normalized_token:
            headers["authorization"] = f"Bearer {normalized_token}"
        self._base_url = normalized_base
        self._client = httpx.Client(
            base_url=normalized_base,
            timeout=timeout_seconds,
            transport=transport,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def _actionable_http_error(
        self, *, operation: str, response: httpx.Response
    ) -> RunCliError:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        detail = _actionable_detail(payload)
        status = response.status_code
        if status in {401, 403}:
            suffix = f": {detail}" if detail else ""
            return RunCliError(
                f"{operation} was not authorized (HTTP {status}{suffix}). "
                "Pass --token or set MOONMIND_API_TOKEN; the CLI never retries "
                "without credentials or switches profiles silently."
            )
        if status == 404:
            suffix = f": {detail}" if detail else ""
            return RunCliError(
                f"{operation} was not found (HTTP 404{suffix}). "
                "Verify the workflow id and that the API base URL points at the "
                "same deployment as the dashboard."
            )
        if status == 409:
            suffix = f": {detail}" if detail else ""
            return RunCliError(
                f"{operation} conflicts with the selected runtime/profile "
                f"(HTTP 409{suffix}). The CLI does not substitute another "
                "credential, runtime, or model; pick a ready profile explicitly "
                "or omit --provider-profile for the server default."
            )
        if status == 422:
            suffix = f": {detail}" if detail else ""
            return RunCliError(
                f"{operation} was rejected by admission (HTTP 422{suffix}). "
                "Check `moonmind readiness` for the blocking readiness entry; "
                "when the credentialless route is unavailable the CLI fails "
                "here instead of switching to paid credentials."
            )
        if status == 503:
            suffix = f": {detail}" if detail else ""
            return RunCliError(
                f"{operation} is unavailable (HTTP 503{suffix}). "
                "Confirm `docker compose up -d` is running and retry; partial "
                "startup and provider downtime are environmental failures, not "
                "successful runs."
            )
        suffix = f": {detail}" if detail else ""
        return RunCliError(f"{operation} failed (HTTP {status}{suffix}).")

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method, path, params=dict(params or {}), json=dict(payload)
                if payload is not None
                else None,
            )
        except httpx.RequestError as exc:
            raise RunCliError(
                f"{operation} could not reach the API at {self._base_url}: {exc}. "
                "Confirm `docker compose up -d` is running and MOONMIND_URL/"
                "MOONMIND_API_BASE_URL points at the dashboard deployment."
            ) from exc
        if response.status_code >= 400:
            raise self._actionable_http_error(operation=operation, response=response)
        try:
            return response.json()
        except ValueError as exc:
            raise RunCliError(
                f"{operation} returned invalid JSON from {path}."
            ) from exc

    def submit_workflow(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST the UI-equivalent workflow envelope; returns the execution."""

        result = self._request(
            "POST", EXECUTIONS_PATH, operation="workflow submit", payload=payload
        )
        if not isinstance(result, Mapping):
            raise RunCliError("workflow submit returned an unexpected response.")
        return dict(result)

    def describe_execution(
        self, workflow_id: str, *, source: str = "temporal"
    ) -> dict[str, Any]:
        """GET the UI-equivalent execution status for one workflow."""

        cleaned = str(workflow_id or "").strip()
        if not cleaned:
            raise RunCliError("workflow id must not be empty")
        result = self._request(
            "GET",
            f"{EXECUTIONS_PATH}/{cleaned}",
            operation="workflow status",
            params={"source": source} if source else None,
        )
        if not isinstance(result, Mapping):
            raise RunCliError("workflow status returned an unexpected response.")
        return dict(result)

    def get_readiness(self) -> dict[str, Any]:
        """GET the UI-equivalent Omnigent bootstrap readiness document."""

        result = self._request(
            "GET", BOOTSTRAP_READINESS_PATH, operation="readiness check"
        )
        if not isinstance(result, Mapping):
            raise RunCliError("readiness check returned an unexpected response.")
        return dict(result)

    def get_captured_evidence(self, workflow_id: str) -> dict[str, Any]:
        """GET the UI-equivalent captured-evidence (terminal evidence) document."""

        cleaned = str(workflow_id or "").strip()
        if not cleaned:
            raise RunCliError("workflow id must not be empty")
        path = CAPTURED_EVIDENCE_PATH_TEMPLATE.format(workflow_id=cleaned)
        result = self._request(
            "GET", path, operation="captured evidence"
        )
        if not isinstance(result, Mapping):
            raise RunCliError("captured evidence returned an unexpected response.")
        return dict(result)


def wait_for_terminal(
    client: RunApiClient,
    workflow_id: str,
    *,
    timeout_seconds: float = 600.0,
    poll_seconds: float = 5.0,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Poll UI-equivalent status until a terminal state or timeout."""

    deadline = time.monotonic() + max(float(timeout_seconds), 1.0)
    last: dict[str, Any] = {}
    while True:
        last = client.describe_execution(workflow_id)
        state = last.get("state", last.get("status"))
        if is_terminal_state(state):
            return last
        if time.monotonic() >= deadline:
            raise RunCliError(
                f"timed out waiting for terminal state for workflow {workflow_id}; "
                f"last state was {str(state or 'unknown')!r}. "
                "Use `moonmind status` to re-check; the run remains durable and "
                "no cleanup or credential change was performed."
            )
        sleep(max(float(poll_seconds), 0.1))


def summarize_execution(execution: Mapping[str, Any]) -> str:
    """Render one stable status line from a UI-equivalent execution document."""

    workflow_id = str(
        execution.get("workflowId")
        or execution.get("workflow_id")
        or execution.get("id")
        or "unknown"
    )
    state = str(execution.get("state", execution.get("status", "unknown")))
    title = str(execution.get("title", "") or "").strip()
    run_id = str(execution.get("runId", execution.get("run_id", "")) or "").strip()
    suffix = f" {title}" if title else ""
    run_suffix = f" runId={run_id}" if run_id else ""
    return f"workflow {workflow_id}: {state}{suffix}{run_suffix}"


def summarize_readiness(readiness: Mapping[str, Any]) -> str:
    """Render one stable line from the UI-equivalent readiness document."""

    state = str(readiness.get("state", "unknown"))
    value = str(readiness.get("readiness", "unknown"))
    enabled = readiness.get("enabled")
    return f"readiness={value} state={state} enabled={bool(enabled)}"
