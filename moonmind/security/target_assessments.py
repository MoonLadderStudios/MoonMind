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
from urllib.parse import unquote, urlencode

import aiohttp
from pydantic import Field, field_validator, model_serializer, model_validator

from moonmind.schemas.repository_scanner_models import (
    HttpMethod,
    WebAssessment,
    web_route,
)
from moonmind.security.assessments import Contract, Text, content_key
from moonmind.security.authorization_matrix import IdentityRef
from moonmind.security.realtime_scope import (
    RealtimeScope,
    TransportBudget,
    TransportEvent,
)
from moonmind.security.web_assessments import path_admitted
from moonmind.security.workload_profiles import WORKLOAD_CREDENTIALS_TARGET

MAX_MESSAGE_BYTES = 256 * 1024


class TargetScope(Contract):
    web: WebAssessment
    realtime: RealtimeScope | None = None

    @model_serializer(mode="wrap")
    def _legacy_shape(self, handler):
        value = handler(self)
        if self.realtime is None:
            value.pop("realtime", None)
        return value

    messages: dict[str, list[IdentityRef]] = Field(default_factory=dict, max_length=32)
    credential_slots: dict[Text, IdentityRef] = Field(
        default_factory=dict, max_length=8
    )
    max_requests: int = Field(4096, strict=True, ge=1, le=8192)

    @model_validator(mode="after")
    def _consistent(self):
        if self.web.identity is not None:
            raise ValueError("target probes select their own admitted identity slot")
        if self.realtime is not None:
            for case in self.realtime.cases:
                if not path_admitted(self.web, case.path, "GET") or (
                    case.negotiation_path
                    and not path_admitted(self.web, case.negotiation_path, "POST")
                ):
                    raise ValueError("real-time endpoints require admitted routes")
                if (
                    case.credential_ref is not None
                    and case.credential_ref not in self.credential_slots
                ):
                    raise ValueError("real-time case credential is not admitted")
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
    protocol: Literal["http", "websocket", "signalr-json"] = "http"
    path: str
    method: HttpMethod = "POST"
    message_type: IdentityRef | None = None
    credential_ref: Text | None = None
    payload: dict = Field(default_factory=dict)
    realtime_case_ref: IdentityRef | None = None
    effect: Literal["read_only", "mutation"] | None = None

    @model_serializer(mode="wrap")
    def _legacy_shape(self, handler):
        value = handler(self)
        for name, alias in (
            ("realtime_case_ref", "realtimeCaseRef"),
            ("effect", "effect"),
        ):
            if getattr(self, name) is None:
                value.pop(name, None)
                value.pop(alias, None)
        return value

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
        elif self.protocol == "signalr-json":
            if (
                self.method != "GET"
                or not self.message_type
                or not self.realtime_case_ref
            ):
                raise ValueError("SignalR requests require an exact named case")
        elif self.message_type is not None:
            raise ValueError("only real-time requests declare a message type")
        return self


