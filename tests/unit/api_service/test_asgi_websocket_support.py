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
"""

import tomllib
from pathlib import Path

from uvicorn.config import WS_PROTOCOLS
from uvicorn.importer import import_from_string

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
ENTRYPOINT_PATH = REPO_ROOT / "api_service" / "entrypoint.sh"


def test_websockets_is_a_declared_main_dependency() -> None:
    """The WebSocket library must be declared, not inherited transitively."""
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)
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
