"""Declarative Omnigent Bridge configuration and compatibility contract.

MM-1151 (source: MM-1140): Declarative Omnigent Bridge configuration and
compatibility contract.

This module parses and validates the ``schemaVersion:
moonmind.omnigent_bridge.v1`` declarative configuration defined in
``docs/Omnigent/OmnigentBridge.md`` §6, with safe defaults and actionable
errors. It is the single canonical entrypoint (§18.2 component-to-module
ownership) through which downstream bridge components read the operator-declared
bridge boundary configuration.

The contract enforces the design principles that make the bridge safe by
construction:

* **Omnigent vocabulary at the boundary (§2.1).** Routes and profiles must use
  Omnigent-style nouns (``session``, ``event``, ``stream``, ``host``,
  ``runner``, ``resource``). A new external product vocabulary such as
  ``runtime bus``, ``agent socket``, or ``conversation broker`` is rejected.
* **Keep the host unchanged (§2.3).** ``compatibility.hostUnchanged`` must be
  ``true``. Only deployment configuration is accepted; any configuration that
  requires a custom host build is rejected.
* **Proxy-first compatibility (§2.4).** ``hostProtocolMode`` accepts
  ``upstream_omnigent_server_proxy`` and ``embedded_omnigent_compatible_server``
  and defaults to the proxy mode. Unknown modes fail fast.
* **MoonMind authority (§1).** The authority map (``temporal=moonmind``,
  ``artifacts=moonmind``, ``liveExecution=omnigent_host``) is validated and
  surfaced to downstream components.

Parsing is deterministic and side-effect-free so the configuration can be
validated at workflow/activity/adapter boundaries and fail fast with actionable
errors.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from moonmind.omnigent.settings import build_omnigent_gate

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION = "moonmind.omnigent_bridge.v1"

# Env var naming the operator-declared §6 bridge configuration document. When
# set, it is loaded before routes are mounted so the enabled flag, host
# protocol mode, and custom mount path/routes are honored (OB-§6, §21.1).
OMNIGENT_BRIDGE_CONFIG_PATH_ENV = "OMNIGENT_BRIDGE_CONFIG_PATH"

# §2.4 host protocol modes. Proxy is preferred (proxy-first default).
HOST_PROTOCOL_MODE_PROXY = "upstream_omnigent_server_proxy"
HOST_PROTOCOL_MODE_EMBEDDED = "embedded_omnigent_compatible_server"

HostProtocolMode = Literal[
    "upstream_omnigent_server_proxy",
    "embedded_omnigent_compatible_server",
]

# §9.3 first-message idempotency state machine, in canonical order.
CANONICAL_FIRST_MESSAGE_STATES: tuple[str, ...] = (
    "not_prepared",
    "prepared",
    "posting",
    "posted",
    "terminal",
)
FirstMessageState = Literal[
    "not_prepared",
    "prepared",
    "posting",
    "posted",
    "terminal",
]

WorkspaceDiffsCapture = bool | Literal["capability_probe"]

# §2.1 Omnigent nouns recognized in bridge routes. Route values must reference at
# least one of these so the boundary keeps Omnigent vocabulary.
_OMNIGENT_ROUTE_NOUNS: tuple[str, ...] = (
    "agents",
    "session",
    "sessions",
    "event",
    "events",
    "stream",
    "resource",
    "resources",
    "snapshot",
    "interrupt",
    "stop_session",
    "host",
    "runner",
    "elicitation",
    "elicitations",
)

# §2.1 banned external product vocabulary. Stored as (normalized, display) pairs
# so we can detect the concept regardless of separators or casing.
_BANNED_EXTERNAL_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("runtimebus", "runtime bus"),
    ("agentsocket", "agent socket"),
    ("conversationbroker", "conversation broker"),
)

# §2.3 markers that indicate a configuration key is requesting a custom host
# build rather than stock deployment configuration.
_HOST_BUILD_MARKERS: tuple[str, ...] = (
    "build",
    "dockerfile",
    "sourcepatch",
    "patch",
    "rebuild",
    "recompile",
    "compile",
    "fork",
)

_OMNIGENT_PROFILE_PREFIX = "omnigent."


class BridgeConfigError(ValueError):
    """Raised when the declarative Omnigent Bridge configuration is invalid.

    Used for fail-fast handling at parse boundaries with actionable messages.
    """


# ---------------------------------------------------------------------------
# Raw-input policy scans (run before structural validation so unknown keys and
# arbitrary string values are still subject to the vocabulary/host-build gates).
# ---------------------------------------------------------------------------


def _normalize_identifier(text: str) -> str:
    """Collapse a token to lowercase alphanumerics only.

    ``runtime-bus``, ``runtime_bus``, ``runtime bus`` and ``runtimeBus`` all
    normalize to ``runtimebus`` so vocabulary detection is separator/case
    insensitive.
    """

    return "".join(ch for ch in text.lower() if ch.isalnum())


def _iter_strings(data: Any, prefix: str = "") -> Iterator[tuple[str, bool, str]]:
    """Yield ``(path, is_key, text)`` for every mapping key and string value."""

    if isinstance(data, Mapping):
        for key, value in data.items():
            key_str = str(key)
            key_path = f"{prefix}.{key_str}" if prefix else key_str
            yield key_path, True, key_str
            yield from _iter_strings(value, key_path)
    elif isinstance(data, (list, tuple)):
        for index, item in enumerate(data):
            yield from _iter_strings(item, f"{prefix}[{index}]")
    elif isinstance(data, str):
        yield prefix, False, data


def _reject_external_vocabulary(data: Mapping[str, Any]) -> None:
    """Reject any key/value that introduces non-Omnigent product vocabulary."""

    for path, is_key, text in _iter_strings(data):
        normalized = _normalize_identifier(text)
        for banned_normalized, banned_display in _BANNED_EXTERNAL_VOCABULARY:
            if banned_normalized in normalized:
                where = "key" if is_key else "value"
                raise BridgeConfigError(
                    f"bridge config {where} at '{path}' uses non-Omnigent external "
                    f"vocabulary '{text}': the bridge boundary must use Omnigent "
                    f"nouns (session, event, stream, host, runner, resource) and "
                    f"must not introduce a new product vocabulary such as "
                    f"'{banned_display}' (§2.1)."
                )


def _is_host_build_key(normalized_key: str) -> bool:
    """Return True when a key requests a custom host build (§2.3)."""

    if "host" not in normalized_key:
        return False
    if "custom" in normalized_key:
        return True
    # "dispatch" contains the "patch" build marker; strip it first so common
    # dispatch-style keys (hostDispatch, hostDispatcher) are not mistaken for a
    # custom host build request.
    key_for_checking = normalized_key.replace("dispatch", "")
    return any(marker in key_for_checking for marker in _HOST_BUILD_MARKERS)


def _reject_host_build_configuration(data: Mapping[str, Any]) -> None:
    """Reject configuration keys that require modifying/building the host."""

    for path, is_key, text in _iter_strings(data):
        if not is_key:
            continue
        if _is_host_build_key(_normalize_identifier(text)):
            raise BridgeConfigError(
                f"bridge config key '{path}' requests a custom host build, which is "
                f"out of scope: the Omnigent Bridge must run against an unchanged, "
                f"stock Omnigent host (compatibility.hostUnchanged=true). Only "
                f"deployment configuration (server/base URL, host auth, endpoint "
                f"refs, network routing, standard Omnigent host settings) is "
                f"accepted (§2.3)."
            )


def _validate_omnigent_route(field_name: str, path: str) -> str:
    """Validate a single bridge route path is absolute and Omnigent-shaped."""

    candidate = str(path).strip()
    if not candidate:
        raise BridgeConfigError(f"publicApi.routes.{field_name} must not be empty")
    if not candidate.startswith("/"):
        raise BridgeConfigError(
            f"publicApi.routes.{field_name} '{candidate}' must be an absolute path "
            f"beginning with '/'."
        )
    lowered = candidate.lower()
    if not any(noun in lowered for noun in _OMNIGENT_ROUTE_NOUNS):
        raise BridgeConfigError(
            f"publicApi.routes.{field_name} '{candidate}' is not an Omnigent-shaped "
            f"route: bridge routes must use Omnigent nouns such as "
            f"agents/sessions/events/stream/resources (§2.1)."
        )
    return candidate


def _require_omnigent_profile(field_name: str, value: Any) -> str:
    candidate = str(value).strip()
    if not candidate:
        raise BridgeConfigError(f"{field_name} must not be empty")
    if not candidate.startswith(_OMNIGENT_PROFILE_PREFIX):
        raise BridgeConfigError(
            f"{field_name} '{candidate}' must be an Omnigent-namespaced profile "
            f"(e.g. 'omnigent.server.v1'); a non-Omnigent external product "
            f"vocabulary is not allowed at the bridge boundary (§2.1)."
        )
    return candidate


# ---------------------------------------------------------------------------
# Configuration blocks (§6)
# ---------------------------------------------------------------------------


class BridgeAuthority(BaseModel):
    """Authority map (§1): who owns each responsibility.

    MoonMind owns durable orchestration (Temporal) and artifact authority; the
    Omnigent host owns live runtime execution. These are fixed by the contract
    and surfaced to downstream components.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    temporal: Literal["moonmind"] = "moonmind"
    artifacts: Literal["moonmind"] = "moonmind"
    live_execution: Literal["omnigent_host"] = Field(
        "omnigent_host", alias="liveExecution"
    )


