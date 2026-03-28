"""Secret reference resolution helpers for codex worker task auth."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

_VAULT_MOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_VAULT_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_VAULT_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class SecretReferenceError(RuntimeError):
    """Raised when secret references are invalid or cannot be resolved safely."""


class SecretMissingError(SecretReferenceError):
    """Raised when a secret reference points to a resource that does not exist or is empty."""


class SecretAccessDeniedError(SecretReferenceError):
    """Raised when the process or resolver lacks permission to read the secret."""


class SecretUnsupportedBackendError(SecretReferenceError):
    """Raised when the requested backend is not supported or not configured."""


class SecretDecryptionError(SecretReferenceError):
    """Raised when an encrypted secret cannot be securely decrypted."""


class SecretBackend(str, Enum):
    ENV = "env"
    DB_ENCRYPTED = "db"
    EXEC = "exec"
    VAULT = "vault"


@dataclass(frozen=True, slots=True)
class ParsedSecretRef:
    """Normalized general secret reference."""

    backend: SecretBackend
    locator: str
    normalized_ref: str


def parse_secret_ref(ref: str) -> ParsedSecretRef:
    """Parse and validate ``<backend>://<locator>`` reference values."""

    candidate = str(ref or "").strip()
    if not candidate:
        raise SecretReferenceError("secret reference is required")
    if len(candidate) > 512:
        raise SecretReferenceError("secret reference exceeds max length")

    if "://" not in candidate:
        raise SecretReferenceError("secret reference must use <backend>://<locator> format")

    scheme, locator = candidate.split("://", 1)
    scheme = scheme.lower()

    if not locator:
        raise SecretReferenceError("secret reference locator cannot be empty")

    try:
        backend = SecretBackend(scheme)
    except ValueError:
        raise SecretReferenceError(f"unsupported secret backend: {scheme}")

    if backend == SecretBackend.ENV:
        if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", locator):
            raise SecretReferenceError("invalid env locator format")
    elif backend == SecretBackend.DB_ENCRYPTED:
        if not re.fullmatch(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$", locator):
            raise SecretReferenceError("invalid db locator format")
    elif backend == SecretBackend.EXEC:
        command = locator.split("?", 1)[0]
        if not command or not re.fullmatch(r"^[a-zA-Z0-9_.-]+$", command):
            raise SecretReferenceError(
                "invalid exec locator format: command contains invalid characters"
            )
    elif backend == SecretBackend.VAULT:
        parse_vault_reference(candidate, allowed_mounts=())

    return ParsedSecretRef(
        backend=backend,
        locator=locator,
        normalized_ref=f"{scheme}://{locator}",
    )


@dataclass(frozen=True, slots=True)
class ResolvedGitHubAuth:
    """Resolved GitHub auth material from Vault secret data."""

    token: str
    username: str
    host: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class ParsedVaultReference:
    """Normalized Vault KV-v2 secret reference."""

    mount: str
    path: str
    field: str
    normalized_ref: str


def parse_vault_reference(
    ref: str,
    *,
    allowed_mounts: tuple[str, ...] = ("kv",),
) -> ParsedVaultReference:
    """Parse and validate ``vault://<mount>/<path>#<field>`` reference values."""

    candidate = str(ref or "").strip()
    if not candidate:
        raise SecretReferenceError("secret reference is required")
    if len(candidate) > 512:
        raise SecretReferenceError("secret reference exceeds max length")

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "vault":
        raise SecretReferenceError("secret reference must use vault:// scheme")

    mount = parsed.netloc.strip()
    path = parsed.path.lstrip("/").strip()
    field = parsed.fragment.strip()
    if not mount or not path or not field:
        raise SecretReferenceError(
            "secret reference must include mount/path and #field"
        )
    if not _VAULT_MOUNT_PATTERN.fullmatch(mount):
        raise SecretReferenceError("vault mount contains invalid characters")
    if not _VAULT_PATH_PATTERN.fullmatch(path):
        raise SecretReferenceError("vault path contains invalid characters")
    if any(segment in {"..", "."} for segment in path.split("/")):
        raise SecretReferenceError("vault path traversal is not allowed")
    if not _VAULT_FIELD_PATTERN.fullmatch(field):
        raise SecretReferenceError("vault field contains invalid characters")

    mounts = tuple(item for item in allowed_mounts if item)
    if mounts and mount not in mounts:
        allowlist = ", ".join(mounts)
        raise SecretReferenceError(
            f"vault mount '{mount}' is not allowed; allowed mounts: {allowlist}"
        )

    normalized_ref = f"vault://{mount}/{path}#{field}"
    return ParsedVaultReference(
        mount=mount,
        path=path,
        field=field,
        normalized_ref=normalized_ref,
    )


def load_vault_token(*, token: str | None, token_file: Path | None) -> str | None:
    """Resolve Vault token from explicit value or file path."""

    direct = str(token or "").strip()
    if direct:
        return direct
    if token_file is None:
        return None
    try:
        candidate = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:  # pragma: no cover - defensive path
        raise SecretReferenceError(f"unable to read Vault token file: {exc}") from exc
    return candidate or None


class VaultSecretResolver:
    """Resolve GitHub auth material from Vault KV-v2 references."""

    def __init__(
        self,
        *,
        address: str,
        token: str,
        namespace: str | None = None,
        allowed_mounts: tuple[str, ...] = ("kv",),
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        addr = str(address or "").strip().rstrip("/")
        tok = str(token or "").strip()
        if not addr:
            raise SecretReferenceError(
                "Vault address is required for secret resolution"
            )
        if not tok:
            raise SecretReferenceError("Vault token is required for secret resolution")
        self._address = addr
        self._token = tok
        self._namespace = str(namespace or "").strip() or None
        self._allowed_mounts = tuple(item for item in allowed_mounts if item)
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close owned async client resources."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def resolve_github_auth(self, ref: str) -> ResolvedGitHubAuth:
        """Resolve ``token`` plus optional ``username``/``host`` from Vault."""

        parsed = parse_vault_reference(ref, allowed_mounts=self._allowed_mounts)
        secret_data = await self._read_secret(parsed)

        token_raw = secret_data.get(parsed.field)
        token = str(token_raw or "").strip()
        if not token:
            raise SecretReferenceError(
                f"vault field '{parsed.field}' is missing or empty for {parsed.normalized_ref}"
            )
        username = str(secret_data.get("username") or "x-access-token").strip()
        host = str(secret_data.get("host") or "github.com").strip()
        return ResolvedGitHubAuth(
            token=token,
            username=username or "x-access-token",
            host=host or "github.com",
            source_ref=parsed.normalized_ref,
        )

    async def _read_secret(
        self,
        ref: ParsedVaultReference,
    ) -> Mapping[str, Any]:
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout_seconds)
            self._client = client
            self._owns_client = True

        headers = {"X-Vault-Token": self._token}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        url = f"{self._address}/v1/{ref.mount}/data/{ref.path}"
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SecretReferenceError(
                f"vault secret read failed for {ref.normalized_ref}: {exc}"
            ) from exc

        payload = response.json()
        if not isinstance(payload, Mapping):
            raise SecretReferenceError("vault response payload must be an object")
        root_data = payload.get("data")
        if not isinstance(root_data, Mapping):
            raise SecretReferenceError("vault response missing data object")
        secret_data = root_data.get("data")
        if not isinstance(secret_data, Mapping):
            raise SecretReferenceError("vault response missing kv-v2 data object")
        return secret_data

    async def resolve_plain_kv_value(self, ref: str) -> str:
        """Read a single string field from a ``vault://`` KV-v2 reference."""

        parsed = parse_vault_reference(ref, allowed_mounts=self._allowed_mounts)
        secret_data = await self._read_secret(parsed)
        raw = secret_data.get(parsed.field)
        value = str(raw or "").strip()
        if not value:
            raise SecretReferenceError(
                f"vault field '{parsed.field}' is missing or empty for {parsed.normalized_ref}"
            )
        return value


__all__ = [
    "ParsedVaultReference",
    "ResolvedGitHubAuth",
    "SecretReferenceError",
    "SecretMissingError",
    "SecretAccessDeniedError",
    "SecretUnsupportedBackendError",
    "SecretDecryptionError",
    "VaultSecretResolver",
    "load_vault_token",
    "parse_vault_reference",
]