class TargetObservation(Contract):
    request_id: IdentityRef
    protocol: Literal["http", "websocket", "signalr-json"]
    status: int = Field(ge=100, le=599)
    payload: dict
    evidence_ref: Text
    transcript: list[TransportEvent] | None = Field(None, max_length=259)

    @model_serializer(mode="wrap")
    def _legacy_shape(self, handler):
        value = handler(self)
        if self.transcript is None:
            value.pop("transcript", None)
        return value


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
        # Named real-time authority is a snapshot, not a live caller-owned
        # object. Legacy callers may still adjust their HTTP/WS rate controls.
        if scope.realtime is not None:
            scope = TargetScope.model_validate(scope.model_dump(mode="json"))
        self.scope, self.credential_root, self.proxy = scope, credential_root, proxy
        self._admitted_realtime_scope = (
            content_key(scope.model_dump(mode="json"))
            if scope.realtime is not None
            else None
        )
        self.should_stop = should_stop
        self.max_seconds = min(
            300 if max_seconds is None else max_seconds, scope.web.timeout_seconds
        )
        self.started = time.monotonic()
        self.next_request = self.started
        self.requests_sent = 0
        self._exchange_lock = asyncio.Lock()
        self._transport_budget = TransportBudget()
        self._transcript: list[TransportEvent] = []
        self._active_request = None

    def admit(self, request: TargetRequest) -> None:
        """Validate before any credential read, handshake or request is sent."""
        if self._admitted_realtime_scope is not None:
            if (
                content_key(self.scope.model_dump(mode="json"))
                != self._admitted_realtime_scope
            ):
                raise PermissionError("target real-time scope changed after admission")
        elif self.scope.realtime is not None:
            raise PermissionError("target real-time scope changed after admission")
        if (
            self._active_request is not None
            and json.dumps(
                request.model_dump(mode="json"), sort_keys=True, allow_nan=False
            )
            != self._active_request
        ):
            raise PermissionError("target request changed during exchange")
        # Revalidate mutable caller-owned objects at every outbound boundary.
        request = TargetRequest.model_validate(request.model_dump())
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
        if request.realtime_case_ref is not None:
            cases = self.scope.realtime.cases if self.scope.realtime else []
            case = next(
                (c for c in cases if c.case_ref == request.realtime_case_ref), None
            )
            if (
                case is None
                or any(
                    getattr(case, name) != getattr(request, name)
                    for name in (
                        "protocol",
                        "path",
                        "credential_ref",
                        "message_type",
                        "effect",
                    )
                )
                or json.dumps(case.payload, sort_keys=True, allow_nan=False)
                != json.dumps(request.payload, sort_keys=True, allow_nan=False)
            ):
                raise PermissionError("target real-time case is not admitted")
            wire = (
                request.payload
                if request.protocol == "websocket"
                else {
                    "type": 1,
                    "invocationId": request.id,
                    "target": request.message_type,
                    "arguments": request.payload["arguments"],
                }
            )
            if (
                len(json.dumps(wire, allow_nan=False).encode())
                + (request.protocol == "signalr-json")
                > self.scope.realtime.max_message_bytes
            ):
                raise PermissionError("target real-time message exceeds admitted bound")
        elif request.protocol == "signalr-json" or (
            request.protocol == "websocket" and self.scope.realtime is not None
        ):
            raise PermissionError("target real-time requests require a named case")
        if (
            request.realtime_case_ref is None
            and request.protocol == "websocket"
            and request.message_type not in self.scope.messages.get(path, [])
        ):
            raise PermissionError("target message type is not admitted")
        if self.scope.web.mode != "active" and (
            request.method not in {"GET", "HEAD"} or request.protocol != "http"
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
            await self._await(asyncio.sleep(delay))
        self._remaining()
        self.requests_sent += 1
        self.next_request = (
            time.monotonic() + 1 / self.scope.web.max_requests_per_second
        )

    def preflight(self, request: TargetRequest) -> int:
        self.admit(request)
        cost = {"http": 1, "websocket": 2, "signalr-json": 4}[request.protocol]
        self.reserve_transport(request)
        self._remaining()
        if self.requests_sent + cost > self.scope.max_requests:
            raise TargetStopped("request_budget")
        return cost

    async def exchange(self, request: TargetRequest) -> TargetObservation:
        async with self._exchange_lock:
            self._transcript = []
            self._active_request = json.dumps(
                request.model_dump(mode="json"), sort_keys=True, allow_nan=False
            )
            try:
                return await self._exchange(request)
            finally:
                self._active_request = None

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

        # ws_connect follows redirects internally. The public trace callback
        # rejects the redirect before aiohttp contacts its destination.
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(refuse_redirect)
        url = self.scope.web.origin + request.path
        await self._turn()
        self.admit(request)
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
                    elif request.realtime_case_ref is not None:
                        status, body = await self._realtime(session, request, headers)
                    else:
                        url = ("wss" if url.startswith("https:") else "ws") + url[
                            url.index(":") :
                        ]
                        async with session.ws_connect(
                            url,
                            headers=headers,
                            proxy=self.proxy,
                            max_msg_size=MAX_MESSAGE_BYTES,
                            autoping=False,
                            heartbeat=None,
                        ) as socket:
                            await self._turn()
                            self.admit(request)
                            await self._await(socket.send_json(request.payload))
                            message = await self._await(socket.receive())
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise TargetStopped("invalid_target_response")
                            status, body = 101, message.data.encode()
        except aiohttp.WSServerHandshakeError as exc:
            if exc.status not in {401, 403}:
                raise TargetStopped("transport_interrupted") from (
                    None if request.realtime_case_ref else exc
                )
            # The upgrade was denied before any message could be sent. Do not
            # retain the server's exception text, headers or credentials.
            status = exc.status
            # A gateway refusal supplies no application-policy response. Keep
            # the historical synthetic denial only for legacy RGS requests.
            body = (
                b"{}"
                if request.realtime_case_ref is not None
                else b'{"decision":"denied","access":"deny"}'
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise TargetStopped("transport_interrupted") from (
                None if request.realtime_case_ref else exc
            )
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
            transcript=self.transport_transcript if request.realtime_case_ref else None,
        )

    @property
    def transport_transcript(self) -> list[TransportEvent]:
        """Sanitized events from the current/last exchange, including failures."""
        return [event.model_copy(deep=True) for event in self._transcript]

    @property
    def transport_budget(self) -> TransportBudget:
        return self._transport_budget.model_copy(deep=True)

    def restore_transport(self, budget: TransportBudget | dict) -> None:
        self._transport_budget = TransportBudget.model_validate(budget)

    def reserve_transport(self, request: TargetRequest) -> TransportBudget:
        """Return a conservative cumulative checkpoint without charging twice.

        Counts protocol body bytes, not HTTP headers or WebSocket framing.
        A failed/uncertain exchange must retain this reservation at the caller.
        """
        budget = self.transport_budget
        if request.protocol == "http" or request.realtime_case_ref is None:
            return budget
        limits = self.scope.realtime
        if limits is None:
            raise PermissionError("target real-time scope is missing")
        outgoing = 3 if request.protocol == "signalr-json" else 1
        messages = outgoing + limits.max_receive_messages
        budget.connections += 1
        budget.messages += messages
        budget.bytes += messages * limits.max_message_bytes
        for name in ("connections", "messages", "bytes"):
            if getattr(budget, name) > getattr(limits, "max_" + name):
                raise TargetStopped("transport_" + name + "_budget")
        return budget

    async def _await(self, operation):
        """Poll cancellation even while the peer stops producing data."""
        task = asyncio.ensure_future(operation)
        try:
            while not task.done():
                remaining = self._remaining()
                await asyncio.wait({task}, timeout=min(0.05, remaining))
            self._remaining()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _record(self, request, stage, direction, body):
        limits = self.scope.realtime
        if len(body) > limits.max_message_bytes:
            raise TargetStopped("transport_message_bytes_budget")
        self._transport_budget.messages += 1
        self._transport_budget.bytes += len(body)
        if (
            self._transport_budget.messages > limits.max_messages
            or self._transport_budget.bytes > limits.max_bytes
        ):
            raise TargetStopped("transport_budget")
        self._transcript.append(
            TransportEvent(
                request_id=request.id,
                case_ref=request.realtime_case_ref,
                correlation_id=request.id,
                stage=stage,
                direction=direction,
                bytes=len(body),
            )
        )

    async def _realtime(self, session, request, headers):
        # aiohttp may otherwise retry an idempotent upgrade on a stale
        # negotiation connection; every attempt belongs to this reservation.
        session._retry_connection = False
        limits = self.scope.realtime
        case = next(c for c in limits.cases if c.case_ref == request.realtime_case_ref)
        url = self.scope.web.origin + request.path
        received = 0
        token = None

        async def receive_body(response):
            nonlocal received
            try:
                body = await self._await(
                    response.content.readexactly(limits.max_message_bytes + 1)
                )
            except asyncio.IncompleteReadError as exc:
                body = exc.partial
            received += 1
            if received > limits.max_receive_messages:
                raise TargetStopped("transport_receive_budget")
            self._record(request, "negotiation", "receive", body)
            return body

        if request.protocol == "signalr-json":
            self.admit(request)
            self._record(request, "negotiation", "send", b"")
            response = await self._await(
                session.post(
                    self.scope.web.origin + case.negotiation_path,
                    params={"negotiateVersion": "1"},
                    headers=headers,
                    proxy=self.proxy,
                    allow_redirects=False,
                )
            )
            async with response:
                if 300 <= response.status < 400:
                    raise PermissionError("target redirects are not admitted")
                body = await receive_body(response)
                if response.status in {401, 403}:
                    # Negotiation refusal is transport evidence, not an
                    # assertion that the requested application action was denied.
                    return response.status, b"{}"
                if response.status != 200:
                    raise TargetStopped("unsupported_signalr_negotiation")
                try:
                    negotiation = json.loads(body)
                except (ValueError, UnicodeError):
                    raise TargetStopped("unsupported_signalr_negotiation") from None
                if not isinstance(negotiation, dict) or any(
                    key in negotiation for key in ("url", "accessToken", "error")
                ):
                    raise TargetStopped("unsupported_signalr_negotiation")
                transports = negotiation.get("availableTransports", [])
                if not isinstance(transports, list) or not any(
                    isinstance(t, dict)
                    and t.get("transport") == "WebSockets"
                    and isinstance(t.get("transferFormats"), list)
                    and "Text" in t["transferFormats"]
                    for t in transports
                ):
                    raise TargetStopped("unsupported_signalr_transport")
                token = negotiation.get("connectionToken")
                if (
                    negotiation.get("negotiateVersion") != 1
                    or not isinstance(token, str)
                    or not token
                    or len(token) > 4096
                ):
                    raise TargetStopped("unsupported_signalr_negotiation")
            await self._turn()
            url += "?" + urlencode({"id": token})
        self.admit(request)
        self._transport_budget.connections += 1
        url = ("wss" if url.startswith("https:") else "ws") + url[url.index(":") :]
        socket = await self._await(
            session.ws_connect(
                url,
                headers=headers,
                proxy=self.proxy,
                max_msg_size=limits.max_message_bytes,
                autoping=False,
                heartbeat=None,
                autoclose=False,
            )
        )
        pending = ""
        records = []
        parsed = 0

        async def receive(stage):
            nonlocal received
            message = await self._await(socket.receive())
            received += 1
            if received > limits.max_receive_messages:
                raise TargetStopped("transport_receive_budget")
            if message.type != aiohttp.WSMsgType.TEXT:
                raise TargetStopped("unsupported_realtime_frame")
            data = message.data.encode()
            self._record(request, stage, "receive", data)
            return message.data

        async def record(stage):
            nonlocal pending, parsed
            while not records:
                pending += await receive(stage)
                if len(pending.encode()) > limits.max_message_bytes:
                    raise TargetStopped("transport_message_bytes_budget")
                parts = pending.split("\x1e")
                pending = parts.pop()
                records.extend(parts)
            parsed += 1
            if parsed > limits.max_receive_messages:
                raise TargetStopped("transport_receive_budget")
            try:
                value = json.loads(records.pop(0))
            except (ValueError, UnicodeError):
                raise TargetStopped("unsupported_signalr_record") from None
            if not isinstance(value, dict):
                raise TargetStopped("unsupported_signalr_record")
            return value

        async def send(stage, payload):
            self.admit(request)
            body = json.dumps(payload, allow_nan=False)
            if request.protocol == "signalr-json":
                body += "\x1e"
            self._record(request, stage, "send", body.encode())
            await self._await(socket.send_str(body))

        try:
            if request.protocol == "signalr-json":
                await self._turn()
                await send("handshake", {"protocol": "json", "version": 1})
                if await record("handshake") != {} or pending or records:
                    raise TargetStopped("unsupported_signalr_handshake")
            await self._turn()
            # No await between revalidation, encoding and dispatch.
            if request.protocol == "websocket":
                await send("application", request.payload)
                return 101, (await receive("application")).encode()
            await send(
                "application",
                {
                    "type": 1,
                    "invocationId": request.id,
                    "target": request.message_type,
                    "arguments": request.payload["arguments"],
                },
            )
            completion = await record("completion")
            if (
                set(completion) - {"type", "invocationId", "result", "error"}
                or ("result" in completion and "error" in completion)
                or completion.get("type") != 3
                or completion.get("invocationId") != request.id
                or pending
                or records
            ):
                raise TargetStopped("unsupported_signalr_completion")
            if "error" in completion and not isinstance(completion["error"], str):
                raise TargetStopped("unsupported_signalr_completion_error")
            result = (
                {"error": completion["error"]}
                if "error" in completion
                else completion.get("result")
            )
            if not isinstance(result, dict):
                raise TargetStopped("unsupported_signalr_result")
            body = json.dumps(result).encode()
            if token and token.encode() in body:
                raise TargetStopped("unsafe_target_response")
            return 101, body
        finally:
            # Closing the underlying response is immediate; no peer-controlled
            # close handshake can extend cancellation or the assessment window.
            socket._response.close()