class BridgeCompatibility(BaseModel):
    """Compatibility block (§2.3, §2.4): host-unchanged + protocol mode."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile: str = "omnigent.server.v1"
    host_unchanged: bool = Field(True, alias="hostUnchanged")
    host_protocol_mode: HostProtocolMode = Field(
        HOST_PROTOCOL_MODE_PROXY, alias="hostProtocolMode"
    )

    @field_validator("profile")
    @classmethod
    def _profile_must_be_omnigent(cls, value: Any) -> str:
        return _require_omnigent_profile("compatibility.profile", value)

    @field_validator("host_unchanged")
    @classmethod
    def _host_must_be_unchanged(cls, value: bool) -> bool:
        if value is not True:
            raise BridgeConfigError(
                "compatibility.hostUnchanged must be true: the Omnigent Bridge must "
                "run against an unchanged, stock Omnigent host. A custom "
                "MoonMind-specific host build is out of scope (§2.3)."
            )
        return value


class BridgePublicApiRoutes(BaseModel):
    """Omnigent-shaped public API routes (§4.1, §6)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    agents: str = "/api/agents"
    hosts: str = "/api/hosts"
    create_session: str = Field("/v1/sessions", alias="createSession")
    get_session: str = Field("/v1/sessions/{session_id}", alias="getSession")
    attach_session: str = Field(
        "/v1/sessions/{session_id}/attach", alias="attachSession"
    )
    delete_session: str = Field("/v1/sessions/{session_id}", alias="deleteSession")
    post_event: str = Field("/v1/sessions/{session_id}/events", alias="postEvent")
    resolve_elicitation: str = Field(
        "/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve",
        alias="resolveElicitation",
    )
    stream_events: str = Field("/v1/sessions/{session_id}/stream", alias="streamEvents")
    changed_files: str = Field(
        "/v1/sessions/{session_id}/resources/environments/default/changes",
        alias="changedFiles",
    )
    workspace_files: str = Field(
        "/v1/sessions/{session_id}/resources/environments/default/filesystem",
        alias="workspaceFiles",
    )
    workspace_file: str = Field(
        "/v1/sessions/{session_id}/resources/environments/default/filesystem/{path:path}",
        alias="workspaceFile",
    )
    workspace_diffs: str = Field(
        "/v1/sessions/{session_id}/resources/environments/default/diff/{path:path}",
        alias="workspaceDiffs",
    )
    session_files: str = Field(
        "/v1/sessions/{session_id}/resources/files", alias="sessionFiles"
    )
    session_file: str = Field(
        "/v1/sessions/{session_id}/resources/files/{file_id}/content",
        alias="sessionFile",
    )

    @model_validator(mode="after")
    def _routes_are_omnigent_shaped(self) -> "BridgePublicApiRoutes":
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            setattr(self, field_name, _validate_omnigent_route(field_name, value))
        return self


