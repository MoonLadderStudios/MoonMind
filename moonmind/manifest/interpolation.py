from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from moonmind.schemas import AuthItem, Manifest

from .secret_providers import (
    EnvSecretProvider,
    ProfileSecretProvider,
    SecretProviderManager,
)


class InterpolationError(Exception):
    """Raised when an interpolation reference cannot be resolved."""


_PATTERN = re.compile(r"^\$\{([^}]+)\}$")
_ALLOWED_ROOTS = {"auth", "defaults", "env"}


def interpolate(
    model: Manifest,
    env: Mapping[str, str],
    profile: Optional[Mapping[str, str]] = None,
) -> Manifest:
    """Return a new Manifest with ${...} references resolved."""

    manager = SecretProviderManager(
        profile_provider=ProfileSecretProvider(profile or {}),
        env_provider=EnvSecretProvider(env),
    )

    data = model.model_dump()
    resolved = _apply(data, model, env, manager)
    return Manifest.model_validate(resolved)


def _apply(
    value: Any,
    model: Manifest,
    env: Mapping[str, str],
    manager: SecretProviderManager,
) -> Any:
    if isinstance(value, str):
        match = _PATTERN.fullmatch(value)
        if match:
            return _resolve(match.group(1), model, env, manager)
        return value
    if isinstance(value, list):
        return [_apply(v, model, env, manager) for v in value]
    if isinstance(value, dict):
        return {k: _apply(v, model, env, manager) for k, v in value.items()}
    return value


def _resolve(
    path: str,
    model: Manifest,
    env: Mapping[str, str],
    manager: SecretProviderManager,
) -> Any:
    parts = path.split(".")
    if not parts:
        raise InterpolationError("empty interpolation path")

    root = parts.pop(0)
    if root not in _ALLOWED_ROOTS:
        raise InterpolationError(f"invalid root '{root}' in '{path}'")

    if root == "env":
        if not parts:
            raise InterpolationError(f"invalid env reference '{path}'")
        key = parts[0]
        if key in env:
            return env[key]
        raise InterpolationError(f"env variable not found: {key}")

    if root == "defaults":
        cur: Any = model.spec.defaults.model_dump() if model.spec.defaults else {}
        for part in parts:
            if isinstance(cur, Mapping) and part in cur:
                cur = cur[part]
            else:
                raise InterpolationError(f"unresolved path '{path}'")
        return cur

    # root == "auth"
    if not parts:
        raise InterpolationError(f"invalid auth reference '{path}'")
    key = parts.pop(0)
    item = model.spec.auth.get(key)
    if item is None:
        raise InterpolationError(f"unknown auth key '{key}'")
    cur = _resolve_auth_item(item, manager)
    for part in parts:
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            raise InterpolationError(f"unresolved path '{path}'")
    return cur


def _resolve_auth_item(item: AuthItem, manager: SecretProviderManager) -> Any:
    if item.value is not None:
        return item.value

    provider = item.secretRef.provider
    key = item.secretRef.key
    try:
        return manager.get_secret(provider, key)
    except ValueError as exc:
        raise InterpolationError(str(exc)) from exc
