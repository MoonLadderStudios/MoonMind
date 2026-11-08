"""Pure operator contracts for bounded, exact real-time application cases."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import unquote

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from moonmind.schemas.repository_scanner_models import web_route

Ref = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")]


class RealtimeContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel
    )


class RealtimeCase(RealtimeContract):
    case_ref: Ref
    protocol: Literal["websocket", "signalr-json"]
    path: str
    credential_ref: str | None = Field(None, max_length=512)
    anonymous: bool = False
    message_type: Ref
    payload: dict
    effect: Literal["read_only", "mutation"]
    negotiation_path: str | None = None

    @field_validator("path", "negotiation_path")
    @classmethod
    def _route(cls, value):
        if value is None:
            return value
        if unquote(value) != value:
            raise ValueError("encoded real-time routes are not admitted")
        return web_route(value)

    @model_validator(mode="after")
    def _shape(self):
        if self.anonymous != (self.credential_ref is None):
            raise ValueError("only explicitly anonymous cases omit credentials")
        if self.protocol == "signalr-json":
            if (
                not self.negotiation_path
                or set(self.payload) != {"arguments"}
                or not isinstance(self.payload["arguments"], list)
            ):
                raise ValueError(
                    "SignalR cases require negotiation and exact arguments"
                )
        elif (
            self.negotiation_path is not None
            or self.payload.get("type") != self.message_type
        ):
            raise ValueError("WebSocket cases declare their exact message type")
        json.dumps(self.payload, allow_nan=False)
        return self


class RealtimeScope(RealtimeContract):
    cases: list[RealtimeCase] = Field(min_length=1, max_length=512)
    max_connections: int = Field(32, strict=True, ge=1, le=8192)
    max_messages: int = Field(256, strict=True, ge=1, le=65536)
    max_bytes: int = Field(4 * 1024 * 1024, strict=True, ge=1, le=64 * 1024 * 1024)
    max_message_bytes: int = Field(65536, strict=True, ge=128, le=256 * 1024)
    max_receive_messages: int = Field(8, strict=True, ge=1, le=256)

    @model_validator(mode="after")
    def _unique(self):
        if len({case.case_ref for case in self.cases}) != len(self.cases):
            raise ValueError("real-time case references must be unique")
        if any(
            len(json.dumps(case.payload).encode()) > self.max_message_bytes
            for case in self.cases
        ):
            raise ValueError("real-time payload exceeds per-message bound")
        return self


class TransportBudget(RealtimeContract):
    connections: int = Field(0, strict=True, ge=0)
    messages: int = Field(0, strict=True, ge=0)
    bytes: int = Field(0, strict=True, ge=0)


class TransportEvent(RealtimeContract):
    """Fixed-category transport metadata; no target content or credentials."""

    request_id: Ref
    case_ref: Ref
    correlation_id: Ref
    stage: Literal["negotiation", "handshake", "application", "completion"]
    direction: Literal["send", "receive"]
    bytes: int = Field(strict=True, ge=0, le=256 * 1024)
