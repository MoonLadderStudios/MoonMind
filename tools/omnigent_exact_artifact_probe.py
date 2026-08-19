#!/usr/bin/env python3
"""In-image runtime capability probe for the exact-artifact gate.

Source issue: MoonLadderStudios/MoonMind#3710.

This script runs *inside the exact deployable image* (it depends on the same
installed packages the deployed process uses) and reports, as a JSON list of
``{"name", "ok", "detail"}`` entries, whether each import/introspection-level
runtime capability is present.  It is invoked by
``tools/run_omnigent_exact_artifact_conformance.py`` via ``docker run <image>``
for the ``server`` and ``worker`` roles; the driver merges these results with
the runtime-only capabilities it probes from outside the container (HTTP/SSE/
WebSocket route handshakes, database migrations, worker task-queue/readiness
advertisement, and the bounded fake-provider execution).

The probe never crashes on a missing capability: a stripped dependency — for
example the Uvicorn WebSocket implementation removed in #3697 — is reported as
``ok=False`` so the gate fails closed with a named reason.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import shutil
import sys
from typing import Callable


def _signal(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _importable(module: str) -> tuple[bool, str]:
    try:
        if importlib.util.find_spec(module) is None:
            return False, f"module {module!r} is not installed"
    except (ImportError, ValueError) as exc:  # pragma: no cover - defensive
        return False, f"module {module!r} could not be resolved: {exc}"
    return True, f"module {module!r} is importable"


def probe_uvicorn_websocket() -> dict[str, object]:
    """Prove Uvicorn resolves an *installed* WebSocket protocol implementation.

    This is the #3697 regression anchor: a deployed image whose WebSocket
    dependency was dropped resolves to the ``"none"`` implementation and
    silently answers real WebSocket routes with HTTP 404.
    """
    try:
        from uvicorn.config import WS_PROTOCOLS
    except Exception as exc:  # pragma: no cover - uvicorn always present in image
        return _signal("uvicorn_websocket_impl", False, f"uvicorn missing: {exc}")

    # The installed implementations Uvicorn can bind to.  ``auto``/``none`` are
    # resolution strategies, not implementations.
    impl_modules = {
        "websockets": "websockets",
        "websockets-sansio": "websockets",
        "wsproto": "wsproto",
    }
    available = sorted(
        name
        for name, module in impl_modules.items()
        if name in WS_PROTOCOLS and importlib.util.find_spec(module) is not None
    )
    if not available:
        return _signal(
            "uvicorn_websocket_impl",
            False,
            "no installed Uvicorn WebSocket protocol implementation "
            "(websockets/wsproto) — real WebSocket routes would 404 (#3697)",
        )
    return _signal(
        "uvicorn_websocket_impl",
        True,
        f"installed Uvicorn WebSocket implementations: {available}",
    )


def _probe_import(name: str, module: str) -> Callable[[], dict[str, object]]:
    def _run() -> dict[str, object]:
        ok, detail = _importable(module)
        return _signal(name, ok, detail)

    return _run


def probe_docker_or_compose() -> dict[str, object]:
    if shutil.which("docker") or shutil.which("docker-compose"):
        return _signal("docker_or_compose_available", True, "docker CLI present")
    ok, detail = _importable("docker")
    return _signal("docker_or_compose_available", ok, f"python docker sdk: {detail}")


# Import/introspection-level capabilities probed inside the image, per role.
# Runtime-only capabilities (route handshakes, migrations, worker task-queue /
# readiness advertisement, fake-provider convergence) are added by the driver.
_SERVER_PROBES: tuple[Callable[[], dict[str, object]], ...] = (
    _probe_import("api_entrypoint_import", "api_service.main"),
    probe_uvicorn_websocket,
    _probe_import("omnigent_adapters_import", "moonmind.omnigent.execute"),
    _probe_import("opentelemetry_init", "opentelemetry"),
    _probe_import("temporal_client_init", "temporalio"),
    probe_docker_or_compose,
    _probe_import("artifact_backend_init", "moonmind.omnigent.bridge_artifacts"),
    _probe_import("database_init", "sqlalchemy"),
    _probe_import("browser_facing_deps", "httpx"),
)

_WORKER_PROBES: tuple[Callable[[], dict[str, object]], ...] = (
    _probe_import("omnigent_adapters_import", "moonmind.omnigent.execute"),
    _probe_import("opentelemetry_init", "opentelemetry"),
    _probe_import("temporal_client_init", "temporalio"),
    probe_docker_or_compose,
    _probe_import("database_init", "sqlalchemy"),
)

_ROLE_PROBES = {"server": _SERVER_PROBES, "worker": _WORKER_PROBES}


def probe_capabilities(role: str) -> list[dict[str, object]]:
    probes = _ROLE_PROBES.get(role)
    if probes is None:
        raise SystemExit(f"unknown probe role: {role!r}")
    return [probe() for probe in probes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=sorted(_ROLE_PROBES))
    args = parser.parse_args(argv)
    json.dump(probe_capabilities(args.role), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
