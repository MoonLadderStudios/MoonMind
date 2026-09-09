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
    """Approved-review-state long-term memory over explicit references.

    MoonLadderStudios/MoonMind#4109: the hosted Mem0 adapter is retired. This
    in-memory service remains for tests and local-only wiring; it performs
    scoped exact-reference reads (namespace/repo/review-state filtered) with
    no vector index, no embeddings, and no external calls.
    """

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
class RetrievalGateway:
    """Assemble Plane A/B/document memory into a token-budgeted context pack.

    MoonLadderStudios/MoonMind#4109: hosted Mem0 Plane C memory is retired, so
    only planning (Beads, explicit-reference), history (in-memory digest
    store), and caller-supplied document candidates are assembled here.
    """

    settings: MemorySettings = field(default_factory=MemorySettings)
    planning: PlanningAdapter | None = None
    history: InMemoryTaskHistoryStore | None = None
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
