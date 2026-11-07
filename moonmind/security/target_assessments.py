"""Bounded target observations using the shared WebAssessment scope.

Deployment workload profiles supply this client's scope and file credential
slots. Existing ContainerJob admission, credential grants, restricted egress,
deadlines and artifact collection remain authoritative. Callers supply requests,
not authority. HTTP and WebSocket messages share the same rate and total budget;
neither transport retries or follows redirects.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

import aiohttp
from pydantic import Field, field_validator, model_validator

from moonmind.schemas.repository_scanner_models import (
    HttpMethod,
    WebAssessment,
    web_route,
)
from moonmind.security.assessments import Contract, Text, content_key
from moonmind.security.authorization_matrix import IdentityRef
from moonmind.security.web_assessments import path_admitted
from moonmind.security.workload_profiles import WORKLOAD_CREDENTIALS_TARGET

MAX_MESSAGE_BYTES = 256 * 1024


class TargetScope(Contract):
    web: WebAssessment
    messages: dict[str, list[IdentityRef]] = Field(default_factory=dict, max_length=32)
    credential_slots: dict[Text, IdentityRef] = Field(
        default_factory=dict, max_length=8
    )
    max_requests: int = Field(4096, strict=True, ge=1, le=8192)

    @model_validator(mode="after")
    def _consistent(self):
        if self.web.identity is not None:
            raise ValueError("target probes select their own admitted identity slot")
        for path, messages in self.messages.items():
            if web_route(path) != path or not path_admitted(self.web, path):
                raise ValueError("real-time channels require an admitted GET route")
            if (
                not messages
                or len(messages) > 64
                or len(set(messages)) != len(messages)
            ):
                raise ValueError("channels require unique permitted message types")
        if any(
            "/" in slot or slot in {".", ".."}
            for slot in self.credential_slots.values()
        ):
            raise ValueError("credentials name existing profile file slots")
        return self


class TargetRequest(Contract):
    id: IdentityRef
    protocol: Literal["http", "websocket"] = "http"
    path: str
    method: HttpMethod = "POST"
    message_type: IdentityRef | None = None
    credential_ref: Text | None = None
    payload: dict = Field(default_factory=dict)

    _path = field_validator("path")(web_route)

    @model_validator(mode="after")
    def _bounded(self):
        if len(json.dumps(self.payload).encode()) > MAX_MESSAGE_BYTES:
            raise ValueError("target request exceeds message bound")
        if self.protocol == "websocket":
            if (
                self.method != "GET"
                or self.message_type is None
                or self.payload.get("type") != self.message_type
            ):
                raise ValueError(
                    "WebSocket requests declare their handshake and message type"
                )
        elif self.message_type is not None:
            raise ValueError("only real-time requests declare a message type")
        return self


class TargetObservation(Contract):
    request_id: IdentityRef
    protocol: Literal["http", "websocket"]
    status: int = Field(ge=100, le=599)
    payload: dict
    evidence_ref: Text


class TargetStopped(RuntimeError):
    """A bounded stop, including evidence errors, without target/secret text."""


class ScopedTargetClient:
    def __init__(
        self,
        scope: TargetScope,
        *,
        credential_root: Path = Path(WORKLOAD_CREDENTIALS_TARGET),
        proxy: str | None,
        should_stop: Callable[[], bool] = lambda: False,
        max_seconds: float | None = None,
    ):
        if not proxy:
            raise PermissionError(
                "target observations require the admitted egress proxy"
            )
        self.scope, self.credential_root, self.proxy = scope, credential_root, proxy
        self.should_stop = should_stop
        self.max_seconds = min(
            300 if max_seconds is None else max_seconds, scope.web.timeout_seconds
        )
        self.started = time.monotonic()
        self.next_request = self.started
        self.requests_sent = 0
        self._exchange_lock = asyncio.Lock()

    def admit(self, request: TargetRequest) -> None:
        """Validate before any credential read, handshake or request is sent."""
        path = request.path
        # Reject encoded separators/dot segments instead of guessing how a
        # proxy or target will normalize them after the shared route guard.
        if unquote(path) != path or not path_admitted(
            self.scope.web, path, request.method
        ):
            raise PermissionError("target route or method is not admitted")
        if (
            request.credential_ref is not None
            and request.credential_ref not in self.scope.credential_slots
        ):
            raise PermissionError("target credential is not admitted")
        if (
            request.protocol == "websocket"
            and request.message_type not in self.scope.messages.get(path, [])
        ):
            raise PermissionError("target message type is not admitted")
        if self.scope.web.mode != "active" and (
            request.method not in {"GET", "HEAD"} or request.protocol == "websocket"
        ):
            raise PermissionError("target effects require an active assessment")

    def _remaining(self):
        if self.should_stop():
            raise TargetStopped("cancelled")
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TargetStopped("time_budget")
        return remaining

    async def _turn(self):
        remaining = self._remaining()
        delay = max(0, self.next_request - time.monotonic())
        if delay >= remaining:
            raise TargetStopped("time_budget")
        if delay:
            await asyncio.sleep(delay)
        self._remaining()
        self.requests_sent += 1
        self.next_request = (
            time.monotonic() + 1 / self.scope.web.max_requests_per_second
        )

    def preflight(self, request: TargetRequest) -> int:
        self.admit(request)
        cost = 2 if request.protocol == "websocket" else 1
        self._remaining()
        if self.requests_sent + cost > self.scope.max_requests:
            raise TargetStopped("request_budget")
        return cost

    async def exchange(self, request: TargetRequest) -> TargetObservation:
        async with self._exchange_lock:
            return await self._exchange(request)

    async def _exchange(self, request: TargetRequest) -> TargetObservation:
        self.preflight(request)
        secret = None
        headers = {}
        if request.credential_ref is not None:
            slot = self.scope.credential_slots[request.credential_ref]
            try:
                with (self.credential_root / slot).open("r") as source:
                    secret = source.read(8193).strip()
            except OSError as exc:
                raise TargetStopped("credential_unavailable") from exc
            if not secret or len(secret) > 8192 or any(c in secret for c in "\r\n"):
                raise TargetStopped("credential_invalid")
            headers["Authorization"] = "Bearer " + secret

        async def refuse_redirect(*_):
            raise PermissionError("target redirects are not admitted")

        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(refuse_redirect)
        url = self.scope.web.origin + request.path
        await self._turn()
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=self._remaining()),
                trace_configs=[trace],
            ) as session:
                async with asyncio.timeout(self._remaining()):
                    if request.protocol == "http":
                        async with session.request(
                            request.method,
                            url,
                            json=(
                                request.payload
                                if request.method not in {"GET", "HEAD"}
                                else None
                            ),
                            headers=headers,
                            proxy=self.proxy,
                            allow_redirects=False,
                        ) as response:
                            if 300 <= response.status < 400:
                                raise PermissionError(
                                    "target redirects are not admitted"
                                )
                            status = response.status
                            try:
                                body = await response.content.readexactly(
                                    MAX_MESSAGE_BYTES + 1
                                )
                            except asyncio.IncompleteReadError as exc:
                                body = exc.partial
                    else:
                        url = ("wss" if url.startswith("https:") else "ws") + url[
                            url.index(":") :
                        ]
                        async with session.ws_connect(
                            url,
                            headers=headers,
                            proxy=self.proxy,
                            max_msg_size=MAX_MESSAGE_BYTES,
                        ) as socket:
                            await self._turn()
                            await socket.send_json(request.payload)
                            message = await socket.receive()
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise TargetStopped("invalid_target_response")
                            status, body = 101, message.data.encode()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise TargetStopped("transport_interrupted") from exc
        if len(body) > MAX_MESSAGE_BYTES or (secret and secret.encode() in body):
            raise TargetStopped("unsafe_target_response")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise TargetStopped("invalid_target_response") from exc
        if not isinstance(payload, dict):
            raise TargetStopped("invalid_target_response")
        digest = content_key(
            {
                "request": request.model_dump(mode="json", by_alias=True),
                "status": status,
                "responseDigest": hashlib.sha256(body).hexdigest(),
            }
        )
        return TargetObservation(
            request_id=request.id,
            protocol=request.protocol,
            status=status,
            payload=payload,
            evidence_ref="sha256:" + digest,
        )
