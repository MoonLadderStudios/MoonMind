"""Retired embedded host-route request payloads (#3955).

MoonLadderStudios/MoonMind#3955 retires the experimental embedded host/runner
transport admission while keeping historical evidence readable. These three
Pydantic payloads are the API contract for the retired host routes
(register/heartbeat/event-ingest): they must stay importable from the proxy-
only production path (API router, OpenAPI schema) without requiring the
retired embedded launch modules (``bridge_embedded``,
``embedded_host_channel``, ``embedded_evidence``).

``bridge_embedded`` re-exports these names so existing facade-internal and
test imports keep working; the canonical definition lives here.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_EMBEDDED_CAPABILITIES = 128
MAX_EMBEDDED_CAPABILITY_BYTES = 64 * 1024
MAX_EMBEDDED_EVENT_ENTRIES = 1024
MAX_EMBEDDED_EVENT_BYTES = 1024 * 1024


def _bounded_mapping(
    value: dict[str, Any], *, label: str, max_entries: int, max_bytes: int
) -> dict[str, Any]:
    if len(value) > max_entries:
        raise ValueError(f"{label} exceeds the {max_entries}-entry limit")
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte limit")
    return value


class EmbeddedHostRegisterRequest(BaseModel):
    """Host registration payload accepted from an unchanged Omnigent host."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    host_id: str | None = Field(None, alias="hostId")
    runner_id: str | None = Field(None, alias="runnerId")
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host capabilities",
            max_entries=MAX_EMBEDDED_CAPABILITIES,
            max_bytes=MAX_EMBEDDED_CAPABILITY_BYTES,
        )


class EmbeddedHostHeartbeatRequest(BaseModel):
    """Host heartbeat payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host capabilities",
            max_entries=MAX_EMBEDDED_CAPABILITIES,
            max_bytes=MAX_EMBEDDED_CAPABILITY_BYTES,
        )


class EmbeddedHostSessionEventRequest(BaseModel):
    """Host-to-MoonMind session event payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: str = Field(..., min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_mapping(
            value,
            label="Host event data",
            max_entries=MAX_EMBEDDED_EVENT_ENTRIES,
            max_bytes=MAX_EMBEDDED_EVENT_BYTES,
        )


__all__ = [
    "MAX_EMBEDDED_CAPABILITIES",
    "MAX_EMBEDDED_CAPABILITY_BYTES",
    "MAX_EMBEDDED_EVENT_BYTES",
    "MAX_EMBEDDED_EVENT_ENTRIES",
    "EmbeddedHostHeartbeatRequest",
    "EmbeddedHostRegisterRequest",
    "EmbeddedHostSessionEventRequest",
]
