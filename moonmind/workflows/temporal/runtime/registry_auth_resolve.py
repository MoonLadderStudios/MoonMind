"""Generic execution-time registry credential resolution (MoonMind#3257).

This module generalizes the managed-session-specific GHCR pull helper into a
backend-neutral resolver for container jobs. It resolves a non-sensitive
credential *reference* into plaintext registry authentication material through
the repository's existing managed-secret backends (``env``/``db``/``exec``/
``vault`` via :func:`resolve_managed_api_key_reference`) rather than duplicating
secret handling.

The returned plaintext is for immediate, single-operation Docker auth
materialization only. Callers must not store it in workflow history, durable job
records, logs, labels, command lines, or diagnostics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
)


class RegistryAuthResolutionError(RuntimeError):
    """Raised when a registry credential reference cannot be resolved.

    The message is intentionally metadata-only: it never includes the resolved
    username, token, password, or the raw secret value.
    """


@dataclass(frozen=True)
class RegistryCredential:
    """Resolved registry authentication material for one Docker operation."""

    username: str
    secret: str

    def docker_auth_entry(self) -> dict[str, str]:
        """Return a Docker ``auths`` entry value for this credential.

        Docker accepts either ``username``/``password`` or a base64 ``auth``
        token; the plaintext pair is materialized by the caller into a
        restricted, per-job config directory and removed immediately after use.
        """

        return {"username": self.username, "password": self.secret}


def _parse_resolved_credential(resolved: str) -> RegistryCredential:
    """Parse a resolved secret value into a username/secret pair.

    Two portable encodings are supported so operators can reuse existing secret
    backends without a MoonMind-specific format:

    - a JSON object with ``username`` and ``password`` (or ``token``);
    - a ``username:secret`` string (the secret may itself contain colons).
    """

    text = str(resolved or "").strip()
    if not text:
        raise RegistryAuthResolutionError(
            "registry credential reference resolved to an empty value"
        )

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistryAuthResolutionError(
                "registry credential JSON could not be parsed"
            ) from exc
        if not isinstance(payload, dict):
            raise RegistryAuthResolutionError(
                "registry credential JSON must be an object"
            )
        username = str(payload.get("username") or "").strip()
        secret = str(
            payload.get("password") or payload.get("token") or ""
        ).strip()
        if not username or not secret:
            raise RegistryAuthResolutionError(
                "registry credential JSON must contain username and password/token"
            )
        return RegistryCredential(username=username, secret=secret)

    if ":" not in text:
        raise RegistryAuthResolutionError(
            "registry credential must be a 'username:secret' pair or a JSON object"
        )
    username, _, secret = text.partition(":")
    username = username.strip()
    secret = secret.strip()
    if not username or not secret:
        raise RegistryAuthResolutionError(
            "registry credential must contain a non-empty username and secret"
        )
    return RegistryCredential(username=username, secret=secret)


async def resolve_registry_pull_credentials(
    credential_ref: str,
) -> RegistryCredential:
    """Resolve a credential reference into registry auth material.

    Resolution runs at execution time only, immediately before the authorized
    inspect/pull operation. Raises :class:`RegistryAuthResolutionError` on any
    unresolved, empty, or malformed credential.
    """

    reference = str(credential_ref or "").strip()
    if not reference:
        raise RegistryAuthResolutionError(
            "registry credential reference is empty"
        )
    try:
        resolved = await resolve_managed_api_key_reference(
            reference, field_name="registryCredentialRef"
        )
    except Exception as exc:  # noqa: BLE001 - normalize to a metadata-only error
        raise RegistryAuthResolutionError(
            "registry credential reference could not be resolved"
        ) from exc
    return _parse_resolved_credential(resolved)


__all__ = [
    "RegistryAuthResolutionError",
    "RegistryCredential",
    "resolve_registry_pull_credentials",
]
