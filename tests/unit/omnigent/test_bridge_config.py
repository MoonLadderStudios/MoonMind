"""Unit tests for the declarative Omnigent Bridge configuration (MM-1151).

MM-1151 (source: MM-1140): validate the ``moonmind.omnigent_bridge.v1``
declarative configuration and compatibility contract defined in
``docs/Omnigent/OmnigentBridge.md`` §6.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_config import (
    CANONICAL_FIRST_MESSAGE_STATES,
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    OMNIGENT_BRIDGE_CONFIG_PATH_ENV,
    OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION,
    BridgeConfigError,
    OmnigentBridgeConfig,
    load_bridge_config,
    parse_bridge_config,
    resolve_bridge_config,
)

# The exact §6 declarative document from docs/Omnigent/OmnigentBridge.md.
SECTION_6_DOCUMENT = """
schemaVersion: moonmind.omnigent_bridge.v1

enabled: true

authority:
  temporal: moonmind
  artifacts: moonmind
  liveExecution: omnigent_host

compatibility:
  profile: omnigent.server.v1
  hostUnchanged: true
  hostProtocolMode: upstream_omnigent_server_proxy

publicApi:
  mountPath: /api/omnigent
  exposeOmnigentCompatibleRoutes: true
  routes:
    agents: /api/agents
    createSession: /v1/sessions
    getSession: /v1/sessions/{session_id}
    postEvent: /v1/sessions/{session_id}/events
    resolveElicitation: /v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve
    streamEvents: /v1/sessions/{session_id}/stream
    changedFiles: /v1/sessions/{session_id}/resources/environments/default/changes
    workspaceFiles: /v1/sessions/{session_id}/resources/environments/default/filesystem
    workspaceDiffs: /v1/sessions/{session_id}/resources/environments/default/diff/{path}
    sessionFiles: /v1/sessions/{session_id}/resources/files

hostConnection:
  mode: upstream_omnigent_server_proxy
  upstreamServerUrlRef: default
  embedded:
    bindAddress: 0.0.0.0
    port: 8000
    authMode: upstream_runner_tunnel
    protocolProfile: omnigent.runner_tunnel.983c93c6

sessionDefaults:
  hostType: managed
  deleteProviderSessionAfterHarvest: false
  capture:
    stream: true
    snapshots: true
    changedFiles: true
    workspaceFiles: true
    workspaceDiffs: capability_probe
    sessionFiles: true
    childSessions: true

idempotency:
  firstMessageStateMachine:
    - not_prepared
    - prepared
    - posting
    - posted
    - terminal
  includeIdempotencyMarker: true
  reconcilePostingState: true

observability:
  writeRawEventJournal: true
  writeNormalizedEventJournal: true
  feedWorkflowChat: true
  feedAgentRunObservability: true
  fallbackToLegacyManagedRunLogs: true
