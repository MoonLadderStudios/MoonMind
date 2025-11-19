"""Logging utilities including simple secret redaction helpers."""

from __future__ import annotations

import os
from typing import Iterable, Sequence


_SENSITIVE_KEYS = ("token", "secret", "password", "key", "credential", "auth")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEYS)


class SecretRedactor:
    """Utility to scrub sensitive values from log output.

    The redactor performs straightforward string replacement against known secret
    values; it is intentionally lightweight so it can be applied to orchestrator
    artifacts without adding heavy dependencies.
    """

    def __init__(self, secrets: Iterable[str] | None = None, placeholder: str = "***") -> None:
        self._placeholder = placeholder
        seen: set[str] = set()
        self._secrets: list[str] = []
        for value in secrets or []:
            if value and value not in seen:
                seen.add(value)
                self._secrets.append(value)

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
