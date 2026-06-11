from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from moonmind.config.settings import MemorySettings
from moonmind.memory.models import (
    ContextPack,
    ContextPackBudget,
    ErrorSignature,
    FixPattern,
    LongTermMemory,
    MemoryCandidate,
    MemoryProvenance,
    RunDigest,
    RunRef,
    estimate_token_cost,
)


class PlanningAdapter(Protocol):
    def prefetch(self, planning_ref: str | None) -> list[MemoryCandidate]:
        raise NotImplementedError


class LongTermMemoryAdapter(Protocol):
    def search(
        self,
        query: str,
        *,
        namespace_id: str,
        repo: str,
    ) -> list[LongTermMemory]:
        raise NotImplementedError

    def add_or_update(self, memory: LongTermMemory) -> LongTermMemory:
        raise NotImplementedError


@dataclass
class InMemoryPlanningAdapter:
    """Beads-compatible planning adapter for tests and local fail-open wiring."""

    items: dict[str, list[MemoryCandidate]] = field(default_factory=dict)

    def prefetch(self, planning_ref: str | None) -> list[MemoryCandidate]:
        if not planning_ref:
            return []
        return list(self.items.get(planning_ref, []))


@dataclass
class InMemoryTaskHistoryStore:
    digests: list[RunDigest] = field(default_factory=list)
    fix_patterns: list[FixPattern] = field(default_factory=list)

    def upsert_digest(self, digest: RunDigest) -> None:
        self.digests = [
            existing
            for existing in self.digests
            if existing.run_ref.kind != digest.run_ref.kind
            or existing.run_ref.id != digest.run_ref.id
        ]
        self.digests.append(digest)

    def upsert_fix_pattern(self, pattern: FixPattern) -> None:
        self.fix_patterns = [
            existing
            for existing in self.fix_patterns
            if existing.namespace_id != pattern.namespace_id
            or existing.repo != pattern.repo
            or existing.signature.value != pattern.signature.value
        ]
        self.fix_patterns.append(pattern)

    def search(self, query: str, *, namespace_id: str, repo: str) -> list[MemoryCandidate]:
        query_terms = _terms(query)
        candidates: list[MemoryCandidate] = []
        for digest in self.digests:
            if digest.namespace_id == namespace_id and digest.repo == repo:
                candidates.append(digest.as_candidate())
        for pattern in self.fix_patterns:
            if pattern.namespace_id == namespace_id and pattern.repo == repo:
                candidates.append(pattern.as_candidate())
        return sorted(
            candidates,
            key=lambda candidate: _score(candidate.text, query_terms),
            reverse=True,
        )


