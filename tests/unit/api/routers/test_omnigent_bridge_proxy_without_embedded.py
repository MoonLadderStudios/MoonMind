"""Proxy composition must not require the retired embedded launch modules.

MoonLadderStudios/MoonMind#3955 (remediation): the experimental embedded
host/runner transport no longer admits new work, but its launch modules stay
live until in-flight sessions drain. Proxy launch, reconnect, first message,
control, terminal harvest, and cleanup therefore must compose with those
modules unavailable, while embedded-only builders fail actionably (naming the
retirement row and the supported proxy alternative) instead of crashing on a
bare ImportError.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_MODULE = (
    REPO_ROOT / "api_service/api/routers/omnigent_bridge_composition.py"
)

_LAUNCH_MODULES = (
    "moonmind.omnigent.bridge_embedded",
    "moonmind.omnigent.embedded_host_channel",
    "moonmind.omnigent.embedded_evidence",
)

# Symbols that only exist on the retired embedded path. The proxy builder
# must not reference any of them.
_EMBEDDED_ONLY_NAMES = frozenset(
    {
        "embedded_host_channels",
        "validate_embedded_evidence",
        "verify_embedded_host_auth",
        "EmbeddedEvidenceError",
        "EmbeddedHostChannelError",
        "OmnigentEmbeddedHostProtocolFacade",
    }
)


def _parse_composition() -> ast.Module:
    return ast.parse(COMPOSITION_MODULE.read_text(encoding="utf-8"))


def test_composition_has_no_top_level_launch_import() -> None:
    tree = _parse_composition()
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.add(node.module)
    assert not any(
        module in _LAUNCH_MODULES or module.startswith("moonmind.omnigent.bridge_embedded")
        for module in top_level_imports
    ), sorted(top_level_imports)


def test_proxy_builder_references_no_embedded_only_symbols() -> None:
    tree = _parse_composition()
    proxy_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "build_bridge_session_proxy"
    )
    referenced = {
        node.id for node in ast.walk(proxy_fn) if isinstance(node, ast.Name)
    }
    assert referenced.isdisjoint(_EMBEDDED_ONLY_NAMES), sorted(
        referenced & _EMBEDDED_ONLY_NAMES
    )


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

from moonmind.omnigent.bridge_config import parse_bridge_config
from api_service.api.routers.omnigent_bridge_composition import (
    OmnigentBridgeModeUnsupportedError,
    build_bridge_session_proxy,
    build_embedded_host_facade,
)

proxy = build_bridge_session_proxy(
    config=parse_bridge_config({}), forward_headers={}
)
assert proxy is not None, "proxy composition must not require launch modules"

try:
    build_embedded_host_facade(parse_bridge_config({}))
except OmnigentBridgeModeUnsupportedError as exc:
    assert "upstream_omnigent_server_proxy" in str(exc), str(exc)
    assert "omnigent.legacy.embedded_host_transport" in str(exc), str(exc)
else:
    raise AssertionError("embedded builder must fail actionably without launch modules")

from moonmind.omnigent.embedded_drain import summarize_embedded_drain

disposition = summarize_embedded_drain(
    active_embedded_sessions=0, active_embedded_leases=0
)
assert disposition["drained"] is True
print("proxy-without-embedded-modules: OK")
"""


def test_proxy_composition_succeeds_with_launch_modules_unavailable() -> None:
    pytest.importorskip("fastapi_users")
    completed = subprocess.run(
        [sys.executable, "-c", _ISOLATION_PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stdout: {completed.stdout}\nstderr: {completed.stderr[-4000:]}"
    )
    assert "proxy-without-embedded-modules: OK" in completed.stdout
