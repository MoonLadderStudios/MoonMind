"""Transport policy for HTTP provider forwarding.

Decides whether an inbound request may be forwarded to the provider and which
upstream path/headers to use. Pure policy: it produces a forwarding decision the
router executes; it performs no I/O and mutates no session state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from moonmind.omnigent.ui_facade.route_contract import RouteClass, classify_route

# Route classes the HTTP proxy may forward to the provider service.
_FORWARDABLE = frozenset(
    {RouteClass.PROVIDER_SERVICE, RouteClass.HOST_CHANNEL, RouteClass.RESOURCES}
)


@dataclass(frozen=True, slots=True)
class ForwardDecision:
    forward: bool
    route_class: RouteClass
    reason: str
    headers: Mapping[str, str] = field(default_factory=dict)


def decide_forward(
    path: str, *, forward_headers: Mapping[str, str] | None = None
) -> ForwardDecision:
    route_class = classify_route(path)
    if route_class in _FORWARDABLE:
        return ForwardDecision(
            forward=True,
            route_class=route_class,
            reason="forwardable",
            headers=dict(forward_headers or {}),
        )
    return ForwardDecision(
        forward=False, route_class=route_class, reason="not_forwardable"
    )


__all__ = ["ForwardDecision", "decide_forward"]
