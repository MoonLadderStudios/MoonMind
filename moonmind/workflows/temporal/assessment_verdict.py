"""Canonical assessment-verdict normalization.

One capability-derived recovery policy for issue-implement assessments.

Deterministic workflows and activities share this module so GitHub and Jira
stay on one verdict path. It trusts the assessment agent's semantic intent over
strict syntactic field presence:

- ``declared``: explicit ``verdict`` field valid.
- ``text_recovered``: verdict found in free text (assistant ``## Verdict: X``,
  ``verdict: X``, ``assessment complete ... X``).
- ``requirements_inferred``: no explicit verdict anywhere, but the agent's own
  requirements list is unanimous. All ``met`` implies the agent believed the
  work complete; mixed implies partial work remains.

Only truly ambiguous payloads (no verdict, no text, empty/mixed-unknown
requirements) return ``unavailable``. Callers preserve the original artifact
and record ``verdictProvenance`` so inference is observable, never silent.

Safe bias: never infer ``BLOCKED`` from requirements alone (BLOCKED needs
explicit missing evidence). Mixed ``partially_met``/``not_met`` infers
``PARTIALLY_IMPLEMENTED`` (do bounded work) rather than skipping work.
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
_ISSUE_REF_PATTERN = r"`?[A-Z][A-Z0-9]+-\d+`?"

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


def _requirement_status(value: Any) -> str:
    status = _string(value).strip().lower().replace("-", "_").replace(" ", "_")
    # Normalize common agent variants to canonical preset vocabulary.
    aliases = {
        "fully_met": "met",
        "fully_implemented": "met",
        "satisfied": "met",
        "passed": "met",
        "done": "met",
        "partial": "partially_met",
        "partial_met": "partially_met",
        "incomplete": "partially_met",
        "unmet": "not_met",
        "missing": "not_met",
        "failed": "not_met",
        "todo": "not_met",
    }
    return aliases.get(status, status)


def infer_verdict_from_requirements(
    requirements: Any,
    *,
    summary: Any = "",
    assistant_text: Any = "",
) -> tuple[str, str]:
    """Infer verdict from the agent's own requirements list.

    Returns ``(verdict, evidence)``. Empty verdict means cannot safely infer.

    Rules (safe bias, no BLOCKED inference):
    - empty/non-list requirements -> unavailable.
    - all ``met`` -> FULLY_IMPLEMENTED (agent believed all done).
    - all ``not_met`` -> NOT_IMPLEMENTED.
    - any ``partially_met``/``not_met`` mixed with ``met`` -> PARTIALLY_IMPLEMENTED.
    - any ``unverifiable`` with rest ``met`` -> PARTIALLY_IMPLEMENTED (preserve
      prerequisites, do bounded work, don't skip).
    - unknown statuses only -> unavailable.
    """
    if not isinstance(requirements, (list, tuple)) or not requirements:
        return "", ""
    statuses: list[str] = []
    for item in requirements:
        if isinstance(item, Mapping):
            raw = (
                item.get("status")
                or item.get("state")
                or item.get("verdict")
                or item.get("result")
            )
            statuses.append(_requirement_status(raw))
        elif isinstance(item, str):
            statuses.append(_requirement_status(item))
        else:
            statuses.append("")
    known = [s for s in statuses if s]
    if not known:
        return "", ""
    # If any status is outside known vocabulary, don't infer.
    allowed = {"met", "partially_met", "not_met", "unverifiable"}
    if any(s not in allowed for s in known):
        # Still allow inference if the unknown looks like a met-variant already
        # normalized above; otherwise treat as ambiguous.
        return "", ""
    if all(s == "met" for s in known):
        evidence = f"{len(known)} requirements all met"
        summary_text = _string(summary)
        if summary_text:
            evidence += f"; summary: {summary_text[:160]}"
        assistant = _string(assistant_text)
        if assistant:
            # Corroborating text verdict strengthens evidence but mapping/text
            # recovery is handled by callers; here we just note it.
            text_verdict = verdict_from_text(assistant)
            if text_verdict:
                evidence += f"; assistant text verdict {text_verdict}"
        return "FULLY_IMPLEMENTED", evidence
    if all(s == "not_met" for s in known):
        return "NOT_IMPLEMENTED", f"{len(known)} requirements all not_met"
    if any(s in {"partially_met", "not_met", "unverifiable"} for s in known):
        return (
            "PARTIALLY_IMPLEMENTED",
            f"{len(known)} requirements mixed: {sorted(set(known))}",
        )
    return "", ""


def normalize_assessment_payload(
    payload: Mapping[str, Any],
    *,
    assistant_text: Any = "",
) -> tuple[str, str, str]:
    """Normalize one assessment artifact payload.

    Returns ``(verdict, provenance, evidence)`` where provenance is one of
    ``declared``, ``mapping``, ``text_recovered``, ``requirements_inferred``,
    or ``unavailable``.

    Order preserves authority: explicit field first, then compact mapping
    variants, then free text, then unanimous requirements. Requirements
    inference is last because it is the weakest signal, but it rescues the
    common minor-difference case (verdict key omitted while every requirement
    is marked met and summary/text agree).
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
    requirements = payload.get("requirements")
    summary = payload.get("summary", "")
    verdict, evidence = infer_verdict_from_requirements(
        requirements,
        summary=summary,
        assistant_text=assistant_text,
    )
    if verdict:
        return verdict, "requirements_inferred", evidence
    return "", "unavailable", "no usable verdict signal"