@dataclass
class TaskHistoryService:
    """Plane B run digest and fix-pattern service."""

    store: InMemoryTaskHistoryStore = field(default_factory=InMemoryTaskHistoryStore)

    def build_run_digest(
        self,
        *,
        namespace_id: str,
        repo: str,
        run_ref: RunRef,
        intent: str,
        outcome: str,
        provenance: MemoryProvenance,
        key_changes: list[str] | None = None,
        decisions: list[str] | None = None,
        gotchas: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> RunDigest:
        return RunDigest(
            namespace_id=namespace_id,
            repo=repo,
            run_ref=run_ref,
            intent=intent,
            outcome=outcome or "unknown",
            key_changes=key_changes or [],
            decisions=decisions or [],
            gotchas=gotchas or [],
            next_steps=next_steps or [],
            provenance=provenance,
        )

    def extract_error_signature(
        self,
        text: str,
        *,
        evidence: MemoryProvenance,
        family: str = "unknown",
    ) -> ErrorSignature:
        return ErrorSignature.from_text(text, evidence=evidence, family=family)

    def upsert_digest_and_fix_patterns(
        self,
        digest: RunDigest,
        fix_patterns: list[FixPattern] | None = None,
    ) -> None:
        self.store.upsert_digest(digest)
        for pattern in fix_patterns or []:
            self.store.upsert_fix_pattern(pattern)


@dataclass
class InMemoryLongTermMemoryService:
    """Mem0-compatible long-term memory adapter."""

    memories: list[LongTermMemory] = field(default_factory=list)

    def search(
        self,
        query: str,
        *,
        namespace_id: str,
        repo: str,
    ) -> list[LongTermMemory]:
        query_terms = _terms(query)
        scoped = [
            memory
            for memory in self.memories
            if memory.namespace_id == namespace_id
            and memory.repo == repo
            and memory.review_state == "approved"
        ]
        return sorted(scoped, key=lambda memory: _score(memory.text, query_terms), reverse=True)

    def add_or_update(self, memory: LongTermMemory) -> LongTermMemory:
        self.memories = [
            existing
            for existing in self.memories
            if existing.namespace_id != memory.namespace_id
            or existing.repo != memory.repo
            or existing.scope != memory.scope
            or existing.text != memory.text
        ]
        self.memories.append(memory)
        return memory


@dataclass
class Mem0LongTermMemoryService:
    """Adapter for an injected Mem0-compatible client.

    The client is expected to expose ``search(query, metadata=...)`` and
    ``add(text, metadata=...)`` methods. MoonMind keeps provenance and review
    metadata in the payload so Mem0 is a long-term memory API layer, not the
    source of truth for run evidence.
    """

    client: Any

    def search(
        self,
        query: str,
        *,
        namespace_id: str,
        repo: str,
    ) -> list[LongTermMemory]:
        results = self.client.search(
            query,
            metadata={
                "namespace_id": namespace_id,
                "repo": repo,
                "review_state": "approved",
            },
        )
        return [
            _memory_from_mem0_result(result, namespace_id=namespace_id, repo=repo)
            for result in results
        ]

    def add_or_update(self, memory: LongTermMemory) -> LongTermMemory:
        metadata = _clean_mem0_metadata(
            {
                "namespace_id": memory.namespace_id,
                "repo": memory.repo,
                "scope": memory.scope,
                "review_state": memory.review_state,
                "workflow_id": memory.provenance.workflow_id,
                "agent_run_id": memory.provenance.agent_run_id,
                "commits": ",".join(memory.provenance.commits),
                "pull_request_url": memory.provenance.pull_request_url,
                "artifact_refs": ",".join(memory.provenance.artifact_refs),
                "source_refs": ",".join(memory.provenance.source_refs),
                **memory.metadata,
            },
        )
        self.client.add(
            memory.text,
            metadata=metadata,
        )
        return memory


@dataclass
class RetrievalGateway:
    """Assemble Plane A/B/C/document memory into a token-budgeted context pack."""

    settings: MemorySettings = field(default_factory=MemorySettings)
    planning: PlanningAdapter | None = None
    history: InMemoryTaskHistoryStore | None = None
    long_term: LongTermMemoryAdapter | None = None
    document_candidates: list[MemoryCandidate] = field(default_factory=list)

    def retrieve_context_pack(
        self,
        query: str,
        *,
        namespace_id: str,
        repo: str,
        planning_ref: str | None = None,
        budget: ContextPackBudget | None = None,
    ) -> ContextPack:
        active_budget = budget or ContextPackBudget(
            max_tokens=self.settings.context_budget_tokens
        )
        if not self.settings.enabled:
            return ContextPack(
                query=query,
                included=[],
                skipped=[],
                budget=active_budget,
                degraded_components=["memory_disabled"],
            )

        candidates: list[MemoryCandidate] = []
        degraded: list[str] = []
        candidates.extend(
            self._collect(
                "planning",
                lambda: self.planning.prefetch(planning_ref)
                if self.settings.planning == "beads"
                else [],
                degraded,
            )
        )
        candidates.extend(
            self._collect(
                "history",
                lambda: self.history.search(query, namespace_id=namespace_id, repo=repo)
                if self.settings.history == "digest"
                else [],
                degraded,
            )
        )
        candidates.extend(
            memory.as_candidate()
            for memory in self._collect(
                "long_term",
                lambda: self.long_term.search(
                    query,
                    namespace_id=namespace_id,
                    repo=repo,
                )
                if self.settings.long_term == "mem0"
                else [],
                degraded,
            )
        )
        candidates.extend(self.document_candidates)
        return _pack(query, candidates, active_budget, degraded)

    def _collect(self, name: str, callback, degraded: list[str]):
        if not self.settings.fail_open:
            return callback()
        try:
            return callback()
        except Exception:
            degraded.append(name)
            return []


def planning_candidate(
    text: str,
    *,
    source_ref: str,
    token_cost: int | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        text=text,
        source="planning",
        trust_class="raw",
        provenance=MemoryProvenance(source_refs=[source_ref]),
        token_cost=token_cost if token_cost is not None else estimate_token_cost(text),
        metadata={"record_kind": "planning_context"},
    )


def _pack(
    query: str,
    candidates: list[MemoryCandidate],
    budget: ContextPackBudget,
    degraded: list[str],
) -> ContextPack:
    included: list[MemoryCandidate] = []
    skipped: list[MemoryCandidate] = []
    remaining = budget.usable_tokens
    for candidate in candidates:
        if candidate.token_cost <= remaining:
            included.append(candidate)
            remaining -= candidate.token_cost
        else:
            skipped.append(candidate)
    return ContextPack(
        query=query,
        included=included,
        skipped=skipped,
        budget=budget,
        degraded_components=degraded,
    )


def _terms(value: str) -> set[str]:
    return {term.casefold() for term in value.split() if term.strip()}


def _score(text: str, terms: set[str]) -> int:
    haystack = text.casefold()
    return sum(1 for term in terms if term in haystack)


def _memory_from_mem0_result(
    result: Any,
    *,
    namespace_id: str,
    repo: str,
) -> LongTermMemory:
    if isinstance(result, dict):
        text = str(result.get("memory") or result.get("text") or result.get("value") or "")
        metadata = result.get("metadata") or {}
    else:
        text = str(getattr(result, "memory", None) or getattr(result, "text", None) or result)
        metadata = getattr(result, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    raw_scope = str(metadata.get("scope") or "project").strip().lower()
    scope = raw_scope if raw_scope in ("project", "team", "user") else "project"
    raw_state = str(metadata.get("review_state") or "approved").strip().lower()
    review_state = (
        raw_state if raw_state in ("draft", "approved", "deprecated") else "approved"
    )
    provenance = MemoryProvenance(
        workflow_id=_optional_str(metadata.get("workflow_id")),
        agent_run_id=_optional_str(metadata.get("agent_run_id")),
        commits=_split_csv(metadata.get("commits")),
        pull_request_url=_optional_str(metadata.get("pull_request_url")),
        artifact_refs=_split_csv(metadata.get("artifact_refs")),
        source_refs=_split_csv(metadata.get("source_refs")) or ["mem0"],
    )
    return LongTermMemory(
        namespace_id=str(metadata.get("namespace_id") or namespace_id),
        repo=str(metadata.get("repo") or repo),
        scope=scope,
        text=text,
        review_state=review_state,
        provenance=provenance,
        metadata={str(key): str(value) for key, value in metadata.items() if value is not None},
    )


def _clean_mem0_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]
