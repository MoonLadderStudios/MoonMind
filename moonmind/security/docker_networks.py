"""Canonical Docker network identities shared across runtime boundaries."""

from __future__ import annotations

import os
from collections.abc import Mapping

CONTROL_PLANE_NETWORK_ENV = "MOONMIND_CONTROL_PLANE_NETWORK"
DEFAULT_CONTROL_PLANE_NETWORK = "moonmind_control-plane-network"


def resolve_control_plane_network(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the deployment's stable Docker-level control-plane network name."""

    source = os.environ if environment is None else environment
    configured = str(source.get(CONTROL_PLANE_NETWORK_ENV) or "").strip()
    return configured or DEFAULT_CONTROL_PLANE_NETWORK
