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
        "outside_binding_auth_fail_closed",
        "scoped_transport_adapter_then_exact_route_gate",
    }
    assert all(call["argumentDigest"].startswith("sha256:") for call in delegated)
    assert all(
        call["unknownBehavior"] == "omnigent_chat_transport_unsupported"
        for call in delegated
    )


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
