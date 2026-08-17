"""Omnigent UI facade: browser binding, authorization, transport policy.

The UI facade owns caller authorization, capability resolution, virtual-id
binding, route classification, transport policy, and provider forwarding. It must
NOT own canonical session lifecycle transitions — those belong to the domain and
application layers. The FastAPI routers call into this facade; the facade returns
typed decisions the routers serialize.
"""

from moonmind.omnigent.ui_facade.route_contract import (
    RouteClass,
    classify_route,
)

__all__ = ["RouteClass", "classify_route"]
