"""Deployment-owned, non-secret project settings for generic runtime shells.

This is not a credential channel. The declaration and values must both exist in
this trusted worker's environment; a workflow cannot supply or override them.
An admitted Provider Profile can supply its own safe literal for a native child;
that explicit value has the same precedence as direct Profile materialization.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from moonmind.provider_profiles.template_renderer import is_safe_project_environment_entry

_SETTING = "MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS"
_LOG = logging.getLogger(__name__)


def deployment_project_environment() -> dict[str, str]:
    """Resolve only explicitly declared safe settings, never logging values."""
    from moonmind.config.runtime_resources import load_runtime_resources

    result: dict[str, str] = dict(load_runtime_resources().environment)
    for candidate in os.environ.get(_SETTING, "").split(","):
        key = candidate.strip()
        if not key:
            continue
        raw_value = os.environ.get(key, "")
        value = raw_value.strip()
        if not is_safe_project_environment_entry(key, raw_value):
            _LOG.warning("Ignoring unsafe or empty declared project environment entry")
            continue
        result[key] = value
    return result


def approved_project_environment(
    supplied: Mapping[str, str], *, reserved: set[str]
) -> dict[str, str]:
    """Validate additional names and exact values at each launch boundary."""
    extras = {key: value for key, value in supplied.items() if key not in reserved}
    trusted = deployment_project_environment()
    if any(
        key not in trusted or value != trusted[key] for key, value in extras.items()
    ):
        raise ValueError("unsupported deployment project environment")
    return extras
