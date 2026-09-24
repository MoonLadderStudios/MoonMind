"""Low-level GitLab REST client with bounded retries and sanitized errors.

Transport mirrors ``moonmind.integrations.jira.client``: an injectable
``httpx.AsyncClient`` (hermetic ``MockTransport`` in tests), bounded retries
for transient statuses, and errors that never carry credential material.
Redirects are followed manually only within the admitted instance authority;
a redirect to another instance raises without forwarding the token.
"""

from __future__ import annotations

import logging as stdlib_logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from moonmind.integrations.gitlab.errors import (
    GitLabIdentityError,
    GitLabTokenExpiredError,
    GitLabToolError,
)
from moonmind.integrations.gitlab.identity import resolve_gitlab_identity

logger = stdlib_logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_REDIRECTS = 3


@dataclass(frozen=True, slots=True)
class ResolvedGitLabConnection:
    """Resolved GitLab connection details for one trusted tool call."""

    base_url: str
    headers: dict[str, str]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    retry_attempts: int
    redaction_values: tuple[str, ...]


def build_gitlab_connection(
    *,
    endpoint: str,
    token: str,
    admitted_endpoints: tuple[str, ...] | list[str],
    connect_timeout_seconds: float = 10.0,
    read_timeout_seconds: float = 30.0,
    retry_attempts: int = 3,
    action: str | None = None,
) -> ResolvedGitLabConnection:
    """Build connection details bound to one admitted instance.

    The token is accepted only as opaque material for the ``PRIVATE-TOKEN``
    header; the endpoint allowlist check happens in identity resolution, so an
    unapproved instance never reaches transport construction.
    """

    # Validate the instance through identity resolution (project/MR values are
    # placeholders here; callers re-resolve the concrete MR identity).
    ref = resolve_gitlab_identity(
        endpoint=endpoint,
        project="group/project",
        mr_iid=1,
        admitted_endpoints=tuple(admitted_endpoints),
        action=action,
    )
    clean_token = str(token or "").strip()
    if not clean_token or "\n" in clean_token or "\r" in clean_token:
        raise GitLabToolError(
            "GitLab token is not configured.",
            code="gitlab_not_configured",
            status_code=503,
            action=action,
        )
    base_url = f"{ref.project.endpoint}/api/v4"
    authorization = f"PRIVATE-TOKEN {clean_token}"
    return ResolvedGitLabConnection(
        base_url=base_url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "PRIVATE-TOKEN": clean_token,
        },
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        retry_attempts=max(1, int(retry_attempts)),
        redaction_values=(clean_token, authorization),
    )


def connection_from_bound_credential(
    acquired: Any,
    *,
    endpoint: str,
    admitted_endpoints: tuple[str, ...] | list[str],
    action: str | None = None,
) -> ResolvedGitLabConnection:
    """Build connection details from a bound acquisition credential.

    Consumes the same ``EphemeralCredential.use_now`` immediate-use boundary
    as ``moonmind.publish.service`` so raw material never leaves the trusted
    scope; accepts any object exposing ``use_now(fn)``.
    """

    captured: list[bytes] = []
    acquired.credential.use_now(captured.append)
    token = bytes(captured[0]) if captured else b""
    return build_gitlab_connection(
        endpoint=endpoint,
        token=token.decode("utf-8", errors="strict"),
        admitted_endpoints=tuple(admitted_endpoints),
        action=action,
    )


def _authority(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}:{parts.port or ''}"


class GitLabClient:
    """Async GitLab REST wrapper for trusted managed-agent tool execution."""

    def __init__(
        self,
        *,
        connection: ResolvedGitLabConnection,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._connection = connection
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(
                connect=connection.connect_timeout_seconds,
                read=connection.read_timeout_seconds,
                write=connection.read_timeout_seconds,
                pool=connection.connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                base_url=connection.base_url,
                headers=connection.headers,
                timeout=timeout,
                follow_redirects=False,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_json(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        """Perform one GitLab request with bounded retry handling."""

        response = await self._request_raw(
            method=method,
            path=path,
            action=action,
            params=params,
            json_body=json_body,
            context=context,
        )
        try:
            return response.json()
        except Exception as exc:
            raise GitLabToolError(
                "GitLab response could not be decoded.",
                code="gitlab_request_failed",
                status_code=502,
                action=action,
            ) from exc

    async def _request_raw(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        attempts = max(self._connection.retry_attempts, 1)
        admitted_authority = _authority(self._connection.base_url)
        last_error: GitLabToolError | None = None
        current_path = path
        redirects = 0
        for attempt in range(1, attempts + 1):
            response = await self._client.request(
                method, current_path, params=params, json=json_body
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                target = httpx.URL(
                    location
                    if "://" in location
                    else f"{self._connection.base_url}{location}"
                )
                if _authority(str(target)) != admitted_authority:
                    raise GitLabIdentityError(
                        "GitLab redirect leaves the admitted instance; "
                        "credentials were not forwarded.",
                        action=action,
                    )
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    raise GitLabToolError(
                        "GitLab redirect limit exceeded.",
                        code="gitlab_request_failed",
                        status_code=502,
                        action=action,
                    )
                current_path = str(target)
                params = None
                continue
            if response.status_code == 401:
                raise GitLabTokenExpiredError(
                    "GitLab credential expired or was revoked.",
                    action=action,
                )
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                last_error = GitLabToolError(
                    f"GitLab transient status {response.status_code}.",
                    code="gitlab_transient",
                    status_code=response.status_code,
                    action=action,
                )
                continue
            if response.status_code >= 400:
                raise GitLabToolError(
                    f"GitLab request failed with status {response.status_code}.",
                    code="gitlab_request_failed",
                    status_code=response.status_code,
                    action=action,
                )
            return response
        raise last_error or GitLabToolError(
            "GitLab request failed after retries.",
            code="gitlab_request_failed",
            status_code=502,
            action=action,
        )
