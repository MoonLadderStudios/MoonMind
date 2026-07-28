"""Ephemeral retrieval authority and bounded evidence for managed sessions.

The raw capability is returned once to the host.  Only its digest is retained;
durable evidence therefore cannot be used to replay retrieval authority.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalBudgetSnapshot:
    tenant_id: str
    repository: str
    run_id: str
    workspace_id: str
    host_id: str
    session_id: str
    step_id: str
    policy_version: str
    collections: tuple[str, ...]
    filters: tuple[tuple[str, str], ...]
    top_k: int = 8
    max_sources: int = 8
    max_query_bytes: int = 4096
    max_context_bytes: int = 32768
    max_context_tokens: int = 8192
    max_queries: int = 12
    latency_ms: int = 5000
    max_concurrency: int = 1
    overlay_policy: str = "include"
    fallback_allowed: bool = False
    retention_days: int = 30
    redact_query: bool = True


@dataclass(slots=True)
class RetrievalCapability:
    capability_id: str
    token_digest: str
    budget: RetrievalBudgetSnapshot
    issued_at: float
    expires_at: float
    revoked_at: float | None = None
    query_count: int = 0
    active_requests: int = 0
    deduplicated: dict[str, dict[str, Any]] = field(default_factory=dict)


class RetrievalCapabilityError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class RetrievalCapabilityRegistry:
    """Process-local live authority with artifact-backed, secret-free evidence."""

    def __init__(self, evidence_root: Path | None = None) -> None:
        self._capabilities: dict[str, RetrievalCapability] = {}
        self._by_digest: dict[str, str] = {}
        self._lock = threading.RLock()
        self._evidence: dict[str, list[dict[str, Any]]] = {}
        self.evidence_root = evidence_root or Path(
            "var/artifacts/retrieval-follow-up"
        )

    def issue(
        self, budget: RetrievalBudgetSnapshot, *, lifetime_seconds: int
    ) -> tuple[str, RetrievalCapability]:
        now = time.time()
        token = secrets.token_urlsafe(32)
        capability = RetrievalCapability(
            capability_id=f"rcap_{secrets.token_hex(12)}",
            token_digest=_digest(token),
            budget=budget,
            issued_at=now,
            expires_at=now + lifetime_seconds,
        )
        with self._lock:
            self._capabilities[capability.capability_id] = capability
            self._by_digest[capability.token_digest] = capability.capability_id
        return token, capability

    def resolve(
        self, token: str, *, host_id: str, session_id: str, run_id: str
    ) -> RetrievalCapability:
        with self._lock:
            capability_id = self._by_digest.get(_digest(token))
            capability = self._capabilities.get(capability_id or "")
            if capability is None:
                raise RetrievalCapabilityError("invalid", "Invalid retrieval capability.")
            if capability.revoked_at is not None:
                raise RetrievalCapabilityError("revoked", "Retrieval capability is revoked.")
            if time.time() >= capability.expires_at:
                raise RetrievalCapabilityError("expired", "Retrieval capability is expired.")
            expected = capability.budget
            if (host_id, session_id, run_id) != (
                expected.host_id,
                expected.session_id,
                expected.run_id,
            ):
                raise RetrievalCapabilityError(
                    "identity_mismatch",
                    "Retrieval capability does not belong to this host, session, and run.",
                )
            return capability

    def begin(self, capability: RetrievalCapability, tool_call_id: str) -> dict[str, Any] | None:
        with self._lock:
            duplicate = capability.deduplicated.get(tool_call_id)
            if duplicate is not None:
                return duplicate
            if capability.query_count >= capability.budget.max_queries:
                raise RetrievalCapabilityError(
                    "budget_exhausted", "Retrieval query-count budget is exhausted."
                )
            if capability.active_requests >= capability.budget.max_concurrency:
                raise RetrievalCapabilityError(
                    "concurrency_exceeded", "Retrieval concurrency budget is exhausted."
                )
            capability.query_count += 1
            capability.active_requests += 1
            return None

    def finish(
        self,
        capability: RetrievalCapability,
        tool_call_id: str,
        response: dict[str, Any],
    ) -> None:
        with self._lock:
            capability.active_requests = max(0, capability.active_requests - 1)
            capability.deduplicated[tool_call_id] = response

    def abort(self, capability: RetrievalCapability) -> None:
        with self._lock:
            capability.active_requests = max(0, capability.active_requests - 1)

    def revoke(self, capability_id: str) -> RetrievalCapability:
        with self._lock:
            capability = self._capabilities[capability_id]
            capability.revoked_at = time.time()
            return capability

    def status(self, capability_id: str) -> dict[str, Any]:
        with self._lock:
            capability = self._capabilities[capability_id]
            state = (
                "revoked"
                if capability.revoked_at is not None
                else "expired"
                if time.time() >= capability.expires_at
                else "active"
            )
            return {
                "capabilityId": capability.capability_id,
                "state": state,
                "expiresAt": capability.expires_at,
                "revokedAt": capability.revoked_at,
                "queryCount": capability.query_count,
                "maxQueries": capability.budget.max_queries,
                "requests": list(self._evidence.get(capability_id, ())),
            }

    def record(self, capability: RetrievalCapability, evidence: dict[str, Any]) -> str:
        directory = self.evidence_root / capability.budget.run_id
        directory.mkdir(parents=True, exist_ok=True)
        evidence_id = f"retrieval_{secrets.token_hex(12)}"
        path = directory / f"{evidence_id}.json"
        payload = {
            "schemaVersion": 1,
            "capabilityId": capability.capability_id,
            "budgetSnapshot": asdict(capability.budget),
            **evidence,
        }
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        with self._lock:
            self._evidence.setdefault(capability.capability_id, []).append(
                {
                    "evidenceRef": f"artifact://retrieval-follow-up/{capability.budget.run_id}/{evidence_id}",
                    "state": evidence.get("state"),
                    "correlation": evidence.get("correlation"),
                    "resultCount": evidence.get("resultCount", 0),
                    "contextBytes": evidence.get("contextBytes", 0),
                    "latencyMs": evidence.get("latencyMs"),
                    "delivery": evidence.get("delivery"),
                    "classification": evidence.get("classification"),
                }
            )
        return f"artifact://retrieval-follow-up/{capability.budget.run_id}/{evidence_id}"
