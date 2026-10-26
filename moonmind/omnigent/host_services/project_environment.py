"""Deployment-owned, non-secret project settings for generic runtime shells.

This is not a credential channel. The declaration and values must both exist in
this trusted worker's environment; a workflow cannot supply or override them.
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


def deployment_project_environment() -> dict[str, str]:
    """Resolve only explicitly declared safe settings, never logging values."""
    result: dict[str, str] = {}
    for candidate in os.environ.get(_SETTING, "").split(","):
        key = candidate.strip()
        if not key:
            continue
        raw_value = os.environ.get(key, "")
        value = raw_value.strip()
        safe = bool(_NAME.fullmatch(key)) and not (
            _PROTECTED.search(key)
            or SENSITIVE_ENVIRONMENT_KEY_PATTERN.search(key)
            or _SECRET_KEY.search(key)
        )
        safe = (
            safe
            and bool(value)
            and not any(ord(c) < 32 or ord(c) == 127 for c in raw_value)
        )
        if safe and "://" in value:
            try:
                parsed = urlsplit(value)
                # URL credentials, signed queries and fragments do not belong in
                # the ordinary shell environment even under an innocuous name.
                safe = not ("@" in parsed.netloc or parsed.query or parsed.fragment)
            except ValueError:
                safe = False
        if not safe:
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
