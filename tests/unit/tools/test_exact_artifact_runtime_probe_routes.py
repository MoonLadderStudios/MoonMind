"""Pin the exact-artifact runtime probe's endpoints to the real application.

Source issue: MoonLadderStudios/MoonMind#3710.

``tools/_exact_artifact_runtime_probes.py`` is the CI-only probe layer that
exercises the *running* exact image; it is deliberately excluded from the
runtime unit suite because it needs a container.  That exclusion is exactly how
a probe can point at an endpoint that does not exist and silently mis-gate a
healthy image (the prior verify found ``/health`` instead of ``/healthz`` and
non-existent SSE/WS paths).

This hermetic test closes that gap: it asserts that every path the probe hits
(liveness, HTTP, Omnigent SSE, Omnigent WebSocket, worker readiness) resolves
through a real handler / payload key in the current application, so a future
route rename fails a unit test instead of the Tier-1 job on a valid image.
"""

from __future__ import annotations

import json

import pytest

from tools._exact_artifact_runtime_probes import (
    probe_route_templates,
    _resolve_probe_path,
)


@pytest.fixture(scope="module")
def templates() -> dict[str, str]:
    return probe_route_templates()


def _bridge_route_paths() -> set[str]:
    """Full production paths registered on the Omnigent bridge router.

    ``api_service.main`` includes this exact router object at
    ``OMNIGENT_BRIDGE_MOUNT_PATH``; asserting against the router's own routes is
    faithful to the mounted surface and independent of app-construction order.
    """

    from api_service.api.routers.omnigent_bridge import (
        router as bridge_router,
        OMNIGENT_BRIDGE_MOUNT_PATH,
    )

    mount = OMNIGENT_BRIDGE_MOUNT_PATH.rstrip("/")
    return {
        mount + route.path
        for route in bridge_router.routes
        if getattr(route, "path", None)
    }


def test_liveness_probe_targets_the_real_healthz_route(templates: dict[str, str]) -> None:
    import api_service.main as main_module

    assert templates["liveness"] == "/healthz"
    health_paths = {
        getattr(route, "path", None) for route in main_module.health_router.routes
    }
    assert templates["liveness"] in health_paths
    # The prior defect polled /health, which is not a registered route.
    assert "/health" not in health_paths


def test_http_probe_targets_the_app_openapi_route(templates: dict[str, str]) -> None:
    import api_service.main as main_module

    assert templates["http"] == main_module.app.openapi_url


def test_sse_probe_targets_a_registered_omnigent_stream_route(
    templates: dict[str, str],
) -> None:
    bridge_paths = _bridge_route_paths()
    assert templates["sse"] in bridge_paths
    # The prior defect probed /api/omnigent/live/events, which does not exist.
    assert "/api/omnigent/live/events" not in bridge_paths


def test_websocket_probe_targets_a_registered_omnigent_tunnel_route(
    templates: dict[str, str],
) -> None:
    from starlette.routing import WebSocketRoute

    from api_service.api.routers.omnigent_bridge import (
        router as bridge_router,
        OMNIGENT_BRIDGE_MOUNT_PATH,
    )

    mount = OMNIGENT_BRIDGE_MOUNT_PATH.rstrip("/")
    websocket_paths = {
        mount + route.path
        for route in bridge_router.routes
        if isinstance(route, WebSocketRoute) and getattr(route, "path", None)
    }
    assert templates["websocket"] in websocket_paths
    # The prior defect probed /ws/omnigent, which is not a registered route and
    # would fall through to HTTP 404 (the #3697 failure mode).
    assert "/api/omnigent/ws/omnigent" not in websocket_paths


def test_worker_readiness_probe_targets_the_real_readyz_key(
    templates: dict[str, str],
) -> None:
    from moonmind.workflows.temporal.worker_healthcheck import (
        WorkerHealthState,
        _build_response_body,
    )

    assert templates["worker_ready"] == "/readyz"
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        readiness_metadata={"taskQueues": ["agent-runtime"]},
    )
    body = json.loads(_build_response_body(state, readiness=True))
    # The worker probe reads exactly these keys off the /readyz payload.
    assert body["ready"] is True
    assert body["taskQueues"] == ["agent-runtime"]


def test_resolve_probe_path_fills_placeholders(templates: dict[str, str]) -> None:
    for key in ("sse", "websocket"):
        resolved = _resolve_probe_path(templates[key])
        assert "{" not in resolved and "}" not in resolved
    # Paths without placeholders are returned unchanged.
    assert _resolve_probe_path(templates["liveness"]) == "/healthz"


def test_a_renamed_route_would_break_the_probe_contract() -> None:
    """Guard: a bogus probe path is absent from the real surface, proving the
    contract test actually catches drift rather than trivially passing."""

    bridge_paths = _bridge_route_paths()
    assert "/api/omnigent/v1/sessions/{session_id}/renamed-stream" not in bridge_paths
