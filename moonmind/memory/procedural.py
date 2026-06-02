"""Fix-pattern procedural memory for prior run failures.

MM-763 implements the compact Plane B primitive described in the memory
architecture: normalize error signatures, retain successful fix patterns with
evidence refs, and expose matches as bounded context entries.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceOutcome = Literal["succeeded", "failed", "unknown"]
SignatureSourceKind = Literal["log", "structured_error", "artifact", "manual"]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ERROR_TOKEN_RE = re.compile(
    r"(error|exception|traceback|failed|failure|timeout|"
    r"modulenotfound|importerror|assertionerror|valueerror|runtimeerror|"
    r"refused|denied|invalid|not found|abort|exit|fatal|critical|unreachable|"
    r"unhandled)",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?<!\w)(?:[A-Za-z]:)?/[^\s:]+")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_SHA_RE = re.compile(r"\b[0-9a-f]{12,64}\b", re.IGNORECASE)
_LINE_RE = re.compile(r"\bline\s+\d+\b", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"\b\d{4,}\b")
_SPACE_RE = re.compile(r"\s+")
_SECRETISH_RE = re.compile(
    r"(ghp_|github_pat_|AIza|ATATT|AKIA|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:token|password|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


class ErrorSignature(BaseModel):
    """A deterministic, compact signature for one observed failure mode."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    signature_id: str = Field(alias="signatureId")
    normalized_text: str = Field(alias="normalizedText")
    source_kind: SignatureSourceKind = Field("log", alias="sourceKind")
    source_ref: str | None = Field(default=None, alias="sourceRef")

    @field_validator("signature_id", "normalized_text")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @model_validator(mode="after")
    def _reject_secretish_signature(self) -> "ErrorSignature":
        _reject_secretish(self.model_dump(by_alias=True))
        return self

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        source_kind: SignatureSourceKind = "log",
        source_ref: str | None = None,
    ) -> "ErrorSignature | None":
        normalized = normalize_error_signature(text)
        if not normalized:
            return None
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return cls(
            signatureId=f"error-signature://sha256:{digest}",
            normalizedText=normalized,
            sourceKind=source_kind,
            sourceRef=source_ref,
        )


