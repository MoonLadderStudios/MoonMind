"""Full router must not hard-require the retired embedded launch modules.

MoonLadderStudios/MoonMind#3955 (remediation): composition-level proxy
isolation is not enough — the real production router
(``api_service.api.routers.omnigent_bridge``) must also import with the
embedded launch modules unavailable so proxy launch, reconnect, first
message, control, terminal harvest, and cleanup pass through production
wiring while embedded-only surfaces fail actionably (naming the retirement
row and the supported proxy alternative) instead of crashing on a bare
ImportError.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ROUTER_MODULE = REPO_ROOT / "api_service/api/routers/omnigent_bridge.py"

_LAUNCH_MODULES = frozenset(
    {
        "moonmind.omnigent.bridge_embedded",
        "moonmind.omnigent.embedded_host_channel",
        "moonmind.omnigent.embedded_evidence",
    }
)


def _parse_router() -> ast.Module:
    return ast.parse(ROUTER_MODULE.read_text(encoding="utf-8"))


def test_router_has_no_unconditional_top_level_launch_import() -> None:
    tree = _parse_router()
    unconditional: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            unconditional.update(
                alias.name for alias in node.names if alias.name in _LAUNCH_MODULES
            )
        elif isinstance(node, ast.ImportFrom) and node.module in _LAUNCH_MODULES:
            unconditional.add(node.module)
    assert not unconditional, sorted(unconditional)


def test_retired_tunnels_close_without_loading_transport() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from api_service.api.routers.omnigent_bridge import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for path in ("/v1/hosts/old/tunnel", "/v1/runners/old/tunnel"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path):
                pass
        assert exc.value.code == 4404
        assert "omnigent_embedded_transport_retired" in exc.value.reason
        assert "upstream_omnigent_server_proxy" in exc.value.reason


_ISOLATION_PROBE = """
import sys

for module in (
    "moonmind.omnigent.bridge_embedded",
    "moonmind.omnigent.embedded_host_channel",
    "moonmind.omnigent.embedded_evidence",
):
    # Poison the import system: any import of a launch module raises
    # ImportError, exactly as if the module had been deleted.
    sys.modules[module] = None

import api_service.api.routers.omnigent_bridge as router

import typing

# The launch modules are genuinely unavailable in this probe, and the
# router's facade annotation degrades to its fail-closed fallback.
try:
    import moonmind.omnigent.bridge_embedded  # noqa: F401
except ImportError:
    pass
else:
    raise AssertionError("launch modules must be unavailable in this probe")

assert not hasattr(router, "build_embedded_host_facade")

from moonmind.omnigent.bridge_config import parse_bridge_config
from api_service.api.routers.omnigent_bridge_composition import (
    build_bridge_session_proxy,
    probe_embedded_transport_drain,
)

proxy = build_bridge_session_proxy(
    config=parse_bridge_config({}), forward_headers={}
)
assert proxy is not None, "proxy composition must not require launch modules"

from moonmind.omnigent.embedded_drain import probe_embedded_drain


class _EmptyStore:
    async def active_host_protocol_modes(self):
        return {}

    async def list_embedded_host_readiness(self):
        return []

    async def count_active_embedded_host_leases(self):
        return 0

    async def cleanup_required_host_lease_refs(self):
        return []


import asyncio

disposition = asyncio.run(probe_embedded_drain(_EmptyStore()))
assert disposition["drained"] is True

import inspect

assert inspect.iscoroutinefunction(probe_embedded_transport_drain)
print("router-without-embedded-modules: OK")
"""


def test_router_imports_and_proxy_composes_with_launch_modules_unavailable() -> None:
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
    assert "router-without-embedded-modules: OK" in completed.stdout