"""


# ---------------------------------------------------------------------------
# AC: A schemaVersion moonmind.omnigent_bridge.v1 document with all blocks
# parses and validates.
# ---------------------------------------------------------------------------


def test_section_6_document_parses_and_validates() -> None:
    config = load_bridge_config(SECTION_6_DOCUMENT)

    assert isinstance(config, OmnigentBridgeConfig)
    assert config.schema_version == OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION
    assert config.enabled is True

    # All §6 blocks are modeled.
    assert config.authority.temporal == "moonmind"
    assert config.compatibility.profile == "omnigent.server.v1"
    assert config.public_api.mount_path == "/api/omnigent"
    assert config.public_api.routes.create_session == "/v1/sessions"
    assert (
        config.public_api.routes.resolve_elicitation
        == "/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve"
    )
    assert config.host_connection.upstream_server_url_ref == "default"
    assert config.host_connection.embedded.port == 8000
    assert config.session_defaults.host_type == "managed"
    assert config.session_defaults.capture.workspace_diffs == "capability_probe"
    assert (
        config.idempotency.first_message_state_machine
        == CANONICAL_FIRST_MESSAGE_STATES
    )
    assert config.observability.feed_workflow_chat is True


def test_minimal_document_uses_safe_defaults() -> None:
    config = parse_bridge_config(
        {"schemaVersion": OMNIGENT_BRIDGE_CONFIG_SCHEMA_VERSION}
    )

    # Safe defaults reproduce the §6 proxy-first shape.
    assert config.enabled is True
    assert config.compatibility.host_unchanged is True
    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY
    assert config.public_api.routes.agents == "/api/agents"
    assert (
        config.public_api.routes.resolve_elicitation
        == "/v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve"
    )
    assert config.session_defaults.capture.stream is True


def test_resolve_bridge_config_defaults_without_env() -> None:
    config = resolve_bridge_config(env={})
    assert config.enabled is True
    assert config.public_api.mount_path == "/api/omnigent"


def test_resolve_bridge_config_loads_operator_document(tmp_path) -> None:
    # An operator that disables the bridge and mounts at a custom path must be
    # honored before routes are registered, not overridden by the default.
    doc = tmp_path / "bridge.yaml"
    doc.write_text(
        "schemaVersion: moonmind.omnigent_bridge.v1\n"
        "enabled: false\n"
        "publicApi:\n"
        "  mountPath: /api/custom-omnigent\n",
        encoding="utf-8",
    )
    config = resolve_bridge_config(
        env={OMNIGENT_BRIDGE_CONFIG_PATH_ENV: str(doc)}
    )
    assert config.enabled is False
    assert config.public_api.mount_path == "/api/custom-omnigent"


def test_resolve_bridge_config_missing_path_fails_fast(tmp_path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(BridgeConfigError, match="could not be read"):
        resolve_bridge_config(env={OMNIGENT_BRIDGE_CONFIG_PATH_ENV: str(missing)})


def test_resolve_bridge_config_invalid_document_fails_fast(tmp_path) -> None:
    doc = tmp_path / "bad.yaml"
    doc.write_text("schemaVersion: moonmind.omnigent_bridge.v2\n", encoding="utf-8")
    with pytest.raises(BridgeConfigError):
        resolve_bridge_config(env={OMNIGENT_BRIDGE_CONFIG_PATH_ENV: str(doc)})


def test_empty_document_is_rejected_with_actionable_error() -> None:
    with pytest.raises(BridgeConfigError, match="empty"):
        load_bridge_config("")


def test_non_mapping_top_level_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="mapping/object"):
        load_bridge_config("- just\n- a\n- list\n")


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="schemaVersion"):
        parse_bridge_config({"schemaVersion": "moonmind.omnigent_bridge.v2"})


# ---------------------------------------------------------------------------
# AC: hostProtocolMode accepts both modes, defaults proxy-first, rejects unknown.
# ---------------------------------------------------------------------------


def test_host_protocol_mode_defaults_to_proxy_first() -> None:
    config = parse_bridge_config({})

    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY
    assert config.host_connection.mode == HOST_PROTOCOL_MODE_PROXY


def test_host_protocol_mode_accepts_embedded() -> None:
    config = parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy-conformance",
                    "liveSmokeEvidenceRef": "artifact://omnigent/live-smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/host-auth",
                }
            },
        }
    )

    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
    # hostConnection.mode is resolved from the compatibility mode.
    assert config.host_connection.mode == HOST_PROTOCOL_MODE_EMBEDDED
    assert (
        config.host_connection.embedded.host_auth_conformance_evidence_ref
        == "artifact://omnigent/host-auth"
    )
    assert config.readiness() == {
        "enabled": True,
        "selectedMode": HOST_PROTOCOL_MODE_EMBEDDED,
        "protocolProfile": "omnigent.runner_tunnel.983c93c6",
        "upstreamComponentVersion": "983c93c6",
        "conformanceState": "gated",
        "evidenceRefs": {
            "proxyConformance": "artifact://omnigent/proxy-conformance",
            "liveSmoke": "artifact://omnigent/live-smoke",
            "hostAuthConformance": "artifact://omnigent/host-auth",
        },
        "evidenceValidation": {},
        "gateReason": "validated_embedded_evidence_required",
    }

    validation = {
        key: {"status": "passed"}
        for key in ("proxyConformance", "liveSmoke", "hostAuthConformance")
    }
    assert (
        config.readiness(evidence_validation=validation)["conformanceState"] == "ready"
    )


def test_proxy_readiness_exposes_supported_fallback_without_embedded_evidence(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.example.test")
    readiness = parse_bridge_config({}).readiness()

    assert readiness["selectedMode"] == HOST_PROTOCOL_MODE_PROXY
    assert readiness["protocolProfile"] == "omnigent.server.v1"
    assert readiness["conformanceState"] == "ready"
    assert readiness["evidenceRefs"] == {}


def test_proxy_readiness_is_gated_when_runtime_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_ENABLED", raising=False)
    monkeypatch.delenv("OMNIGENT_SERVER_URL", raising=False)

    assert parse_bridge_config({}).readiness()["conformanceState"] == "gated"


def test_embedded_mode_requires_conformance_and_smoke_evidence() -> None:
    with pytest.raises(BridgeConfigError, match="proxy conformance"):
        parse_bridge_config(
            {"compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED}}
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("authMode", "header_or_token", "authMode"),
        ("protocolProfile", "omnigent.host_runner.v1", "protocolProfile"),
    ],
)
def test_embedded_auth_contract_rejects_unsupported_profiles(
    field: str, value: str, match: str
) -> None:
    with pytest.raises(BridgeConfigError, match=match):
        parse_bridge_config({"hostConnection": {"embedded": {field: value}}})


def test_disabled_embedded_mode_can_be_declared_without_evidence() -> None:
    config = parse_bridge_config(
        {
            "enabled": False,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
        }
    )

    assert config.enabled is False
    assert config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED


def test_unknown_host_protocol_mode_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="hostProtocolMode"):
        parse_bridge_config(
            {"compatibility": {"hostProtocolMode": "carrier_pigeon"}}
        )


def test_host_connection_mode_must_match_compatibility_mode() -> None:
    with pytest.raises(BridgeConfigError, match="must match"):
        parse_bridge_config(
            {
                "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_PROXY},
                "hostConnection": {"mode": HOST_PROTOCOL_MODE_EMBEDDED},
            }
        )


# ---------------------------------------------------------------------------
# AC: Bridge boundary exposes Omnigent nouns/routes and rejects new external
# product vocabulary.
# ---------------------------------------------------------------------------


def test_non_omnigent_vocabulary_in_route_value_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="non-Omnigent"):
        parse_bridge_config(
            {"publicApi": {"routes": {"createSession": "/v1/conversation-broker"}}}
        )


def test_non_omnigent_vocabulary_in_key_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="runtime bus"):
        parse_bridge_config({"runtime_bus": {"enabled": True}})


def test_agent_socket_vocabulary_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="agent socket"):
        parse_bridge_config({"hostConnection": {"agentSocket": "/tmp/x.sock"}})


def test_route_must_be_absolute_and_omnigent_shaped() -> None:
    with pytest.raises(BridgeConfigError, match="absolute path"):
        parse_bridge_config({"publicApi": {"routes": {"agents": "api/agents"}}})

    with pytest.raises(BridgeConfigError, match="Omnigent-shaped"):
        parse_bridge_config(
            {"publicApi": {"routes": {"agents": "/api/widgets"}}}
        )


def test_compatibility_profile_must_be_omnigent_namespaced() -> None:
    with pytest.raises(BridgeConfigError, match="Omnigent-namespaced"):
        parse_bridge_config({"compatibility": {"profile": "acme.server.v1"}})


def test_embedded_protocol_profile_must_be_omnigent_namespaced() -> None:
    with pytest.raises(BridgeConfigError, match="Omnigent-namespaced"):
        parse_bridge_config(
            {"hostConnection": {"embedded": {"protocolProfile": "acme.host.v1"}}}
        )


# ---------------------------------------------------------------------------
# AC: compatibility.hostUnchanged=true is honored; host-modifying config
# is rejected.
# ---------------------------------------------------------------------------


def test_host_unchanged_true_is_honored() -> None:
    config = parse_bridge_config({"compatibility": {"hostUnchanged": True}})

    assert config.compatibility.host_unchanged is True


def test_host_unchanged_false_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="hostUnchanged must be true"):
        parse_bridge_config({"compatibility": {"hostUnchanged": False}})


@pytest.mark.parametrize(
    "payload",
    [
        {"compatibility": {"customHostImage": "ghcr.io/org/host:custom"}},
        {"hostConnection": {"hostBuildArgs": {"FOO": "bar"}}},
        {"hostConnection": {"embedded": {"hostDockerfile": "./Dockerfile"}}},
        {"hostSourcePatch": "patches/host.diff"},
    ],
)
def test_custom_host_build_configuration_is_rejected(payload: dict) -> None:
    with pytest.raises(BridgeConfigError, match="custom host build"):
        parse_bridge_config(payload)


def test_deployment_configuration_is_accepted() -> None:
    # A stock host image *reference* and standard deployment settings are
    # allowed; only custom-build configuration is rejected.
    config = parse_bridge_config(
        {
            "hostConnection": {
                "upstreamServerUrlRef": "prod-endpoint",
                "embedded": {"bindAddress": "127.0.0.1", "port": 7000},
            }
        }
    )

    assert config.host_connection.upstream_server_url_ref == "prod-endpoint"
    assert config.host_connection.embedded.port == 7000


def test_host_dispatch_is_not_rejected_as_custom_build() -> None:
    # "hostDispatch" normalizes to contain "patch" (from "dispatch") but must not
    # be treated as a custom host build request. It still fails structural
    # validation as an unknown/extra field, but not via the host-build gate.
    with pytest.raises(BridgeConfigError) as exc_info:
        parse_bridge_config({"hostConnection": {"hostDispatch": "value"}})

    message = str(exc_info.value)
    assert "custom host build" not in message
    assert "Extra inputs are not permitted" in message


# ---------------------------------------------------------------------------
# AC: The authority map is validated and surfaced to downstream components.
# ---------------------------------------------------------------------------


def test_authority_map_is_surfaced() -> None:
    config = parse_bridge_config({})

    assert config.authority_map() == {
        "temporal": "moonmind",
        "artifacts": "moonmind",
        "liveExecution": "omnigent_host",
    }


@pytest.mark.parametrize(
    "authority",
    [
        {"temporal": "omnigent"},
        {"artifacts": "omnigent_host"},
        {"liveExecution": "moonmind"},
    ],
)
def test_invalid_authority_map_is_rejected(authority: dict) -> None:
    with pytest.raises(BridgeConfigError):
        parse_bridge_config({"authority": authority})


# ---------------------------------------------------------------------------
# Additional fail-fast coverage.
# ---------------------------------------------------------------------------


def test_first_message_state_machine_must_be_canonical_order() -> None:
    with pytest.raises(BridgeConfigError, match="canonical order"):
        parse_bridge_config(
            {"idempotency": {"firstMessageStateMachine": ["posted", "prepared"]}}
        )


def test_unknown_block_is_rejected() -> None:
    with pytest.raises(BridgeConfigError):
        parse_bridge_config({"totallyUnknownBlock": {"x": 1}})


def test_invalid_yaml_is_rejected() -> None:
    with pytest.raises(BridgeConfigError, match="valid YAML/JSON"):
        load_bridge_config("schemaVersion: [unterminated")
