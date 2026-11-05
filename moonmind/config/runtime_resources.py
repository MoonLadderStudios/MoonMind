"""Read installed runtime resources without executing repository configuration.

The deployment installs this file and mounts it into its API and worker. The
existing backend parsers own each resource declaration; this envelope provides
one bounded source for those declarations and safe runtime environment literals.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

RUNTIME_RESOURCES_FILE_ENV_KEY = "MOONMIND_RUNTIME_RESOURCES_FILE"
RUNTIME_RESOURCES_DIGEST_ENV_KEY = "MOONMIND_RUNTIME_RESOURCES_DIGEST"
DEFAULT_RUNTIME_RESOURCES_FILE = Path("/run/moonmind/runtime-resources/resources.json")
_MAX_FILE_BYTES = 1024 * 1024
_MAX_ENVIRONMENT_ENTRIES = 128
_ENVELOPE_KEYS = frozenset(
    {
        "schemaVersion",
        "imageSources",
        "cacheSources",
        "serviceConnections",
        "environment",
    }
)


class RuntimeResourcesConfigError(ValueError):
    """An installed runtime resource file cannot be used as configured."""


@dataclass(frozen=True)
class RuntimeResources:
    path: Path | None = None
    image_sources: tuple[Mapping[str, Any], ...] = ()
    cache_sources: tuple[Mapping[str, Any], ...] = ()
    service_connections: tuple[Mapping[str, Any], ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _declarations(
    payload: Mapping[str, Any], key: str, ref_key: str, limit: int
) -> tuple[Mapping[str, Any], ...]:
    rows = payload.get(key, [])
    if not isinstance(rows, list) or len(rows) > limit:
        raise RuntimeResourcesConfigError(
            f"Installed runtime resources require a bounded {key} array."
        )
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeResourcesConfigError(
                f"Installed runtime resources require {key} objects."
            )
        ref = row.get(ref_key)
        if (
            not isinstance(ref, str)
            or not ref.strip()
            or ref != ref.strip()
            or len(ref) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in ref)
            or ref in seen
        ):
            raise RuntimeResourcesConfigError(
                f"Installed runtime resources require unique, nonempty {ref_key} values."
            )
        seen.add(ref)
    return tuple(rows)


def load_runtime_resources(
    env: Mapping[str, str] | None = None,
) -> RuntimeResources:
    """Read one resource snapshot for this API or worker process.

    Compose's activation identity selects a retained package, so a delayed
    process reads the same configuration as the backend that admitted its work.
    Deployment reads share a process snapshot; explicit mappings read fresh for
    package installation and validation. An explicit file overrides activation.
    """

    source = os.environ if env is None else env
    selected = str(source.get(RUNTIME_RESOURCES_FILE_ENV_KEY) or "").strip()
    digest = str(source.get(RUNTIME_RESOURCES_DIGEST_ENV_KEY) or "").strip()
    if digest and not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise RuntimeResourcesConfigError(
            "The installed runtime resources activation identity is invalid."
        )
    if selected:
        path = Path(selected).expanduser()
    elif digest:
        path = (
            DEFAULT_RUNTIME_RESOURCES_FILE.parent
            / "packages"
            / digest.lower()
            / "resources.json"
        )
    else:
        path = DEFAULT_RUNTIME_RESOURCES_FILE
    required = bool(selected or digest)
    if env is None or env is os.environ:
        return _deployment_snapshot(path, required)
    return _read_runtime_resources(path, required)


@cache
def _deployment_snapshot(path: Path, required: bool) -> RuntimeResources:
    return _read_runtime_resources(path, required)


def _read_runtime_resources(path: Path, required: bool) -> RuntimeResources:
    """Read bounded installed data without reflecting file paths or contents."""

    try:
        path = path.resolve(strict=True)
        with path.open("rb") as stream:
            contents = stream.read(_MAX_FILE_BYTES + 1)
        if len(contents) > _MAX_FILE_BYTES:
            raise ValueError("file too large")
        payload = json.loads(contents, object_pairs_hook=_unique_object)
    except FileNotFoundError:
        if not required:
            return RuntimeResources()
        raise RuntimeResourcesConfigError(
            "The configured installed runtime resources file is unavailable or invalid."
        ) from None
    except (OSError, ValueError, RuntimeError):
        raise RuntimeResourcesConfigError(
            "The configured installed runtime resources file is unavailable or invalid."
        ) from None
    if (
        not isinstance(payload, dict)
        or set(payload) - _ENVELOPE_KEYS
        or not isinstance(payload.get("schemaVersion"), int)
        or isinstance(payload.get("schemaVersion"), bool)
        or payload["schemaVersion"] != 1
    ):
        raise RuntimeResourcesConfigError(
            "Installed runtime resources require a version 1 object with supported fields."
        )

    environment = payload.get("environment", {})
    if not isinstance(environment, dict) or len(environment) > _MAX_ENVIRONMENT_ENTRIES:
        raise RuntimeResourcesConfigError(
            "Installed runtime resources require a bounded environment object."
        )
    # Import at use time: runtime environment projection also reads this loader.
    from moonmind.omnigent.host_services.project_environment import (
        is_safe_project_environment_entry,
    )

    if any(
        not is_safe_project_environment_entry(key, value)
        for key, value in environment.items()
    ):
        raise RuntimeResourcesConfigError(
            "Installed runtime resources contain an unsafe environment setting."
        )
    return RuntimeResources(
        path=path,
        image_sources=_declarations(payload, "imageSources", "sourceRef", 128),
        cache_sources=_declarations(payload, "cacheSources", "cacheRef", 128),
        service_connections=_declarations(
            payload, "serviceConnections", "serviceRef", 32
        ),
        environment=dict(environment),
    )


__all__ = [
    "DEFAULT_RUNTIME_RESOURCES_FILE",
    "RUNTIME_RESOURCES_DIGEST_ENV_KEY",
    "RUNTIME_RESOURCES_FILE_ENV_KEY",
    "RuntimeResources",
    "RuntimeResourcesConfigError",
    "load_runtime_resources",
]