class BridgePublicApi(BaseModel):
    """Public API surface (§6): mount path + Omnigent-compatible routes."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mount_path: str = Field("/api/omnigent", alias="mountPath")
    expose_omnigent_compatible_routes: bool = Field(
        True, alias="exposeOmnigentCompatibleRoutes"
    )
    routes: BridgePublicApiRoutes = Field(default_factory=BridgePublicApiRoutes)

    @field_validator("mount_path")
    @classmethod
    def _validate_mount_path(cls, value: Any) -> str:
        candidate = str(value).strip()
        if not candidate.startswith("/"):
            raise BridgeConfigError(
                "publicApi.mountPath must be an absolute path beginning with '/'."
            )
        return candidate


class BridgeEmbeddedHostConnection(BaseModel):
    """Embedded host-facing server settings (§6) for embedded mode."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    bind_address: str = Field("0.0.0.0", alias="bindAddress")
    port: int = Field(8000, ge=1, le=65535)
    auth_mode: str = Field("upstream_runner_tunnel", alias="authMode")
    protocol_profile: str = Field(
        "omnigent.runner_tunnel.3e88237c", alias="protocolProfile"
    )
    proxy_conformance_evidence_ref: str | None = Field(
        None, alias="proxyConformanceEvidenceRef"
    )
    live_smoke_evidence_ref: str | None = Field(None, alias="liveSmokeEvidenceRef")
    host_auth_conformance_evidence_ref: str | None = Field(
        None, alias="hostAuthConformanceEvidenceRef"
    )

    @model_validator(mode="after")
    def _auth_contract_is_supported(self) -> "BridgeEmbeddedHostConnection":
        if self.auth_mode != "upstream_runner_tunnel":
            raise BridgeConfigError(
                "hostConnection.embedded.authMode must be 'upstream_runner_tunnel'."
            )
        if self.protocol_profile != "omnigent.runner_tunnel.3e88237c":
            raise BridgeConfigError(
                "hostConnection.embedded.protocolProfile must be "
                "'omnigent.runner_tunnel.3e88237c'."
            )
        return self

    @field_validator("protocol_profile")
    @classmethod
    def _protocol_profile_must_be_omnigent(cls, value: Any) -> str:
        return _require_omnigent_profile(
            "hostConnection.embedded.protocolProfile", value
        )

    @field_validator(
        "proxy_conformance_evidence_ref",
        "live_smoke_evidence_ref",
        "host_auth_conformance_evidence_ref",
    )
    @classmethod
    def _evidence_ref_must_be_non_empty(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            raise BridgeConfigError(
                "embedded enablement evidence refs must not be empty"
            )
        return candidate


class BridgeHostConnection(BaseModel):
    """Host connection block (§6).

    ``mode`` defaults to (and must agree with) ``compatibility.hostProtocolMode``
    so there is a single source of truth for the active protocol mode.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: HostProtocolMode | None = None
    upstream_server_url_ref: str = Field("default", alias="upstreamServerUrlRef")
    embedded: BridgeEmbeddedHostConnection = Field(
        default_factory=BridgeEmbeddedHostConnection
    )


class BridgeCaptureDefaults(BaseModel):
    """Per-session capture defaults (§6)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    stream: bool = True
    snapshots: bool = True
    changed_files: bool = Field(True, alias="changedFiles")
    workspace_files: bool = Field(True, alias="workspaceFiles")
    workspace_diffs: WorkspaceDiffsCapture = Field(
        "capability_probe", alias="workspaceDiffs"
    )
    session_files: bool = Field(True, alias="sessionFiles")
    child_sessions: bool = Field(True, alias="childSessions")


class BridgeSessionDefaults(BaseModel):
    """Session defaults (§6)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host_type: Literal["managed", "external"] = Field("managed", alias="hostType")
    delete_provider_session_after_harvest: bool = Field(
        False, alias="deleteProviderSessionAfterHarvest"
    )
    capture: BridgeCaptureDefaults = Field(default_factory=BridgeCaptureDefaults)


class BridgeIdempotency(BaseModel):
    """First-message idempotency block (§6, §9.3)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    first_message_state_machine: tuple[FirstMessageState, ...] = Field(
        default=CANONICAL_FIRST_MESSAGE_STATES,
        alias="firstMessageStateMachine",
    )
    include_idempotency_marker: bool = Field(True, alias="includeIdempotencyMarker")
    reconcile_posting_state: bool = Field(True, alias="reconcilePostingState")

    @field_validator("first_message_state_machine")
    @classmethod
    def _canonical_order(
        cls, value: tuple[FirstMessageState, ...]
    ) -> tuple[FirstMessageState, ...]:
        if tuple(value) != CANONICAL_FIRST_MESSAGE_STATES:
            raise BridgeConfigError(
                "idempotency.firstMessageStateMachine must be the canonical order: "
                "not_prepared -> prepared -> posting -> posted -> terminal (§9.3)."
            )
        return tuple(value)


class BridgeObservability(BaseModel):
    """Observability block (§6)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    write_raw_event_journal: bool = Field(True, alias="writeRawEventJournal")
    write_normalized_event_journal: bool = Field(
        True, alias="writeNormalizedEventJournal"
    )
    feed_workflow_chat: bool = Field(True, alias="feedWorkflowChat")
    feed_agent_run_observability: bool = Field(True, alias="feedAgentRunObservability")
    fallback_to_legacy_managed_run_logs: bool = Field(
        True, alias="fallbackToLegacyManagedRunLogs"
    )


class OmnigentBridgeConfig(BaseModel):
    """Parsed ``moonmind.omnigent_bridge.v1`` declarative bridge configuration.

    Built with safe defaults so a minimal document (or an empty one) yields a
    valid proxy-first bridge configuration, while unsupported values fail fast.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["moonmind.omnigent_bridge.v1"] = Field(
        OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION, alias="schemaVersion"
    )
    enabled: bool = True
    authority: BridgeAuthority = Field(default_factory=BridgeAuthority)
    compatibility: BridgeCompatibility = Field(default_factory=BridgeCompatibility)
    public_api: BridgePublicApi = Field(
        default_factory=BridgePublicApi, alias="publicApi"
    )
    host_connection: BridgeHostConnection = Field(
        default_factory=BridgeHostConnection, alias="hostConnection"
    )
    session_defaults: BridgeSessionDefaults = Field(
        default_factory=BridgeSessionDefaults, alias="sessionDefaults"
    )
    idempotency: BridgeIdempotency = Field(default_factory=BridgeIdempotency)
    observability: BridgeObservability = Field(default_factory=BridgeObservability)

    @model_validator(mode="after")
    def _resolve_host_protocol_mode(self) -> "OmnigentBridgeConfig":
        compat_mode = self.compatibility.host_protocol_mode
        if self.host_connection.mode is None:
            self.host_connection.mode = compat_mode
        elif self.host_connection.mode != compat_mode:
            raise BridgeConfigError(
                f"hostConnection.mode '{self.host_connection.mode}' must match "
                f"compatibility.hostProtocolMode '{compat_mode}': the bridge has a "
                f"single active host protocol mode (§2.4)."
            )
        if self.enabled and compat_mode == HOST_PROTOCOL_MODE_EMBEDDED:
            embedded = self.host_connection.embedded
            missing = [
                field_name
                for field_name, value in (
                    (
                        "hostConnection.embedded.proxyConformanceEvidenceRef",
                        embedded.proxy_conformance_evidence_ref,
                    ),
                    (
                        "hostConnection.embedded.liveSmokeEvidenceRef",
                        embedded.live_smoke_evidence_ref,
                    ),
                    (
                        "hostConnection.embedded.hostAuthConformanceEvidenceRef",
                        embedded.host_auth_conformance_evidence_ref,
                    ),
                )
                if not value
            ]
            if missing:
                raise BridgeConfigError(
                    "embedded_omnigent_compatible_server mode cannot be enabled "
                    "without proxy conformance, live smoke-test, and upstream host "
                    "auth conformance evidence refs (§2.4, §16 rule 8). Missing: "
                    + ", ".join(missing)
                )
        return self

    @property
    def host_protocol_mode(self) -> HostProtocolMode:
        """Resolved active host protocol mode."""

        return self.compatibility.host_protocol_mode

    def authority_map(self) -> dict[str, str]:
        """Return the authority map for surfacing to downstream components."""

        return {
            "temporal": self.authority.temporal,
            "artifacts": self.authority.artifacts,
            "liveExecution": self.authority.live_execution,
        }

    def evidence_policy_sha256(self) -> str:
        """Bind evidence to execution-relevant config without self-referential refs."""

        payload = self.model_dump(mode="json", by_alias=True)
        embedded = payload["hostConnection"]["embedded"]
        for key in (
            "proxyConformanceEvidenceRef",
            "liveSmokeEvidenceRef",
            "hostAuthConformanceEvidenceRef",
        ):
            embedded[key] = None
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def readiness(
        self, *, evidence_validation: Mapping[str, Mapping[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Return non-secret, operator-visible mode/conformance readiness."""

        embedded = self.host_connection.embedded
        evidence = {
            "proxyConformance": embedded.proxy_conformance_evidence_ref,
            "liveSmoke": embedded.live_smoke_evidence_ref,
            "hostAuthConformance": embedded.host_auth_conformance_evidence_ref,
        }
        selected_embedded = self.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        proxy_ready = build_omnigent_gate().enabled
        validation = dict(evidence_validation or {})
        evidence_ready = bool(validation) and all(
            validation.get(key, {}).get("status") == "passed" for key in evidence
        )
        result = {
            "enabled": self.enabled,
            "selectedMode": self.host_protocol_mode,
            "protocolProfile": (
                embedded.protocol_profile
                if selected_embedded
                else self.compatibility.profile
            ),
            "upstreamComponentVersion": (
                embedded.protocol_profile.rsplit(".", 1)[-1]
                if selected_embedded
                else None
            ),
            "conformanceState": (
                "ready"
                if self.enabled
                and (
                    evidence_ready if selected_embedded else proxy_ready
                )
                else "disabled" if not self.enabled else "gated"
            ),
            "evidenceRefs": evidence if selected_embedded else {},
        }
        if selected_embedded:
            result["evidenceValidation"] = validation
            if not evidence_ready:
                result["gateReason"] = "validated_embedded_evidence_required"
        return result


# ---------------------------------------------------------------------------
# Parse / load entrypoints
# ---------------------------------------------------------------------------


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(piece) for piece in err.get("loc", ()))
        message = err.get("msg", "invalid value")
        # Pydantic prefixes custom validator messages with "Value error, "; drop
        # that so the actionable message reads cleanly.
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        parts.append(f"{loc}: {message}" if loc else message)
    return "invalid Omnigent bridge configuration: " + "; ".join(parts)


def parse_bridge_config(data: Mapping[str, Any]) -> OmnigentBridgeConfig:
    """Parse and validate a §6 declarative bridge configuration mapping.

    Applies the Omnigent-vocabulary and host-build policy gates to the raw input
    (so unknown keys are still covered) before structural validation, then
    returns a fully-defaulted :class:`OmnigentBridgeConfig`. Raises
    :class:`BridgeConfigError` with an actionable message on any invalid input.
    """

    if not isinstance(data, Mapping):
        raise BridgeConfigError(
            f"bridge config must be a mapping/object, got {type(data).__name__}"
        )

    _reject_external_vocabulary(data)
    _reject_host_build_configuration(data)

    try:
        return OmnigentBridgeConfig.model_validate(dict(data))
    except BridgeConfigError:
        raise
    except ValidationError as exc:
        raise BridgeConfigError(_format_validation_error(exc)) from exc


def load_bridge_config(text: str) -> OmnigentBridgeConfig:
    """Load a §6 declarative bridge configuration from YAML or JSON text."""

    if not isinstance(text, str):
        raise BridgeConfigError("bridge config text must be a string")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BridgeConfigError(f"bridge config is not valid YAML/JSON: {exc}") from exc
    if loaded is None:
        raise BridgeConfigError("bridge config document is empty")
    if not isinstance(loaded, Mapping):
        raise BridgeConfigError(
            "bridge config document must be a mapping/object at the top level"
        )
    return parse_bridge_config(loaded)


def resolve_bridge_config(
    *, env: Mapping[str, str] | None = None
) -> OmnigentBridgeConfig:
    """Resolve the operator-declared bridge configuration (OB-§6, §21.1).

    Loads the §6 declarative document referenced by
    ``OMNIGENT_BRIDGE_CONFIG_PATH`` so operator-declared values (the enabled
    flag, host protocol mode, and custom ``publicApi`` mount path/routes) are
    honored before the router registers routes. Falls back to safe defaults
    when the env var is unset, and fails fast with an actionable
    :class:`BridgeConfigError` when the path is unreadable or the document is
    invalid rather than silently mounting the default proxy surface.
    """

    source = env if env is not None else os.environ
    raw_path = str(source.get(OMNIGENT_BRIDGE_CONFIG_PATH_ENV) or "").strip()
    if not raw_path:
        return OmnigentBridgeConfig()
    try:
        text = Path(raw_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise BridgeConfigError(
            f"Omnigent bridge config path {raw_path!r} "
            f"({OMNIGENT_BRIDGE_CONFIG_PATH_ENV}) could not be read: {exc}"
        ) from exc
    return load_bridge_config(text)


__all__ = [
    "CANONICAL_FIRST_MESSAGE_STATES",
    "HOST_PROTOCOL_MODE_EMBEDDED",
    "HOST_PROTOCOL_MODE_PROXY",
    "OMNIGENT_BRIDGE_CONFIG_PATH_ENV",
    "OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION",
    "BridgeAuthority",
    "BridgeCaptureDefaults",
    "BridgeCompatibility",
    "BridgeConfigError",
    "BridgeEmbeddedHostConnection",
    "BridgeHostConnection",
    "BridgeIdempotency",
    "BridgeObservability",
    "BridgePublicApi",
    "BridgePublicApiRoutes",
    "BridgeSessionDefaults",
    "FirstMessageState",
    "HostProtocolMode",
    "OmnigentBridgeConfig",
    "WorkspaceDiffsCapture",
    "load_bridge_config",
    "parse_bridge_config",
    "resolve_bridge_config",
]
