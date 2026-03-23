"""Logging utilities including simple secret redaction helpers."""

from __future__ import annotations

import os
import re
from base64 import b64encode
from typing import Iterable, Sequence
from urllib.parse import quote_plus

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:token|secret|password|key|credential|auth)(?:$|[^a-z0-9])"
)
_GITHUB_TOKEN_PATTERN = re.compile(
    r"(?:ghp|gho|ghu|ghs|ghr|github_pat)[_-][A-Za-z0-9_-]{20,}",
    re.IGNORECASE,
)
_NON_SECRET_SENTINEL_VALUES = frozenset(
    {"true", "false", "none", "null", "yes", "no", "on", "off"}
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


__all__ = ["SecretRedactor", "scrub_github_tokens"]
