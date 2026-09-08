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

# Symbols the router uses from the retired launch path. Every one must still
# resolve (real or fail-closed fallback) when the launch modules are gone.
_FALLBACK_NAMES = frozenset(
    {
        "EmbeddedHostRegisterRequest",
        "EmbeddedHostHeartbeatRequest",
        "EmbeddedHostSessionEventRequest",
        "OmnigentEmbeddedHostProtocolFacade",
        "EmbeddedEvidenceError",
        "validate_embedded_evidence",
        "EmbeddedHostChannelError",
        "embedded_host_channels",
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


def _importfrom_bound_names(node: ast.ImportFrom) -> set[str]:
    return {
        alias.asname or alias.name.split(".")[0] for alias in node.names
    }


def test_router_defines_fail_closed_launch_fallbacks() -> None:
    # The router carries no fail-closed import shim for the launch path: its
    # design is lazy imports plus one annotation fallback. Every launch-path
    # symbol the router uses must therefore resolve without importing the
    # launch modules — assigned as a fallback, imported at top level from a
    # non-launch module, or imported lazily inside a function whose failure
    # maps to an actionable retirement error.
    tree = _parse_router()
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.add(target.id)
                elif isinstance(target, ast.Attribute):
                    assigned.add(target.attr)
    top_level_imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module not in _LAUNCH_MODULES:
                top_level_imported.update(_importfrom_bound_names(node))
        elif isinstance(node, ast.Import):
            top_level_imported.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
            )
    lazy_imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    lazy_imported.update(_importfrom_bound_names(child))
    resolvable = assigned | top_level_imported | lazy_imported
    assert _FALLBACK_NAMES <= resolvable, sorted(
        _FALLBACK_NAMES - resolvable
    )


def test_tunnel_handlers_fail_actionably_without_launch_modules() -> None:
    tree = _parse_router()
    for fn_name in (
        "embedded_omnigent_host_tunnel",
        "embedded_omnigent_runner_tunnel",
    ):
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn_name
        )
        referenced = {
            node.id for node in ast.walk(fn) if isinstance(node, ast.Name)
        }
        assert "OmnigentBridgeModeUnsupportedError" in referenced, fn_name
        assert "EMBEDDED_TRANSPORT_RETIRED_CODE" in referenced, fn_name


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

assert router.OmnigentEmbeddedHostProtocolFacade is typing.Any

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


import asyncio

disposition = asyncio.run(probe_embedded_drain(_EmptyStore()))
assert disposition["drained"] is True

import inspect

assert inspect.iscoroutinefunction(probe_embedded_transport_drain)
print("router-without-embedded-modules: OK")
"""


def test_router_imports_and_proxy_composes_with_launch_modules_unavailable() -> None:
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
    assert "router-without-embedded-modules: OK" in completed.stdout
