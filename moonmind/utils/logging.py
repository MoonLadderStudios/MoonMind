"""Logging utilities including simple secret redaction helpers."""

from __future__ import annotations

import os
import re
from base64 import b64encode
from urllib.parse import quote_plus
from typing import Iterable, Sequence


_SENSITIVE_KEYS = ("token", "secret", "password", "key", "credential", "auth")
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:token|secret|password|key|credential|auth)(?:$|[^a-z0-9])"
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


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


class SecretRedactor:
    """Utility to scrub sensitive values from log output.

    The redactor performs straightforward string replacement against known secret
    values; it is intentionally lightweight so it can be applied to orchestrator
    artifacts without adding heavy dependencies.
    """

    def __init__(self, secrets: Iterable[str] | None = None, placeholder: str = "***") -> None:
        self._placeholder = placeholder
        seen: set[str] = set()
        unique_secrets: list[str] = []
        for value in secrets or []:
            if not value:
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


__all__ = ["SecretRedactor"]
