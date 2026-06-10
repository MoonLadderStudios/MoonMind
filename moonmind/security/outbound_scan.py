"""Reusable outbound scan contract for side-effect callers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.config.settings import SecuritySettings
from moonmind.utils.logging import redact_sensitive_text


class OutboundScanDecision(StrEnum):
    """Allow/block decision returned before an external side effect."""

    ALLOW = "allow"
    BLOCK = "block"


class OutboundFinding(BaseModel):
    """Sanitized finding metadata for outbound scan diagnostics."""

    category: str = Field(min_length=1)
    location: str = Field(min_length=1)
    redacted_preview: str = Field(alias="redactedPreview", min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class OutboundBundleItem(BaseModel):
    """One text-bearing item from a commit-like outbound payload bundle."""

    location: str = Field(default="", validate_default=True)
    content: str = ""

    @field_validator("location", mode="after")
    @classmethod
    def _normalize_location(cls, value: str) -> str:
        normalized = str(value or "").strip()
        return normalized or "bundle.item"


class OutboundScanResult(BaseModel):
    """Structured scan decision for outbound side-effect callers."""

    allowed: bool
    decision: Literal["allow", "block"]
    high_security_mode: bool = Field(alias="highSecurityMode")
    findings: list[OutboundFinding] = Field(default_factory=list)
    sanitized_diagnostics: list[str] = Field(
        default_factory=list,
        alias="sanitizedDiagnostics",
    )
    original_content: str | None = Field(default=None, alias="originalContent")
    original_bundle: list[OutboundBundleItem] | None = Field(
        default=None,
        alias="originalBundle",
    )

    model_config = ConfigDict(populate_by_name=True)


_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:token|password|secret|api[_-]?key|credential)\s*[:=]\s*"
    r"[^\s,;\"']+"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]+"
)
_GITHUB_TOKEN_PATTERN = re.compile(
    r"(?i)\b(?:ghp|gho|ghu|ghs|ghr|github_pat)[_-][A-Za-z0-9_-]{20,}\b"
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
)


def resolve_high_security_mode(
    explicit: bool | None = None,
    *,
    settings: object | None = None,
) -> bool:
    """Resolve high-security mode using deterministic runtime precedence."""

    if explicit is not None:
        return bool(explicit)

    security_settings = getattr(settings, "security", None)
    if security_settings is not None and hasattr(security_settings, "high_security_mode"):
        return bool(getattr(security_settings, "high_security_mode"))

    if settings is not None and hasattr(settings, "high_security_mode"):
        return bool(getattr(settings, "high_security_mode"))

    return bool(SecuritySettings().high_security_mode)


def scan_outbound_text(
    content: str,
    *,
    location: str | None = None,
    high_security_mode: bool | None = None,
    settings: object | None = None,
) -> OutboundScanResult:
    """Scan one outbound text payload without performing a side effect."""

    effective_mode = resolve_high_security_mode(high_security_mode, settings=settings)
    text = str(content or "")
    normalized_location = _normalize_location(location, default="outbound.text")
    if not effective_mode:
        return _allow_result(
            high_security_mode=False,
            original_content=text,
        )

    findings = _scan_text_for_findings(text, location=normalized_location)
    return _result_for_findings(findings, high_security_mode=True)


def scan_outbound_bundle(
    items: Iterable[OutboundBundleItem | Mapping[str, Any]],
    *,
    high_security_mode: bool | None = None,
    settings: object | None = None,
) -> OutboundScanResult:
    """Scan a commit-like outbound payload bundle before a side effect."""

    effective_mode = resolve_high_security_mode(high_security_mode, settings=settings)
    bundle = [_coerce_bundle_item(item, index) for index, item in enumerate(items)]
    if not effective_mode:
        return _allow_result(
            high_security_mode=False,
            original_bundle=bundle,
        )

    findings: list[OutboundFinding] = []
    for item in bundle:
        findings.extend(_scan_text_for_findings(item.content, location=item.location))
    return _result_for_findings(findings, high_security_mode=True)


def _allow_result(
    *,
    high_security_mode: bool,
    original_content: str | None = None,
    original_bundle: list[OutboundBundleItem] | None = None,
) -> OutboundScanResult:
    return OutboundScanResult(
        allowed=True,
        decision=OutboundScanDecision.ALLOW.value,
        high_security_mode=high_security_mode,
        findings=[],
        sanitized_diagnostics=[],
        original_content=original_content,
        original_bundle=original_bundle,
    )


def _result_for_findings(
    findings: list[OutboundFinding],
    *,
    high_security_mode: bool,
) -> OutboundScanResult:
    if not findings:
        return _allow_result(high_security_mode=high_security_mode)

    diagnostics = [
        f"Blocked outbound content: {finding.category} at {finding.location}"
        for finding in findings
    ]
    return OutboundScanResult(
        allowed=False,
        decision=OutboundScanDecision.BLOCK.value,
        high_security_mode=high_security_mode,
        findings=findings,
        sanitized_diagnostics=diagnostics,
    )


def _coerce_bundle_item(
    item: OutboundBundleItem | Mapping[str, Any],
    index: int,
) -> OutboundBundleItem:
    if isinstance(item, OutboundBundleItem):
        if item.location == "bundle.item":
            return OutboundBundleItem(
                location=f"bundle.item[{index}]",
                content=item.content,
            )
        return item
    location = str(item.get("location") or "").strip() or f"bundle.item[{index}]"
    return OutboundBundleItem(location=location, content=str(item.get("content") or ""))


def _normalize_location(value: str | None, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default


def _scan_text_for_findings(text: str, *, location: str) -> list[OutboundFinding]:
    if not text:
        return []

    findings: list[OutboundFinding] = []
    for category, pattern in (
        ("private_key", _PRIVATE_KEY_PATTERN),
        ("authorization", _AUTHORIZATION_PATTERN),
        ("token", _GITHUB_TOKEN_PATTERN),
        ("credential", _SECRET_ASSIGNMENT_PATTERN),
    ):
        for match in pattern.finditer(text):
            redacted_preview = redact_sensitive_text(match.group(0))
            findings.append(
                OutboundFinding(
                    category=category,
                    location=location,
                    redacted_preview=redacted_preview,
                )
            )
    return findings
