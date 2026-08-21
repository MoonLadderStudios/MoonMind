"""Unit tests for the versioned native Omnigent UI compatibility map.

MoonLadderStudios/MoonMind#3635: the facade owns transport and route coverage
for the native UI. Every recognized route/transport must be inventoried with an
explicit disposition, unknown transports must fail closed, terminal/PTY
operations must be separately classed and gated, and WebSocket subprotocols must
be allowlisted.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent import native_ui_compat as compat
from moonmind.omnigent.workflow_chat_facade import FACADE_OPERATIONS


def test_compatibility_map_is_versioned_and_inventories_every_route() -> None:
    cmap = compat.compatibility_map()

    assert cmap["version"] == compat.NATIVE_UI_COMPAT_VERSION == "omnigent.server.v1"
    # Every recognized route declares an explicit, known disposition — no route
    # is left ambiguous (acceptance criterion 1).
    dispositions = {route["disposition"] for route in cmap["routes"]}
    assert dispositions <= {
        compat.DISPOSITION_SERVED,
        compat.DISPOSITION_COMPAT_REVIEW,
    }
    # HTTP + SSE + WebSocket transports are all covered.
    assert set(cmap["transports"]) == {"http", "sse", "websocket"}
    assert cmap["wsSubprotocols"] == list(compat.NATIVE_UI_WS_SUBPROTOCOLS)
    required_policy_fields = {
        "publicRoute",
        "callerPermission",
        "requestBounds",
        "responseBounds",
        "identityVirtualization",
        "reconnect",
        "idempotency",
        "historicalRead",
        "unsupportedBehavior",
        "mutationReceipt",
    }
    assert all(required_policy_fields <= route.keys() for route in cmap["routes"])


def test_served_surface_is_single_sourced_from_facade_operations() -> None:
    served = {
        route.name
        for route in compat.NATIVE_UI_ROUTES
        if route.disposition == compat.DISPOSITION_SERVED
    }
    # The transcript slice is single-sourced from #3634 and the reviewed pinned
    # workspace routes are served by this compatibility module.
    assert served == {
        *(operation.name for operation in FACADE_OPERATIONS),
        "terminal_view",
        "terminal_create",
        "terminal_status",
        "terminal_close",
        "terminal_shell",
        "execution_logs",
        "workspace_edit",
        "workspace_delete",
        "resource_upload",
        "resource_download",
        "resource_attach",
        "browser_pane",
        "subagent_tree",
        "subagent_control",
        "task_todo",
        "task_mutate",
        "host_liveness",
        "runner_liveness",
        "session_reconnect",
        "ws_session_updates",
        "terminal_attach",
        "dictation_stream",
        "session_items",
    }


@pytest.mark.parametrize(
    ("method", "path", "name"),
    [
        ("GET", "v1/sessions/b1/resources/terminals", "terminal_view"),
        ("POST", "v1/sessions/b1/resources/terminals", "terminal_create"),
        ("DELETE", "v1/sessions/b1/resources/terminals/t1", "terminal_close"),
        ("PATCH", "v1/sessions/b1/tasks/t1", "task_mutate"),
        ("POST", "v1/sessions/b1/reconnect", "session_reconnect"),
    ],
)
def test_classify_reviewed_http_routes(method: str, path: str, name: str) -> None:
    match = compat.classify_native_ui_http(method, path)
    assert match is not None
    assert match.route.name == name
    assert match.route.disposition == compat.DISPOSITION_SERVED
    assert compat.upstream_http_path(match, "provider-1").startswith(
        "/v1/sessions/provider-1/"
    )


def test_native_http_classifier_fails_closed_on_unknown_method_or_route() -> None:
    assert compat.classify_native_ui_http("TRACE", "v1/sessions/b1/tasks") is None
    assert compat.classify_native_ui_http("GET", "v1/sessions/b1/new-route") is None
    # Input and resize are terminal-attach WebSocket frames. The internal
    # cross-session transfer route is not part of the stock browser contract.
    assert compat.classify_native_ui_http(
        "POST", "v1/sessions/b1/resources/terminals/t1/transfer"
    ) is None
    assert compat.classify_native_ui_http(
        "PATCH", "v1/sessions/b1/resources/terminals/t1"
    ) is None


def test_every_native_ui_transport_class_is_represented() -> None:
    classes = {route.operation_class for route in compat.NATIVE_UI_ROUTES}
    frame_classes = {
        operation
        for route in compat.NATIVE_UI_ROUTES
        for operation in route.frame_operations
    }
    # The transports/route families the issue enumerates are all classified.
    assert {
        compat.CLASS_STREAM,
        compat.CLASS_CONTROL,
        compat.CLASS_RESOURCE_READ,
        compat.CLASS_RESOURCE_MUTATE,
        compat.CLASS_TERMINAL_VIEW,
        compat.CLASS_TERMINAL_CREATE,
        compat.CLASS_TERMINAL_ATTACH,
        compat.CLASS_TERMINAL_CLOSE,
        compat.CLASS_EXEC_LOG,
        compat.CLASS_BROWSER_PANE,
        compat.CLASS_SUBAGENT,
        compat.CLASS_TASK,
        compat.CLASS_RECONNECT,
    } <= classes
    assert {
        compat.CLASS_TERMINAL_INPUT,
        compat.CLASS_TERMINAL_RESIZE,
    } <= frame_classes


@pytest.mark.parametrize(
    ("path", "expected_name", "expected_class"),
    [
        ("v1/sessions/updates", "ws_session_updates", compat.CLASS_STREAM),
        (
            "v1/sessions/s1/resources/terminals/t1/attach",
            "terminal_attach",
            compat.CLASS_TERMINAL_ATTACH,
        ),
        ("v1/dictation/stream", "dictation_stream", compat.CLASS_CONTROL),
    ],
)
def test_classify_websocket_matches_known_transports(
    path: str, expected_name: str, expected_class: str
) -> None:
    match = compat.classify_native_ui_websocket(path)
    assert match is not None
    assert match.route.name == expected_name
    assert match.route.operation_class == expected_class
    assert match.route.disposition in {
        compat.DISPOSITION_SERVED,
        compat.DISPOSITION_COMPAT_REVIEW,
    }


def test_classify_websocket_captures_session_id() -> None:
    match = compat.classify_native_ui_websocket(
        "v1/sessions/prov-123/resources/terminals/t9/attach"
    )
    assert match is not None
    assert match.params.get("session_id") == "prov-123"
    assert (
        compat.upstream_websocket_path(match, "provider-session")
        == "/v1/sessions/provider-session/resources/terminals/t9/attach"
    )


def test_unknown_websocket_route_fails_closed() -> None:
    # An unrecognized transport is not proxied by default (acceptance criterion 8).
    assert compat.classify_native_ui_websocket("v1/sessions/s1/unknown-thing") is None
    assert compat.classify_native_ui_websocket("../../etc/passwd") is None


def test_terminal_write_operations_require_ungranted_capabilities() -> None:
    # Terminal create/attach/input/resize/close require capabilities the browser
    # facade never grants, so nonowners and read-only viewers cannot write to a
    # PTY (acceptance criteria 4-5).
    write_ops = {
        "terminal_create": compat.CAP_CREATE_TERMINAL,
        "terminal_attach": compat.CAP_ATTACH_TERMINAL,
        "terminal_close": compat.CAP_CLOSE_TERMINAL,
    }
    by_name = {route.name: route for route in compat.NATIVE_UI_ROUTES}
    for name, capability in write_ops.items():
        assert by_name[name].capability == capability
        assert by_name[name].mutation is True
    # Terminal viewing / exec-log inspection is a read capability, kept distinct.
    assert by_name["terminal_view"].capability == compat.CAP_VIEW_TERMINAL
    assert by_name["terminal_view"].mutation is False
    assert by_name["terminal_attach"].frame_operations == (
        compat.CLASS_TERMINAL_INPUT,
        compat.CLASS_TERMINAL_RESIZE,
    )


def test_subprotocol_negotiation_allowlists_one_protocol() -> None:
    allowed = compat.NATIVE_UI_WS_SUBPROTOCOLS[0]
    # No offered subprotocol -> accept without one.
    assert compat.negotiate_ws_subprotocol([]) is None
    assert compat.negotiate_ws_subprotocol(None) is None
    # An allowlisted subprotocol is selected.
    assert compat.negotiate_ws_subprotocol([allowed]) == allowed
    assert compat.negotiate_ws_subprotocol(["other", allowed]) == allowed


def test_subprotocol_negotiation_rejects_unlisted_protocol() -> None:
    with pytest.raises(compat.NativeUiCompatibilityError) as exc:
        compat.negotiate_ws_subprotocol(["evil.raw.protocol"])
    assert exc.value.code == compat.CODE_WS_SUBPROTOCOL_REJECTED
    assert exc.value.status_code == 403
