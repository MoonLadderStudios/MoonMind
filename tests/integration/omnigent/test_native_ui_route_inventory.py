"""Exact stock Omnigent route-inventory regression tests.

MoonLadderStudios/MoonMind#3635 requires the inventory to be generated from
the pinned upstream artifacts, independently of the facade classification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.omnigent.native_ui_route_inventory import (
    INVENTORY_SCHEMA_VERSION,
    NativeUiRouteInventoryError,
    exact_artifact_role_digest,
    generate_native_ui_route_inventory,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


_REPO_ROOT = Path(__file__).parents[3]
_FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "omnigent"
    / "native_ui_network_contract_v2.json"
)


def test_generated_inventory_matches_the_versioned_exact_artifact_fixture() -> None:
    generated = generate_native_ui_route_inventory(_REPO_ROOT)
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert generated == fixture
    assert generated["schemaVersion"] == INVENTORY_SCHEMA_VERSION
    assert generated["inventoryDigest"].startswith("sha256:")
    assert generated == generate_native_ui_route_inventory(_REPO_ROOT)


def test_inventory_binds_every_required_exact_artifact() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)

    assert set(inventory["artifactDigests"]) == {
        "omnigent",
        "ui",
        "server",
        "host",
        "harnessImplementation",
        "moonmindFacade",
    }
    assert inventory["artifactDigests"]["omnigent"].startswith("git:")
    for key in {
        "ui",
        "server",
        "host",
        "harnessImplementation",
        "moonmindFacade",
    }:
        assert inventory["artifactDigests"][key].startswith("sha256:")

    assert exact_artifact_role_digest(_REPO_ROOT, "omnigent_host") == (
        inventory["artifactDigests"]["host"]
    )
    assert exact_artifact_role_digest(_REPO_ROOT, "moonmind_facade") == (
        inventory["artifactDigests"]["moonmindFacade"]
    )


def test_running_image_inventory_requires_matching_in_image_bytes(tmp_path) -> None:
    reviewed = generate_native_ui_route_inventory(_REPO_ROOT)
    compiled_ui = tmp_path / "compiled-ui"
    compiled_ui.mkdir()
    (compiled_ui / "index.html").write_text("compiled stock UI", encoding="utf-8")
    (compiled_ui / "app.js").write_text(
        'fetch("/v1/info")',
        encoding="utf-8",
    )
    observed = {
        "omnigentHost": reviewed["artifactDigests"]["host"],
        "moonmindFacade": reviewed["artifactDigests"]["moonmindFacade"],
        "moonmindHarness": exact_artifact_role_digest(
            _REPO_ROOT, "moonmind_harness"
        ),
    }
    images = {
        "omnigentServer": "omnigent-server@sha256:" + "1" * 64,
        "omnigentHost": "omnigent-host@sha256:" + "2" * 64,
        "moonmindFacade": "moonmind@sha256:" + "3" * 64,
    }

    generated = generate_native_ui_route_inventory(
        _REPO_ROOT,
        compiled_ui_root=compiled_ui,
        deployable_images=images,
        observed_artifact_digests=observed,
    )
    assert generated["artifactProvenance"]["sourceMode"] == "running_images"
    assert generated["artifactProvenance"]["inImageArtifactDigests"] == observed
    compiled_surface = generated["artifactProvenance"]["compiledUiNetworkSurface"]
    assert compiled_surface["routeLiteralCount"] == 1
    assert compiled_surface["routeLiterals"][0]["resolvedRoutes"] == [
        {"method": "GET", "path": "/v1/info", "routeKey": "GET /v1/info"}
    ]

    with pytest.raises(
        NativeUiRouteInventoryError,
        match="in-image source bytes do not match",
    ):
        generate_native_ui_route_inventory(
            _REPO_ROOT,
            compiled_ui_root=compiled_ui,
            deployable_images=images,
            observed_artifact_digests={
                **observed,
                "moonmindFacade": "sha256:" + "4" * 64,
            },
        )


def test_compiled_ui_unknown_route_literal_is_stably_fail_closed(tmp_path) -> None:
    reviewed = generate_native_ui_route_inventory(_REPO_ROOT)
    compiled_ui = tmp_path / "compiled-ui"
    compiled_ui.mkdir()
    (compiled_ui / "app.js").write_text(
        'fetch("/v1/new-unclassified-route")',
        encoding="utf-8",
    )
    observed = {
        "omnigentHost": reviewed["artifactDigests"]["host"],
        "moonmindFacade": reviewed["artifactDigests"]["moonmindFacade"],
        "moonmindHarness": exact_artifact_role_digest(
            _REPO_ROOT, "moonmind_harness"
        ),
    }
    generated = generate_native_ui_route_inventory(
        _REPO_ROOT,
        compiled_ui_root=compiled_ui,
        deployable_images={
            "omnigentServer": "omnigent-server@sha256:" + "1" * 64,
            "omnigentHost": "omnigent-host@sha256:" + "2" * 64,
            "moonmindFacade": "moonmind@sha256:" + "3" * 64,
        },
        observed_artifact_digests=observed,
    )

    literal = generated["artifactProvenance"]["compiledUiNetworkSurface"][
        "routeLiterals"
    ][0]
    assert literal["classification"] == "stable_unsupported_route"
    assert literal["resolution"] == "reject_before_upstream"
    assert "resolvedRoutes" not in literal


def test_every_exact_stock_route_has_one_explicit_classification() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)
    routes = inventory["routes"]

    assert routes
    assert len({route["routeKey"] for route in routes}) == len(routes)
    assert {route["classification"] for route in routes} <= {
        "binding_scoped",
        "fail_closed",
    }
    assert all(
        route["publicRoute"].startswith("/api/workflow-chat-bindings/")
        for route in routes
    )
    assert all(route["callerPermission"] for route in routes)
    assert all(route["requestBounds"] for route in routes)
    assert all(route["responseBounds"] for route in routes)
    assert all(route["identityVirtualization"] for route in routes)
    assert all(route["reconnect"] for route in routes)
    assert all(route["idempotency"] for route in routes)
    assert all(route["historicalRead"] for route in routes)
    assert all(route["unsupportedBehavior"] for route in routes)
    assert all(route["handlerDigest"].startswith("sha256:") for route in routes)
    assert all(route["responseContract"]["declaredStatusCodes"] for route in routes)
    assert all(
        route["responseContract"]["mutationReceiptSchemaVersion"]
        == "moonmind.omnigent.mutation-receipt.v1"
        for route in routes
        if route["classification"] == "binding_scoped"
        and route["method"] not in {"GET", "WEBSOCKET"}
    )

    by_key = {route["routeKey"]: route for route in routes}
    assert (
        by_key["GET /v1/sessions/{session_id}/items"]["classification"]
        == "binding_scoped"
    )
    assert (
        by_key["WEBSOCKET /v1/sessions/updates"]["classification"]
        == "binding_scoped"
    )
    assert (
        by_key["GET /v1/sessions/{session_id}/child_sessions"]["classification"]
        == "fail_closed"
    )
    assert (
        by_key["GET /v1/sessions/{session_id}/resources/files/{file_id}"][
            "classification"
        ]
        == "fail_closed"
    )
    assert (
        by_key["PUT /v1/sessions/{session_id}/codex_goal"]["classification"]
        == "fail_closed"
    )


def test_every_ui_network_call_is_method_aware_and_exactly_joined() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)
    route_keys = {route["routeKey"] for route in inventory["routes"]}
    references = inventory["uiRouteReferences"]

    assert references
    assert inventory["uiReferenceCount"] == len(references)
    assert all(reference["join"] == "exact_stock_route" for reference in references)
    assert all(reference["routeKey"] in route_keys for reference in references)
    assert all(reference["method"] for reference in references)
    assert all(reference["sourceLine"] > 0 for reference in references)
    assert {
        reference["routeKey"]
        for reference in references
        if reference["method"] == "WEBSOCKET"
    } == {
        "WEBSOCKET /v1/dictation/stream",
        "WEBSOCKET /v1/sessions/updates",
        (
            "WEBSOCKET /v1/sessions/{session_id}/resources/terminals/"
            "{terminal_id}/attach"
        ),
    }

    delegated = inventory["uiDelegatedNetworkCalls"]
    assert delegated
    assert inventory["uiDelegatedCallCount"] == len(delegated)
    assert {call["classification"] for call in delegated} <= {
        "stable_unsupported_route",
        "scoped_transport_adapter",
    }
    assert all(call["method"] for call in delegated)
    assert all(call["pathPattern"].startswith("/") for call in delegated)
    assert all(
        call["resolution"]
        in {"reject_before_upstream", "exact_method_path_allowlist"}
        for call in delegated
    )
    assert all(
        call.get("resolvedRoutes")
        if call["classification"] == "scoped_transport_adapter"
        else call["resolution"] == "reject_before_upstream"
        for call in delegated
    )
    assert all(call["argumentDigest"].startswith("sha256:") for call in delegated)
    assert all(
        call["unknownBehavior"] == "omnigent_chat_transport_unsupported"
        for call in delegated
    )


def test_literal_default_environment_variants_join_the_scoped_facade() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)
    by_key = {route["routeKey"]: route for route in inventory["routes"]}
    references = inventory["uiRouteReferences"]

    expected = {
        "GET /v1/sessions/{session_id}/resources/environments/{environment_id}/changes",
        (
            "GET /v1/sessions/{session_id}/resources/environments/{environment_id}/"
            "diff/{relative_path:path}"
        ),
        (
            "GET /v1/sessions/{session_id}/resources/environments/{environment_id}/"
            "filesystem/{relative_path:path}"
        ),
        (
            "PUT /v1/sessions/{session_id}/resources/environments/{environment_id}/"
            "filesystem/{relative_path:path}"
        ),
    }
    assert all(by_key[key]["classification"] == "binding_scoped" for key in expected)
    for key in expected:
        assert by_key[key]["pathConstraints"] == {
            "environment_id": {
                "allowedLiterals": ["default"],
                "otherValuesBehavior": "omnigent_chat_transport_unsupported",
            }
        }
    environment_references = [
        reference
        for reference in references
        if "/resources/environments/default/" in reference["path"]
    ]
    assert environment_references
    assert all("{default_environment_id}" not in item["path"] for item in references)
    assert all(
        item["join"] == "exact_stock_route" for item in environment_references
    )


def test_health_is_an_exact_stock_ui_reference() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)

    health = [
        reference
        for reference in inventory["uiRouteReferences"]
        if reference["path"] == "/health"
    ]
    assert health == [
        {
            "classification": "binding_scoped",
            "facadeOperation": "liveness",
            "join": "exact_stock_route",
            "method": "GET",
            "networkApi": "authenticatedFetch",
            "path": "/health",
            "routeKey": "GET /health",
            "sourceFile": "omnigent/web/src/hooks/useRunnerHealth.ts",
            "sourceLine": 95,
            "unsupportedBehavior": "not_applicable",
        }
    ]


def test_websocket_message_classes_come_from_exact_ui_and_server_sources() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)
    by_route = {item["routeKey"]: item for item in inventory["websocketProtocols"]}

    assert set(by_route["WEBSOCKET /v1/sessions/updates"]["clientMessageClasses"]) == {"watch"}
    assert {"snapshot", "changed", "removed", "hosts_changed", "heartbeat"} <= set(
        by_route["WEBSOCKET /v1/sessions/updates"]["serverMessageClasses"]
    )
    assert {"binary_input", "resize"} <= set(
        by_route[
            "WEBSOCKET /v1/sessions/{session_id}/resources/terminals/{terminal_id}/attach"
        ]["clientMessageClasses"]
    )
    assert {"binary_audio", "stop"} <= set(
        by_route["WEBSOCKET /v1/dictation/stream"]["clientMessageClasses"]
    )


def test_sse_cursor_and_reconnect_contract_comes_from_exact_sources() -> None:
    inventory = generate_native_ui_route_inventory(_REPO_ROOT)
    by_route = {item["routeKey"]: item for item in inventory["sseProtocols"]}

    stream = by_route["GET /v1/sessions/{session_id}/stream"]
    assert stream["upstreamCursorBehavior"] == (
        "snapshot_then_live_tail_no_server_replay"
    )
    assert stream["facadeCursorBehavior"] == (
        "durable_sequence_cursor_and_last_event_id"
    )
    assert stream["reconnectAuthorization"] == (
        "reauthorize_on_connect_and_periodically"
    )
