"""Canonical assessment-verdict normalization.

Syntactic normalization only. Completion decisions stay in the assessment Skill.

Deterministic workflows and activities share this module so GitHub and Jira
stay on one verdict path. It recovers explicit verdict statements when the
``verdict`` key is omitted, but it never manufactures completion decisions
from requirement statuses:

- ``declared``: explicit ``verdict`` field valid.
- ``mapping``: compact alias (``assessmentVerdict`` etc., including nested).
- ``text_recovered``: explicit verdict in free text (assistant
  ``## Verdict: X``, ``verdict: X``, ``assessment complete ... X``).

Requirement lists (all ``met`` etc.) do NOT imply a verdict here. Inferring
``FULLY_IMPLEMENTED`` from unanimous requirements is a completion decision
owned by the portable assessment Skill, not the native boundary. When no
explicit verdict exists, callers must reject with repair guidance or execute
the portable Skill entrypoint — never synthesize completion natively.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


ASSESSMENT_VERDICTS = frozenset(
    {"FULLY_IMPLEMENTED", "PARTIALLY_IMPLEMENTED", "NOT_IMPLEMENTED", "BLOCKED"}
)

_MAPPING_VERDICT_KEYS = (
    "assessmentVerdict",
    "assessment_verdict",
    "jiraAssessmentVerdict",
    "jira_assessment_verdict",
    "initialAssessmentVerdict",
    "initial_assessment_verdict",
    "verdict",
)

_MAPPING_NESTED_KEYS = (
    "assessment",
    "jiraAssessment",
    "jira_assessment",
    "jiraImplementAssessment",
    "jira_implement_assessment",
)

_TEXT_KEYS = ("lastAssistantText", "assistantText", "summary", "operator_summary")

_VERDICT_PATTERN = r"(FULLY_IMPLEMENTED|PARTIALLY_IMPLEMENTED|NOT_IMPLEMENTED|BLOCKED)"
_VERDICT_PREFIX = r"[\s:`*_\"']*"
_VERDICT_SUFFIX = r"(?:_+(?!\w)|(?![-\w]))"
_ASSESSMENT_SEP = r"[\s:.,;!?\-\u2010-\u2015`*_\"'\[\]\(\)]*"
# Issue refs for text recovery: Jira (MM-123), GitHub qualified
# (Owner/Repo#123), and short (#123). Backticks optional.
_ISSUE_REF_PATTERN = r"(?:`?[A-Z][A-Z0-9]+-\d+`?|`?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+`?|`?#\d+`?)"

_TEXT_PATTERNS = (
    r"(?im)^\s*#{1,6}\s*verdict\s*[:\-]\s*"
    rf"{_VERDICT_PREFIX}{_VERDICT_PATTERN}{_VERDICT_SUFFIX}",
    r"(?im)^\s*verdict\s*[:\-]\s*"
    rf"{_VERDICT_PREFIX}{_VERDICT_PATTERN}{_VERDICT_SUFFIX}",
    r"(?is)\bassessment\s+complete\b"
    rf"{_ASSESSMENT_SEP}"
    rf"(?:(?:for|on)\b{_ASSESSMENT_SEP}"
    rf"{_ISSUE_REF_PATTERN}{_ASSESSMENT_SEP})?"
    rf"(?:{_ISSUE_REF_PATTERN}{_ASSESSMENT_SEP})?"
    rf"(?:(?:is|was|has|verdict|status)\b{_ASSESSMENT_SEP})?"
    rf"{_VERDICT_PATTERN}{_VERDICT_SUFFIX}",
    r"(?is)\brecorded\s+verdict\b[^.\n:]*[:\s`]+"
    rf"{_VERDICT_PREFIX}{_VERDICT_PATTERN}{_VERDICT_SUFFIX}",
    r"(?is)['\"]verdict['\"]\s*:\s*['\"]"
    rf"{_VERDICT_PATTERN}['\"]",
)


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_verdict(value: Any) -> str:
    """Return canonical verdict or empty when invalid."""
    verdict = _string(value).strip().upper()
    return verdict if verdict in ASSESSMENT_VERDICTS else ""


def verdict_from_text(value: Any) -> str:
    """Extract verdict from free text using bounded patterns."""
    text = _string(value)
    if not text:
        return ""
    for pattern in _TEXT_PATTERNS:
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if match:
            return normalize_verdict(match.group(1))
    return ""


def verdict_from_mapping(payload: Mapping[str, Any]) -> str:
    """Extract verdict from compact mapping keys (including nested)."""
    for key in _MAPPING_VERDICT_KEYS:
        verdict = normalize_verdict(payload.get(key))
        if verdict:
            return verdict
    for key in _MAPPING_NESTED_KEYS:
        nested = _mapping(payload.get(key))
        if nested:
            verdict = verdict_from_mapping(nested)
            if verdict:
                return verdict
    return ""


def normalize_assessment_payload(
    payload: Mapping[str, Any],
    *,
    assistant_text: Any = "",
) -> tuple[str, str, str]:
    """Normalize one assessment artifact payload (syntactic only).

    Returns ``(verdict, provenance, evidence)`` where provenance is one of
    ``declared``, ``mapping``, ``text_recovered``, or ``unavailable``.

    Order preserves authority: explicit field first, then compact mapping
    variants, then explicit free-text verdict statements. Requirement statuses
    are never mapped to verdicts here; that completion decision belongs to the
    portable assessment Skill.
    """
    if not isinstance(payload, Mapping):
        return "", "unavailable", "payload is not a JSON object"
    verdict = normalize_verdict(payload.get("verdict"))
    if verdict:
        return verdict, "declared", "payload.verdict"
    verdict = verdict_from_mapping(payload)
    if verdict:
        return verdict, "mapping", "compact mapping key"
    for key in _TEXT_KEYS:
        verdict = verdict_from_text(payload.get(key))
        if verdict:
            return verdict, "text_recovered", f"payload.{key}"
    verdict = verdict_from_text(assistant_text)
    if verdict:
        return verdict, "text_recovered", "assistant text"
    return "", "unavailable", "no explicit verdict signal"