class EvidenceRun(BaseModel):
    """Evidence linking a fix pattern to source-of-truth run artifacts."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str | None = Field(default=None, alias="workflowId")
    run_id: str | None = Field(default=None, alias="runId")
    task_run_id: str | None = Field(default=None, alias="taskRunId")
    commit_sha: str | None = Field(default=None, alias="commitSha")
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    outcome: EvidenceOutcome = "unknown"

    @field_validator("artifact_refs")
    @classmethod
    def _clean_artifact_refs(cls, value: Sequence[str]) -> list[str]:
        return _dedupe_text(value)

    @model_validator(mode="after")
    def _require_evidence_pointer(self) -> "EvidenceRun":
        if not any(
            [
                self.workflow_id,
                self.run_id,
                self.task_run_id,
                self.commit_sha,
                self.artifact_refs,
            ]
        ):
            raise ValueError("evidence requires a run, commit, or artifact ref")
        _reject_secretish(self.model_dump(by_alias=True))
        return self

    @property
    def dedupe_key(self) -> str:
        payload = self.model_dump(by_alias=True, exclude_none=True)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return digest


class FixPattern(BaseModel):
    """A reusable playbook for one normalized error signature."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    pattern_ref: str = Field(alias="patternRef")
    signature: ErrorSignature
    summary: str
    steps: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRun] = Field(default_factory=list)
    success_count: int = Field(0, alias="successCount")

    @field_validator("pattern_ref", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("steps")
    @classmethod
    def _clean_steps(cls, value: Sequence[str]) -> list[str]:
        return [_bounded_text(step, limit=240) for step in _dedupe_text(value)]

    @field_validator("success_count")
    @classmethod
    def _non_negative_success_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("successCount must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_pattern(self) -> "FixPattern":
        _reject_secretish(self.model_dump(by_alias=True))
        return self

    @classmethod
    def from_successful_run(
        cls,
        *,
        signature: ErrorSignature,
        summary: str,
        steps: Sequence[str],
        evidence: EvidenceRun,
    ) -> "FixPattern":
        digest = hashlib.sha256(
            f"{signature.signature_id}:{_bounded_text(summary)}".encode("utf-8")
        ).hexdigest()
        return cls(
            patternRef=f"fix-pattern://sha256:{digest}",
            signature=signature,
            summary=_bounded_text(summary, limit=320),
            steps=list(steps),
            evidence=[evidence],
            successCount=1 if evidence.outcome == "succeeded" else 0,
        )


class FileFixPatternStore:
    """Append-safe JSONL store for compact fix-pattern records.

    The store persists only normalized signatures, playbook text, and evidence
    refs. It intentionally does not store raw logs.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def list_patterns(self) -> list[FixPattern]:
        if not self.path.exists():
            return []
        patterns: list[FixPattern] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            patterns.append(FixPattern.model_validate_json(line))
        return patterns

    def upsert(self, pattern: FixPattern) -> FixPattern:
        patterns = self.list_patterns()
        merged = False
        next_patterns: list[FixPattern] = []
        for existing in patterns:
            if existing.signature.signature_id == pattern.signature.signature_id:
                existing = _merge_patterns(existing, pattern)
                merged = True
            next_patterns.append(existing)
        if not merged:
            next_patterns.append(pattern)
        self._write_patterns(next_patterns)
        for candidate in next_patterns:
            if candidate.signature.signature_id == pattern.signature.signature_id:
                return candidate
        raise RuntimeError("upserted fix pattern could not be reloaded")

    def find_matches(
        self,
        signature: ErrorSignature | str,
        *,
        limit: int = 3,
    ) -> list[FixPattern]:
        normalized = (
            signature.normalized_text
            if isinstance(signature, ErrorSignature)
            else normalize_error_signature(signature)
        )
        signature_id = (
            signature.signature_id if isinstance(signature, ErrorSignature) else None
        )
        if not normalized and not signature_id:
            return []

        matches: list[tuple[tuple[int, int], FixPattern]] = []
        for pattern in self.list_patterns():
            score = 0
            if signature_id and pattern.signature.signature_id == signature_id:
                score = 100
            elif normalized and pattern.signature.normalized_text == normalized:
                score = 90
            elif (
                normalized
                and _token_overlap(normalized, pattern.signature.normalized_text) >= 0.6
            ):
                score = 50
            if score:
                matches.append(((score, pattern.success_count), pattern))

        matches.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in matches[: max(limit, 0)]]

    def _write_patterns(self, patterns: Sequence[FixPattern]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            pattern.model_dump_json(by_alias=True, exclude_none=True)
            for pattern in sorted(patterns, key=lambda item: item.pattern_ref)
        )
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        tmp_path.replace(self.path)


def extract_error_signature(
    text: str,
    *,
    source_kind: SignatureSourceKind = "log",
    source_ref: str | None = None,
) -> ErrorSignature | None:
    """Extract a deterministic signature from raw log or error text."""

    return ErrorSignature.from_text(
        text,
        source_kind=source_kind,
        source_ref=source_ref,
    )


def normalize_error_signature(text: str) -> str | None:
    """Normalize dynamic failure text into a reusable signature key."""

    candidate = _select_error_line(text)
    if not candidate:
        return None
    candidate = _ANSI_RE.sub("", candidate)
    candidate = _PATH_RE.sub("<path>", candidate)
    candidate = _UUID_RE.sub("<uuid>", candidate)
    candidate = _HEX_RE.sub("<hex>", candidate)
    candidate = _SHA_RE.sub("<sha>", candidate)
    candidate = _LINE_RE.sub("line <n>", candidate)
    candidate = _LONG_NUMBER_RE.sub("<n>", candidate)
    candidate = _SPACE_RE.sub(" ", candidate).strip().lower()
    return candidate or None


def fix_patterns_to_memory_proposals(
    patterns: Sequence[FixPattern | Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Convert matched fix patterns into existing execution memory proposals."""

    proposals: list[dict[str, str]] = []
    for raw_pattern in patterns:
        pattern = (
            raw_pattern
            if isinstance(raw_pattern, FixPattern)
            else FixPattern.model_validate(raw_pattern)
        )
        steps = "; ".join(pattern.steps[:3])
        summary = pattern.summary if not steps else f"{pattern.summary} Steps: {steps}"
        proposals.append(
            {
                "proposalRef": pattern.pattern_ref,
                "state": "accepted_for_run_context",
                "summary": _bounded_text(summary, limit=500),
            }
        )
    return proposals


def _select_error_line(text: str) -> str | None:
    lines = [
        line.strip()
        for line in str(text or "").splitlines()
        if line and line.strip()
    ]
    if not lines:
        return None
    for line in reversed(lines):
        if _ERROR_TOKEN_RE.search(line):
            return line
    return None


def _merge_patterns(existing: FixPattern, incoming: FixPattern) -> FixPattern:
    evidence_by_key = {item.dedupe_key: item for item in existing.evidence}
    for item in incoming.evidence:
        evidence_by_key.setdefault(item.dedupe_key, item)
    evidence = list(evidence_by_key.values())[-20:]
    success_count = sum(1 for item in evidence if item.outcome == "succeeded")
    return FixPattern(
        patternRef=existing.pattern_ref,
        signature=existing.signature,
        summary=incoming.summary or existing.summary,
        steps=_dedupe_text([*incoming.steps, *existing.steps])[:10],
        evidence=evidence,
        successCount=success_count,
    )


def _token_overlap(left: str, right: str) -> float:
    def _clean_tokens(text: str) -> set[str]:
        tokens: set[str] = set()
        for token in text.split():
            cleaned = re.sub(r"^\W+|\W+$", "", token)
            if cleaned:
                tokens.add(cleaned)
        return tokens

    left_tokens = _clean_tokens(left)
    right_tokens = _clean_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _dedupe_text(values: Iterable[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = str(value or "").strip()
        if not candidate or candidate in seen:
            continue
        deduped.append(candidate)
        seen.add(candidate)
    return deduped


def _bounded_text(value: str, *, limit: int = 180) -> str:
    candidate = _SPACE_RE.sub(" ", str(value or "")).strip()
    if len(candidate) <= limit:
        return candidate
    return candidate[: limit - 3].rstrip() + "..."


def _reject_secretish(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    if _SECRETISH_RE.search(serialized):
        raise ValueError("procedural memory must not contain raw secret material")
