"""Route classification for the single public Omnigent route contract.

Pure classification of an incoming request path into the facade that should
serve it (provider service, Workflow Chat, host channel, resources, or
WebSocket). This preserves "one public route contract" while allowing
``omnigent_bridge`` to be split into per-facade routers.
"""

from __future__ import annotations

from enum import Enum


class RouteClass(str, Enum):
    PROVIDER_SERVICE = "provider_service"
    WORKFLOW_CHAT = "workflow_chat"
    HOST_CHANNEL = "host_channel"
    RESOURCES = "resources"
    WEBSOCKET = "websocket"
    UNKNOWN = "unknown"


def classify_route(path: str, *, upgrade_websocket: bool = False) -> RouteClass:
    """Classify a request path into its owning facade.

    ``upgrade_websocket`` reflects a transport-level WebSocket upgrade; such
    requests are always the WebSocket facade regardless of path.
    """

    if upgrade_websocket:
        return RouteClass.WEBSOCKET
    normalized = "/" + path.strip("/")
    if normalized.startswith("/chat") or "/workflow-chat" in normalized:
        return RouteClass.WORKFLOW_CHAT
    if "/host" in normalized:
        return RouteClass.HOST_CHANNEL
    if "/resources" in normalized or "/files" in normalized:
        return RouteClass.RESOURCES
    if normalized.startswith("/ws") or normalized.endswith("/ws"):
        return RouteClass.WEBSOCKET
    if normalized.startswith("/v1") or "/sessions" in normalized:
        return RouteClass.PROVIDER_SERVICE
    return RouteClass.UNKNOWN


__all__ = ["RouteClass", "classify_route"]
