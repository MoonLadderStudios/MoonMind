"""Guard the ASGI WebSocket serving capability the API service depends on.

MoonMind serves several WebSocket endpoints through the uvicorn process started
by ``api_service/entrypoint.sh`` (OAuth provider login terminals, agent live
terminals, and the Omnigent host/runner tunnels). uvicorn only speaks the
WebSocket protocol when a WebSocket library is importable; without one it logs
"No supported WebSocket library detected", declines the upgrade, and lets the
handshake fall through to the HTTP router, which answers ``404 Not Found``.

That failure is invisible to route-level tests because every route stays
correctly registered, so this module pins the dependency itself. ``websockets``
was previously present only as a transitive dependency of an unrelated package;
removing that package silently disabled every WebSocket endpoint.

The declaration and resolution assertions cannot see a lock combination that
imports but cannot upgrade, so this module also replays the incident against a
real uvicorn server: it drives the production ``ws="auto"`` launch config
through an actual handshake, and pins the ``ws="none"`` variant to the exact
404 route-miss the outage produced.
"""

import contextlib
import socket
import threading
import time
from pathlib import Path

import pytest
import toml
import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket
from uvicorn.config import WS_PROTOCOLS
from uvicorn.importer import import_from_string
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
ENTRYPOINT_PATH = REPO_ROOT / "api_service" / "entrypoint.sh"

TERMINAL_PATH = "/api/v1/oauth-sessions/oas-replay/terminal/ws"
STARTUP_TIMEOUT_SECONDS = 15.0
HANDSHAKE_TIMEOUT_SECONDS = 15.0


def test_websockets_is_a_declared_main_dependency() -> None:
    """The WebSocket library must be declared, not inherited transitively."""
    # `toml` rather than `tomllib`: pyproject declares python >=3.10 and
    # tomllib only exists from 3.11, so importing it would break collection of
    # this module on a supported interpreter.
    pyproject = toml.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = pyproject["tool"]["poetry"]["dependencies"]

    assert "websockets" in dependencies, (
        "websockets must stay an explicit main dependency: uvicorn needs it to "
        "serve MoonMind's WebSocket endpoints, and relying on another package "
        "to pull it in lets routine dependency cleanups disable them silently."
    )


def test_production_uvicorn_resolves_a_websocket_protocol() -> None:
    """The launch config used in production must resolve a WebSocket protocol."""
    # api_service/entrypoint.sh passes no --ws flag, so uvicorn uses ws="auto".
    assert "--ws" not in ENTRYPOINT_PATH.read_text(encoding="utf-8"), (
        "entrypoint.sh now sets --ws explicitly; update this test to resolve the "
        "configured value instead of uvicorn's auto-detection default."
    )

    # Mirrors uvicorn.Config.load(), which resolves the protocol class this way.
    # Resolving it directly keeps the assertion hermetic: Config.load() would
    # import the full application just to reach the same lookup.
    protocol_class = import_from_string(WS_PROTOCOLS["auto"])

    assert protocol_class is not None, (
        "uvicorn found no WebSocket library, so it will decline upgrade "
        "requests and every MoonMind WebSocket endpoint will answer 404 when "
        "a client tries to open a terminal or tunnel."
    )


async def _terminal_endpoint(websocket: WebSocket) -> None:
    """Stand in for the terminal/tunnel endpoints the outage made unreachable."""
    await websocket.accept()
    await websocket.send_text("connected")
    await websocket.close()


def _replay_app() -> Starlette:
    """An app whose only route is a WebSocket route, as in the outage.

    The endpoint being registered is exactly what made the incident hard to
    see: routing was never the problem, so any HTTP request that reaches the
    router at this path is a missed upgrade and answers 404.
    """
    return Starlette(routes=[WebSocketRoute(TERMINAL_PATH, _terminal_endpoint)])


@contextlib.contextmanager
def _serving(ws: str):
    """Run a real uvicorn server on an ephemeral port and yield its URL."""
    server = uvicorn.Server(
        uvicorn.Config(
            _replay_app(),
            host="127.0.0.1",
            port=0,
            ws=ws,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError(f"uvicorn exited before serving with ws={ws!r}")
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"uvicorn did not start within {STARTUP_TIMEOUT_SECONDS}s"
                )
            time.sleep(0.02)
        sockets: list[socket.socket] = server.servers[0].sockets
        yield f"ws://127.0.0.1:{sockets[0].getsockname()[1]}{TERMINAL_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=STARTUP_TIMEOUT_SECONDS)


def test_production_launch_config_completes_a_websocket_upgrade() -> None:
    """A real handshake must reach the registered endpoint, not just resolve."""
    # ws="auto" is what api_service/entrypoint.sh gets by passing no --ws flag.
    with _serving("auto") as url:
        with connect(url, open_timeout=HANDSHAKE_TIMEOUT_SECONDS) as client:
            message = client.recv(timeout=HANDSHAKE_TIMEOUT_SECONDS)

    assert message == "connected", (
        "The upgrade completed but did not reach the registered endpoint, so "
        "MoonMind's terminals and tunnels would open without ever receiving "
        "server output."
    )


def test_missing_websocket_protocol_reproduces_the_404_route_miss() -> None:
    """Pin the outage signature so a silent regression is recognizable."""
    # ws="none" is how uvicorn behaves when no WebSocket library is importable:
    # the upgrade is never handled, the handshake falls through to the HTTP
    # router, and the registered WebSocket route cannot answer it.
    with _serving("none") as url:
        with pytest.raises(InvalidStatus) as excinfo:
            connect(url, open_timeout=HANDSHAKE_TIMEOUT_SECONDS)

    assert excinfo.value.response.status_code == 404, (
        "Expected the outage's route-miss signature; a different rejection "
        "means this replay no longer reproduces the incident."
    )
