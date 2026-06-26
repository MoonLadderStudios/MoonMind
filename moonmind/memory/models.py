from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemorySource = Literal["planning", "history", "fix_pattern", "long_term", "document"]
TrustClass = Literal["raw", "derived", "approved"]
ReviewState = Literal["draft", "approved", "deprecated"]
RunOutcome = Literal["succeeded", "failed", "blocked", "cancelled", "unknown"]


class RunRef(BaseModel):
    """Compact reference to a durable execution source of truth."""

    kind: Literal["workflow", "task_run", "agent_job", "external"] = "workflow"
    id: str

    @field_validator("id")
    @classmethod
    def _require_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("run reference id is required")
        return cleaned


class MemoryProvenance(BaseModel):
    """Evidence links carried by every memory contribution."""

    workflow_id: str | None = None
    agent_run_id: str | None = None
    commits: list[str] = Field(default_factory=list)
    pull_request_url: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _require_some_evidence(self) -> "MemoryProvenance":
        if (
            self.workflow_id
            or self.agent_run_id
            or self.commits
            or self.pull_request_url
            or self.artifact_refs
            or self.source_refs
        ):
            return self
        raise ValueError("memory provenance requires at least one evidence pointer")


class RunDigest(BaseModel):
    """Plane B task history summary suitable for retrieval indexing."""

    namespace_id: str
    repo: str
    run_ref: RunRef
    intent: str
    outcome: RunOutcome = "unknown"
    key_changes: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    gotchas: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    provenance: MemoryProvenance
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="forbid")

    def as_candidate(self) -> "MemoryCandidate":
        parts = [f"Intent: {self.intent}", f"Outcome: {self.outcome}"]
        if self.key_changes:
            parts.append("Changes: " + "; ".join(self.key_changes))
        if self.decisions:
            parts.append("Decisions: " + "; ".join(self.decisions))
        if self.gotchas:
            parts.append("Gotchas: " + "; ".join(self.gotchas))
        if self.next_steps:
            parts.append("Next steps: " + "; ".join(self.next_steps))
        return MemoryCandidate(
            text="\n".join(parts),
            source="history",
            trust_class="derived",
            provenance=self.provenance,
            token_cost=estimate_token_cost("\n".join(parts)),
            metadata={
                "record_kind": "run_digest",
                "namespace_id": self.namespace_id,
                "repo": self.repo,
                "run_ref.kind": self.run_ref.kind,
                "run_ref.id": self.run_ref.id,
            },
        )


class ErrorSignature(BaseModel):
    """Normalized procedural-memory key derived from an error or log excerpt."""

    value: str
    family: str = "unknown"
    evidence: MemoryProvenance

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        evidence: MemoryProvenance,
        family: str = "unknown",
    ) -> "ErrorSignature":
        normalized = normalize_error_signature(text)
        if not normalized:
            raise ValueError("error signature text is required")
        return cls(value=normalized, family=family, evidence=evidence)


class FixPattern(BaseModel):
    """Plane B procedural memory for repeat failures and known fixes."""

    namespace_id: str
    repo: str
    signature: ErrorSignature
    summary: str
    successful_run_refs: list[RunRef] = Field(default_factory=list)
    provenance: MemoryProvenance
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="forbid")

    def as_candidate(self) -> "MemoryCandidate":
        text = f"Error signature: {self.signature.value}\nFix pattern: {self.summary}"
        return MemoryCandidate(
            text=text,
            source="fix_pattern",
            trust_class="derived",
            provenance=self.provenance,
            token_cost=estimate_token_cost(text),
            metadata={
                "record_kind": "fix_pattern",
                "namespace_id": self.namespace_id,
                "repo": self.repo,
                "signature": self.signature.value,
            },
        )


class LongTermMemory(BaseModel):
    """Plane C curated memory entry, backed by a Mem0-compatible adapter."""

    namespace_id: str
    repo: str
    scope: Literal["project", "team", "user"] = "project"
    text: str
    review_state: ReviewState = "draft"
    provenance: MemoryProvenance
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def as_candidate(self) -> "MemoryCandidate":
        return MemoryCandidate(
            text=self.text,
            source="long_term",
            trust_class="approved" if self.review_state == "approved" else "derived",
            provenance=self.provenance,
            token_cost=estimate_token_cost(self.text),
            metadata={
                "record_kind": "long_term_shadow",
                "namespace_id": self.namespace_id,
                "repo": self.repo,
                "scope": self.scope,
                "review_state": self.review_state,
                **self.metadata,
            },
        )


class MemoryCandidate(BaseModel):
    text: str
    source: MemorySource
    trust_class: TrustClass
    provenance: MemoryProvenance
    token_cost: int = Field(ge=0)
    recency: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ContextPackBudget(BaseModel):
    max_tokens: int = Field(4000, ge=1)
    reserve_tokens: int = Field(0, ge=0)

    @property
    def usable_tokens(self) -> int:
        return max(self.max_tokens - self.reserve_tokens, 0)


class ContextPack(BaseModel):
    query: str
    included: list[MemoryCandidate]
    skipped: list[MemoryCandidate] = Field(default_factory=list)
    budget: ContextPackBudget
    degraded_components: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @property
    def token_cost(self) -> int:
        return sum(candidate.token_cost for candidate in self.included)

    @property
    def provenance(self) -> list[MemoryProvenance]:
        return [candidate.provenance for candidate in self.included]


_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(/[A-Za-z0-9_.:-]+)+")
_NUMBER_RE = re.compile(r"\b\d+\b")


def estimate_token_cost(text: str) -> int:
    """Cheap deterministic budget estimate for memory packaging."""

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def normalize_error_signature(text: str) -> str:
    """Collapse volatile IDs, paths, and numbers into a reusable signature."""

    value = " ".join(text.strip().split())
    value = _PATH_RE.sub("<path>", value)
    value = _UUID_RE.sub("<uuid>", value)
    value = _HEX_RE.sub("<hex>", value)
    value = _NUMBER_RE.sub("<num>", value)
    return value[:512]
