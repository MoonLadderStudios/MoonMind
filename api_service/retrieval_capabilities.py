"""Ephemeral retrieval authority and bounded evidence for managed sessions.

The raw capability is returned once to the host.  Only its digest is retained;
durable evidence therefore cannot be used to replay retrieval authority.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
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
    embedding_timeout_ms: int = 2000
    search_timeout_ms: int = 3000
    max_concurrency: int = 1
    max_requests_per_minute: int = 12
    overlay_max_age_seconds: int = 3600
    stale_overlay_allowed: bool = False
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
    """Durable live authority with artifact-backed, secret-free evidence.

    SQLite is the authority for capability lifecycle, accounting and
    deduplication.  The in-memory objects are short-lived projections only.
    """

    def __init__(self, evidence_root: Path | None = None) -> None:
        self._capabilities: dict[str, RetrievalCapability] = {}
        self._by_digest: dict[str, str] = {}
        self._lock = threading.RLock()
        self._evidence: dict[str, list[dict[str, Any]]] = {}
        self.evidence_root = evidence_root or Path(
            "var/artifacts/retrieval-follow-up"
        )
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self._database_path = self.evidence_root / "capabilities.sqlite3"
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS retrieval_capabilities (
                    capability_id TEXT PRIMARY KEY,
                    token_digest TEXT UNIQUE NOT NULL,
                    budget_json TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    active_requests INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS retrieval_deduplication (
                    capability_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (capability_id, tool_call_id)
                );
                CREATE TABLE IF NOT EXISTS retrieval_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_retrieval_evidence_capability
                    ON retrieval_evidence(capability_id, created_at);
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> RetrievalCapability:
        budget_data = json.loads(row["budget_json"])
        budget_data["collections"] = tuple(budget_data["collections"])
        budget_data["filters"] = tuple(tuple(item) for item in budget_data["filters"])
        return RetrievalCapability(
            capability_id=row["capability_id"],
            token_digest=row["token_digest"],
            budget=RetrievalBudgetSnapshot(**budget_data),
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            query_count=row["query_count"],
            active_requests=row["active_requests"],
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
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO retrieval_capabilities
                       (capability_id, token_digest, budget_json, issued_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        capability.capability_id,
                        capability.token_digest,
                        json.dumps(asdict(budget), sort_keys=True),
                        capability.issued_at,
                        capability.expires_at,
                    ),
                )
        return token, capability

    def resolve(
        self, token: str, *, host_id: str, session_id: str, run_id: str
    ) -> RetrievalCapability:
        with self._lock:
            token_digest = _digest(token)
            capability_id = self._by_digest.get(token_digest)
            capability = self._capabilities.get(capability_id or "")
            if capability is None:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM retrieval_capabilities WHERE token_digest = ?",
                        (token_digest,),
                    ).fetchone()
                if row is not None:
                    capability = self._from_row(row)
                    self._capabilities[capability.capability_id] = capability
                    self._by_digest[token_digest] = capability.capability_id
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
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                duplicate = connection.execute(
                    """SELECT response_json FROM retrieval_deduplication
                       WHERE capability_id = ? AND tool_call_id = ?""",
                    (capability.capability_id, tool_call_id),
                ).fetchone()
                if duplicate is not None:
                    return json.loads(duplicate["response_json"])
                row = connection.execute(
                    "SELECT query_count, active_requests FROM retrieval_capabilities WHERE capability_id = ?",
                    (capability.capability_id,),
                ).fetchone()
                if row["query_count"] >= capability.budget.max_queries:
                    raise RetrievalCapabilityError(
                        "budget_exhausted", "Retrieval query-count budget is exhausted."
                    )
                if row["active_requests"] >= capability.budget.max_concurrency:
                    raise RetrievalCapabilityError(
                        "concurrency_exceeded", "Retrieval concurrency budget is exhausted."
                    )
                capability.query_count = row["query_count"] + 1
                capability.active_requests = row["active_requests"] + 1
                connection.execute(
                    """UPDATE retrieval_capabilities
                       SET query_count = ?, active_requests = ?
                       WHERE capability_id = ?""",
                    (capability.query_count, capability.active_requests, capability.capability_id),
                )
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
            with self._connect() as connection:
                connection.execute(
                    """UPDATE retrieval_capabilities SET active_requests = ?
                       WHERE capability_id = ?""",
                    (capability.active_requests, capability.capability_id),
                )
                connection.execute(
                    """INSERT OR REPLACE INTO retrieval_deduplication
                       (capability_id, tool_call_id, response_json) VALUES (?, ?, ?)""",
                    (capability.capability_id, tool_call_id, json.dumps(response)),
                )

    def abort(self, capability: RetrievalCapability) -> None:
        with self._lock:
            capability.active_requests = max(0, capability.active_requests - 1)
            with self._connect() as connection:
                connection.execute(
                    """UPDATE retrieval_capabilities SET active_requests = ?
                       WHERE capability_id = ?""",
                    (capability.active_requests, capability.capability_id),
                )

    def revoke(self, capability_id: str) -> RetrievalCapability:
        with self._lock:
            capability = self._capabilities.get(capability_id)
            if capability is None:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM retrieval_capabilities WHERE capability_id = ?",
                        (capability_id,),
                    ).fetchone()
                if row is None:
                    raise KeyError(capability_id)
                capability = self._from_row(row)
                self._capabilities[capability_id] = capability
            capability.revoked_at = time.time()
            with self._connect() as connection:
                connection.execute(
                    "UPDATE retrieval_capabilities SET revoked_at = ? WHERE capability_id = ?",
                    (capability.revoked_at, capability_id),
                )
            return capability

    def status(self, capability_id: str) -> dict[str, Any]:
        with self._lock:
            capability = self._capabilities.get(capability_id)
            if capability is None:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM retrieval_capabilities WHERE capability_id = ?",
                        (capability_id,),
                    ).fetchone()
                if row is None:
                    raise KeyError(capability_id)
                capability = self._from_row(row)
                self._capabilities[capability_id] = capability
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
                "requests": self._evidence_summaries(capability_id),
            }

    def _evidence_summaries(self, capability_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT summary_json FROM retrieval_evidence
                   WHERE capability_id = ? ORDER BY created_at""",
                (capability_id,),
            ).fetchall()
        return [json.loads(row["summary_json"]) for row in rows]

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
            summary = {
                "evidenceRef": f"artifact://retrieval-follow-up/{capability.budget.run_id}/{evidence_id}",
                "state": evidence.get("state"),
                "correlation": evidence.get("correlation"),
                "resultCount": evidence.get("resultCount", 0),
                "contextBytes": evidence.get("contextBytes", 0),
                "latencyMs": evidence.get("latencyMs"),
                "delivery": evidence.get("delivery"),
                "classification": evidence.get("classification"),
            }
            self._evidence.setdefault(capability.capability_id, []).append(summary)
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO retrieval_evidence
                       (evidence_id, capability_id, run_id, summary_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        evidence_id,
                        capability.capability_id,
                        capability.budget.run_id,
                        json.dumps(summary, sort_keys=True),
                        time.time(),
                    ),
                )
        return f"artifact://retrieval-follow-up/{capability.budget.run_id}/{evidence_id}"

    def store_result(
        self, capability: RetrievalCapability, tool_call_id: str, payload: dict[str, Any]
    ) -> str:
        """Persist a large ContextPack outside bridge and workflow payloads."""
        directory = self.evidence_root / capability.budget.run_id / "results"
        directory.mkdir(parents=True, exist_ok=True)
        result_id = f"result_{_digest(tool_call_id)[:24]}"
        path = directory / f"{result_id}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return (
            f"artifact://retrieval-follow-up/{capability.budget.run_id}"
            f"/results/{result_id}"
        )

    def acknowledge_delivery(
        self,
        capability_id: str,
        tool_call_id: str,
        *,
        state: str,
    ) -> dict[str, Any]:
        """Apply the bridge's authoritative delivery outcome to a typed result."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT response_json FROM retrieval_deduplication
                   WHERE capability_id = ? AND tool_call_id = ?""",
                (capability_id, tool_call_id),
            ).fetchone()
            if row is None:
                raise KeyError(tool_call_id)
            response = json.loads(row["response_json"])
            response["deliveryState"] = state
            connection.execute(
                """UPDATE retrieval_deduplication SET response_json = ?
                   WHERE capability_id = ? AND tool_call_id = ?""",
                (json.dumps(response), capability_id, tool_call_id),
            )
            capability = self._capabilities.get(capability_id)
            if capability is not None:
                capability.deduplicated[tool_call_id] = response
            return response
