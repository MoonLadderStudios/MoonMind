"""Logging utilities including simple secret redaction helpers."""

from __future__ import annotations

import os
import re
from base64 import b64encode
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote_plus

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:token|secret|password|key|credential|auth)(?:$|[^a-z0-9])"
)
_GITHUB_TOKEN_PATTERN = re.compile(
    r"(?:ghp|gho|ghu|ghs|ghr|github_pat)[_-][A-Za-z0-9_-]{20,}",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key|credential)\s*[:=]\s*([^\s,;\"']+)"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]+"
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_PATH_PATTERN = re.compile(
    r"(?i)(?:/[^\s\"']*)?(?:\.codex|\.claude|codex-auth|claude-auth|gemini-auth|auth-volume)[^\s\"']*"
)
_NON_SECRET_SENTINEL_VALUES = frozenset(
    {"true", "false", "none", "null", "yes", "no", "on", "off"}
)
_NON_SECRET_REF_KEYS = frozenset(
    {
        "auth_actions",
        "authactions",
        "auth_readiness",
        "authreadiness",
        "auth_status_label",
        "authstatuslabel",
        "auth_state",
        "authstate",
        "auth_strategy",
        "authstrategy",
        "credential_source",
        "credentialsource",
        "runtime_materialization_mode",
        "runtimematerializationmode",
    }
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


def _is_non_secret_sentinel(value: str) -> bool:
    return value.strip().casefold() in _NON_SECRET_SENTINEL_VALUES


def _secret_variants(secret: str) -> set[str]:
    variants = {secret}
    try:
        variants.add(b64encode(secret.encode()).decode())
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        variants.add(quote_plus(secret))
    except Exception:  # pragma: no cover - defensive
        pass
    return variants


def scrub_github_tokens(text: str) -> str:
    """Scrub common GitHub token-like values from diagnostic text."""

    if not text:
        return ""
    return _GITHUB_TOKEN_PATTERN.sub("[REDACTED]", text)


def redact_sensitive_text(text: str | None) -> str:
    """Scrub common credential-shaped values from browser/artifact text."""

    if not text:
        return ""
    redacted = scrub_github_tokens(str(text))
    redacted = _PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", redacted)
    redacted = _AUTHORIZATION_PATTERN.sub("[REDACTED_AUTHORIZATION]", redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[REDACTED]", redacted)
    redacted = _AUTH_PATH_PATTERN.sub("[REDACTED_AUTH_PATH]", redacted)
    return redacted


def redact_sensitive_payload(payload: Any, *, key: str | None = None) -> Any:
    """Recursively redact sensitive values while preserving compact refs."""

    if payload is None:
        return None
    if isinstance(payload, str):
        normalized_key = str(key or "").strip().lower()
        normalized_key_compact = normalized_key.replace("_", "")
        if (
            normalized_key in _NON_SECRET_REF_KEYS
            or normalized_key_compact in _NON_SECRET_REF_KEYS
        ):
            return payload
        if normalized_key.endswith("_ref") or normalized_key.endswith("ref"):
            return payload
        if _is_sensitive_key(normalized_key):
            if payload.startswith(("env://", "secret://", "vault://", "ref://")):
                return payload
            return "[REDACTED]"
        return redact_sensitive_text(payload)
    if isinstance(payload, Mapping):
        return {
            str(nested_key): redact_sensitive_payload(
                nested_value,
                key=str(nested_key),
            )
            for nested_key, nested_value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_sensitive_payload(item, key=key) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_sensitive_payload(item, key=key) for item in payload)
    return payload


def redact_profile_file_templates(value: list[Any]) -> list[Any]:
    """Redact provider-profile file templates without exposing template bodies."""

    redacted = redact_sensitive_payload(value)
    if not isinstance(redacted, list):
        return []

    templates: list[Any] = []
    for item in redacted:
        if not isinstance(item, Mapping):
            templates.append(item)
            continue
        template = dict(item)
        for content_key in ("content", "contentTemplate", "content_template"):
            if content_key in template:
                template[content_key] = redact_sensitive_payload(
                    template[content_key], key="secret"
                )
        templates.append(template)
    return templates


class SecretRedactor:
    """Utility to scrub sensitive values from log output.

    The redactor performs straightforward string replacement against known secret
    values; it is intentionally lightweight so it can be applied to temporal worker workflows
    artifacts without adding heavy dependencies.
    """

    def __init__(
        self, secrets: Iterable[str] | None = None, placeholder: str = "***"
    ) -> None:
        self._placeholder = placeholder
        seen: set[str] = set()
        unique_secrets: list[str] = []
        for value in secrets or []:
            if not value:
                continue
            if _is_non_secret_sentinel(value):
                continue
            for variant in _secret_variants(value):
                if variant and variant not in seen:
                    seen.add(variant)
                    unique_secrets.append(variant)
        self._secrets: list[str] = sorted(unique_secrets, key=len, reverse=True)

    @classmethod
    def from_environ(
        cls, *, placeholder: str = "***", extra_secrets: Iterable[str] | None = None
    ) -> "SecretRedactor":
        secrets = []
        for key, value in os.environ.items():
            if _is_sensitive_key(key) and value:
                secrets.append(value)
        if extra_secrets:
            secrets.extend(secret for secret in extra_secrets if secret)
        return cls(secrets=secrets, placeholder=placeholder)

    def scrub(self, text: str | None) -> str:
        if not text:
            return ""
        scrubbed = text
        for secret in self._secrets:
            scrubbed = scrubbed.replace(secret, self._placeholder)
        return scrubbed

    def scrub_sequence(self, values: Sequence[str]) -> list[str]:
        return [self.scrub(value) for value in values]


__all__ = [
    "SecretRedactor",
    "redact_profile_file_templates",
    "redact_sensitive_payload",
    "redact_sensitive_text",
    "scrub_github_tokens",
]
