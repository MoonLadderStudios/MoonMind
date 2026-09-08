"""Live drain disposition must be reported by a production caller.

MoonLadderStudios/MoonMind#3955 (remediation): the drain probe
(:func:`moonmind.omnigent.embedded_drain.probe_embedded_drain` and the
composition entrypoint
:func:`omnigent_bridge_composition.probe_embedded_transport_drain`) is only
evidence once a route, workflow, or janitor actually calls it. This suite pins
the production caller — ``GET /embedded-transport-drain`` on the real bridge
router — and its contract:

* mode-neutral and read-only (authenticated, bridge-enabled, no proxy-only or
  embedded-only gate, no embedded facade, no launch-module import);
* returns the composition probe disposition verbatim;
* maps a durable-store failure to 503 with a bounded code instead of implying
  drain (missing evidence is a blocker, never an implicit drain).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ROUTER_MODULE = REPO_ROOT / "api_service/api/routers/omnigent_bridge.py"

_HANDLER = "get_omnigent_embedded_transport_drain"
_ROUTE_PATH = "/embedded-transport-drain"


def _parse_router() -> ast.Module:
    return ast.parse(ROUTER_MODULE.read_text(encoding="utf-8"))


def _handler_def(tree: ast.Module) -> ast.AsyncFunctionDef:
    node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == _HANDLER
        ),
        None,
    )
    assert node is not None, (
        f"router must define {_HANDLER} as the production drain-probe caller"
    )
    assert isinstance(node, ast.AsyncFunctionDef)
    return node


def test_drain_route_is_registered_on_the_bridge_router() -> None:
    tree = _parse_router()
    fn = _handler_def(tree)
    paths: set[str] = set()
    for decorator in fn.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "router"
            and func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        ):
            paths.add(str(decorator.args[0].value))
    assert _ROUTE_PATH in paths, sorted(paths)


def test_drain_route_calls_the_composition_probe() -> None:
    tree = _parse_router()
    fn = _handler_def(tree)
    referenced = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}
    assert "probe_embedded_transport_drain" in referenced


def test_drain_route_is_authenticated_mode_neutral_and_read_only() -> None:
    tree = _parse_router()
    fn = _handler_def(tree)
    referenced = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}
    assert "get_current_user" in referenced
    assert "_require_bridge_enabled" in referenced
    for forbidden in (
        "_require_proxy_mode",
        "_require_embedded_mode",
        "_get_create_embedded_facade",
        "build_embedded_host_facade",
        "verify_embedded_host_request",
    ):
        assert forbidden not in referenced, forbidden


def test_drain_route_maps_store_failure_to_503_without_implying_drain() -> None:
    tree = _parse_router()
    fn = _handler_def(tree)
    source = ast.get_source_segment(ROUTER_MODULE.read_text(encoding="utf-8"), fn)
    assert source is not None
    assert "omnigent_embedded_drain_unavailable" in source
    assert "503" in source or "SERVICE_UNAVAILABLE" in source
    # HTTPException (e.g. bridge-disabled 404) must propagate unchanged.
    assert "except HTTPException" in source


_ISOLATION_PROBE = """
import sys

for module in (
    "moonmind.omnigent.bridge_embedded",
    "moonmind.omnigent.embedded_host_channel",
    "moonmind.omnigent.embedded_evidence",
):
    # Poison the import system: any import of a launch module raises
    # ImportError, exactly as if the removal stage had deleted it.
    sys.modules[module] = None

import asyncio

import api_service.api.routers.omnigent_bridge as router
from fastapi import HTTPException
from moonmind.omnigent.bridge_config import parse_bridge_config

import typing

# The launch modules are genuinely unavailable in this probe, and the
# router's facade annotation degrades to its fail-closed fallback.
try:
    import moonmind.omnigent.bridge_embedded  # noqa: F401
except ImportError:
    pass
else:
    raise AssertionError("launch modules must be unavailable in this probe")

assert router.OmnigentEmbeddedHostProtocolFacade is typing.Any

# The drain route must be registered on the production router.
paths = {
    getattr(route, "path", "")
    for route in router.router.routes
    if hasattr(route, "path")
}
assert "/embedded-transport-drain" in paths, sorted(paths)


async def _failing_probe():
    raise RuntimeError("durable store is unavailable")


async def _blocked_probe():
    return {
        "drained": False,
        "blockers": ("active_embedded_sessions:1",),
        "activeEmbeddedSessions": 1,
        "activeEmbeddedLeases": 0,
    }


async def _drained_probe():
    return {
        "drained": True,
        "blockers": (),
        "activeEmbeddedSessions": 0,
        "activeEmbeddedLeases": 0,
    }


config = parse_bridge_config({})

router.probe_embedded_transport_drain = _drained_probe
disposition = asyncio.run(
    router.get_omnigent_embedded_transport_drain(config=config, _user=object())
)
assert disposition["drained"] is True

router.probe_embedded_transport_drain = _blocked_probe
disposition = asyncio.run(
    router.get_omnigent_embedded_transport_drain(config=config, _user=object())
)
assert disposition["drained"] is False
assert disposition["blockers"] == ("active_embedded_sessions:1",)

# A store failure must surface as 503 with a bounded code — never as drain.
router.probe_embedded_transport_drain = _failing_probe
try:
    asyncio.run(
        router.get_omnigent_embedded_transport_drain(config=config, _user=object())
    )
except HTTPException as exc:
    assert exc.status_code == 503, exc.status_code
    assert exc.detail["code"] == "omnigent_embedded_drain_unavailable", exc.detail
else:
    raise AssertionError("drain route must not swallow a store failure")

print("embedded-transport-drain-route: OK")
"""


def test_drain_route_reports_live_disposition_without_launch_modules() -> None:
    pytest.importorskip("fastapi_users")
    pytest.importorskip("aiohttp")
    completed = subprocess.run(
        [sys.executable, "-c", _ISOLATION_PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stdout: {completed.stdout}\nstderr: {completed.stderr[-4000:]}"
    )
    assert "embedded-transport-drain-route: OK" in completed.stdout
