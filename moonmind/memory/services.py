from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from moonmind.schemas.step_execution_models import (
    MemoryApplicationResultManifest,
    MemoryPolicyDecisionManifest,
    StepExecutionIdentityModel,
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


def evaluate_memory_proposals(
    *,
    proposal_refs: list[str],
    source: dict[str, Any],
    terminal_disposition: str | None,
    publication_gate: dict[str, Any] | None,
    requested_target: str,
    policy_decision: str | None = None,
    reason: str | None = None,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate memory proposal refs and return compact policy decisions."""

    source_identity = StepExecutionIdentityModel.model_validate(source)
    normalized_refs = [str(ref).strip() for ref in proposal_refs if str(ref).strip()]
    gate = dict(publication_gate or {})
    publication_passed = gate.get("passed") is True
    decision = str(policy_decision or "").strip()
    repo_target = str(requested_target or "").strip().startswith("repo://")
    if decision not in {
        "reject",
        "accept_for_run_context",
        "approve_repo_application",
        "supersede",
        "blocked",
    }:
        decision = "blocked"
        resolved_reason = "unknown_policy_decision"
    elif not repo_target and decision == "approve_repo_application":
        decision = "blocked"
        resolved_reason = "memory_target_requires_run_context_decision"
    elif decision == "approve_repo_application" and (
        terminal_disposition != "accepted" or not publication_passed
    ):
        decision = "blocked"
        resolved_reason = (
            "terminal_disposition_not_accepted"
            if terminal_disposition != "accepted"
            else "publication_gate_not_passed"
        )
    elif repo_target and decision == "accept_for_run_context":
        decision = "blocked"
        resolved_reason = "repo_target_requires_repo_application_decision"
    else:
        resolved_reason = str(reason or "").strip() or "policy_decision_recorded"

    decisions: list[dict[str, Any]] = []
    decision_refs: list[str] = []
    for index, proposal_ref in enumerate(normalized_refs, start=1):
        decision_ref = f"artifact://memory/decision-{index}"
        manifest = MemoryPolicyDecisionManifest(
            decisionId=f"decision-{index}",
            proposalRef=proposal_ref,
            source=source_identity,
            target=requested_target,
            reason=resolved_reason,
            decision=decision,
            decisionRef=decision_ref,
            evidenceRefs=evidence_refs or [proposal_ref],
            gateStatus={
                "terminalDisposition": terminal_disposition,
                "publicationGate": publication_passed,
                "publicationGateEvidenceRef": gate.get("evidenceRef"),
                "policyGate": decision != "blocked",
            },
            createdAt=datetime.now(UTC),
        )
        decision_refs.append(decision_ref)
        decisions.append(
            {
                "proposalRef": manifest.proposal_ref,
                "decision": manifest.decision,
                "target": manifest.target,
                "reason": manifest.reason,
                "evidenceRefs": manifest.evidence_refs,
                "decisionRef": manifest.decision_ref,
            }
        )
    return {"decisionRefs": decision_refs, "decisions": decisions}


def apply_memory_policy(
    *,
    proposal_ref: str,
    decision_ref: str,
    source: dict[str, Any],
    target: str,
    decision: str,
    result_ref: str | None = None,
    gate_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an approved memory decision or return a blocked result."""

    source_identity = StepExecutionIdentityModel.model_validate(source)
    proposal = str(proposal_ref or "").strip()
    decision_artifact_ref = str(decision_ref or "").strip()
    repo_target = str(target or "").strip().startswith("repo://")
    gate = dict(gate_status or {})
    gates_passed = (
        gate.get("terminalDisposition") == "accepted"
        and gate.get("publicationGate") is True
        and gate.get("policyGate", True) is True
    )
    if not decision_artifact_ref:
        outcome = "blocked"
        failure_reason = "missing_decision_ref"
    elif decision not in {"accept_for_run_context", "approve_repo_application"}:
        outcome = "blocked"
        failure_reason = "policy_decision_not_approving"
    elif decision == "approve_repo_application" and repo_target and not gates_passed:
        outcome = "blocked"
        failure_reason = "applied_repo_memory_result_requires_accepted_gates"
    else:
        outcome = "applied"
        failure_reason = None
    application_ref = "artifact://memory/application-1"
    resolved_result_ref = result_ref or (
        "artifact://memory/run-context-1"
        if outcome == "applied" and str(target).startswith("memory://")
        else "artifact://memory/repo-application-1"
        if outcome == "applied"
        else None
    )
    manifest = MemoryApplicationResultManifest(
        applicationId="application-1",
        proposalRef=proposal,
        decisionRef=decision_artifact_ref or application_ref,
        source=source_identity,
        target=target,
        outcome=outcome,
        resultRef=resolved_result_ref,
        failureReason=failure_reason,
        gateStatus=gate,
        createdAt=datetime.now(UTC),
    )
    return {
        "applicationResultRef": application_ref,
        "outcome": manifest.outcome,
        "target": manifest.target,
        "resultRef": manifest.result_ref,
        "failureReason": manifest.failure_reason,
    }


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
