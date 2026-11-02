"""Deployment-owned, non-secret project settings for generic runtime shells.

This is not a credential channel. The declaration and values must both exist in
this trusted worker's environment; a workflow cannot supply or override them.
An admitted Provider Profile can supply its own safe literal for a native child;
that explicit value has the same precedence as direct Profile materialization.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from moonmind.schemas.container_job_models import SENSITIVE_ENVIRONMENT_KEY_PATTERN

_SETTING = "MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PROTECTED = re.compile(
    r"^(?:MOONMIND|OMNIGENT|LD|DYLD|PYTHON|NODE|NPM|BASH|SHELL|ENV|BASH_ENV|"
    r"GIT|GH|SSH|SSL|CURL|REQUESTS|AWS|AZURE|GOOGLE|DOCKER|CONTAINER|"
    r"RUBY|PERL|JAVA|JDK|JRE|PHP|LUA|RUST|CARGO|GODEBUG|GOTOOLCHAIN|"
    r"OPENCODE|CLAUDE|CODEX|OPENAI|ANTHROPIC)|"
    r"^(?:PATH|HOME|USER|LOGNAME|PWD|OLDPWD|IFS|CDPATH|SHELLOPTS|BASHOPTS|"
    r"PYTHONPATH|PYTHONHOME|NODE_OPTIONS|TMPDIR|TMP|TEMP|RES_OPTIONS|LOCALDOMAIN|"
    r"HOSTALIASES|HOSTNAME|TZDIR|LOCPATH|NLSPATH|TERMINFO|TERMINFO_DIRS)$|PROXY$",
    re.IGNORECASE,
)
_SECRET_KEY = re.compile(
    r"(?:private|signing|encryption|access)[_-]?key", re.IGNORECASE
)
_LOG = logging.getLogger(__name__)


def is_safe_project_environment_entry(key: str, raw_value: str) -> bool:
    """Whether one literal can use the nonsecret runtime projection."""
    if not isinstance(raw_value, str) or not isinstance(key, str):
        return False
    value = raw_value.strip()
    safe = bool(_NAME.fullmatch(key)) and not (
        _PROTECTED.search(key)
        or SENSITIVE_ENVIRONMENT_KEY_PATTERN.search(key)
        or _SECRET_KEY.search(key)
    )
    safe = safe and bool(value) and not any(
        ord(c) < 32 or ord(c) == 127 for c in raw_value
    )
    if safe and "://" in value:
        try:
            parsed = urlsplit(value)
            safe = not ("@" in parsed.netloc or parsed.query or parsed.fragment)
        except ValueError:
            return False
    return bool(safe)


def deployment_project_environment() -> dict[str, str]:
    """Resolve only explicitly declared safe settings, never logging values."""
    result: dict[str, str] = {}
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
