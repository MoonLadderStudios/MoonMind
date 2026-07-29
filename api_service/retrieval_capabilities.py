"""Ephemeral retrieval authority and bounded evidence for managed sessions.

The raw capability is returned once to the host.  Only its digest is retained;
durable evidence therefore cannot be used to replay retrieval authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

#: Grace added to a request lease beyond the capability latency ceiling so an
#: interrupted process releases its concurrency slot instead of wedging it.
REQUEST_LEASE_GRACE_SECONDS = 30

#: Durable root for capability authority, accounting, and bounded evidence.
#: The canonical Compose deployment mounts this path from a named volume so
#: recreating the API container does not destroy still-live capabilities.
STATE_ROOT_ENV_VAR = "MOONMIND_FOLLOWUP_RETRIEVAL_STATE_ROOT"
DEFAULT_STATE_ROOT = "var/retrieval-follow-up"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_state_root() -> Path:
    """Return the configured durable root for retrieval capability state."""
    configured = str(os.getenv(STATE_ROOT_ENV_VAR, "")).strip()
    return Path(configured or DEFAULT_STATE_ROOT)


@dataclass(frozen=True, slots=True)
class RetrievalBudgetSnapshot:
    tenant_id: str
    repository: str
    run_id: str
    workspace_id: str
    host_id: str
    session_id: str
    step_id: str
    workflow_id: str
    bridge_session_id: str
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
        self.evidence_root = evidence_root or default_state_root()
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
                    query_count INTEGER NOT NULL DEFAULT 0
                );
                -- One row per tool call: an expiring in-progress reservation
                -- first, then the terminal deduplicated response.  The
                -- reservation is the concurrency slot, so an interrupted
                -- process cannot leak an unrecoverable slot.
                CREATE TABLE IF NOT EXISTS retrieval_requests (
                    capability_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    lease_expires_at REAL,
                    response_json TEXT,
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
                CREATE TABLE IF NOT EXISTS retrieval_rate_windows (
                    capability_id TEXT NOT NULL,
                    window_started_at INTEGER NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (capability_id, window_started_at)
                );
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
        )

    @staticmethod
    def _lease_seconds(budget: RetrievalBudgetSnapshot) -> int:
        """Bound a reservation by the latency a request is allowed to consume."""
        return (
            math.ceil(budget.latency_ms / 1000) + REQUEST_LEASE_GRACE_SECONDS
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
        self,
        token: str,
        *,
        host_id: str,
        session_id: str,
        run_id: str,
        denial_context: dict[str, Any] | None = None,
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
                self._record_authorization_denial(
                    capability, "revoked", denial_context
                )
                raise RetrievalCapabilityError("revoked", "Retrieval capability is revoked.")
            if time.time() >= capability.expires_at:
                self._record_authorization_denial(
                    capability, "expired", denial_context
                )
                raise RetrievalCapabilityError("expired", "Retrieval capability is expired.")
            expected = capability.budget
            if (host_id, session_id, run_id) != (
                expected.host_id,
                expected.session_id,
                expected.run_id,
            ):
                self._record_authorization_denial(
                    capability, "identity_mismatch", denial_context
                )
                raise RetrievalCapabilityError(
                    "identity_mismatch",
                    "Retrieval capability does not belong to this host, session, and run.",
                )
            return capability

    def _record_authorization_denial(
        self,
        capability: RetrievalCapability,
        classification: str,
        correlation: dict[str, Any] | None,
    ) -> None:
        self.record(
            capability,
            {
                "state": "denied",
                "classification": classification,
                "correlation": correlation or {},
                "delivery": {"state": "not_delivered"},
            },
        )

    def begin(self, capability: RetrievalCapability, tool_call_id: str) -> dict[str, Any] | None:
        """Atomically reserve a tool call, or return its terminal response.

        The reservation is inserted in the same immediate transaction that
        checks the rate, query, and concurrency budgets, so a retry issued
        while the first attempt is still running can neither execute twice nor
        consume the budget twice.
        """
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time()
                # Reclaim reservations abandoned by an interrupted process.
                connection.execute(
                    """DELETE FROM retrieval_requests
                       WHERE capability_id = ? AND state = 'in_progress'
                         AND lease_expires_at <= ?""",
                    (capability.capability_id, now),
                )
                existing = connection.execute(
                    """SELECT state, response_json FROM retrieval_requests
                       WHERE capability_id = ? AND tool_call_id = ?""",
                    (capability.capability_id, tool_call_id),
                ).fetchone()
                if existing is not None:
                    if existing["state"] == "completed":
                        return json.loads(existing["response_json"])
                    raise RetrievalCapabilityError(
                        "duplicate_in_flight",
                        "An identical retrieval tool call is already in flight.",
                    )
                window_started_at = int(now // 60) * 60
                rate_row = connection.execute(
                    """SELECT request_count FROM retrieval_rate_windows
                       WHERE capability_id = ? AND window_started_at = ?""",
                    (capability.capability_id, window_started_at),
                ).fetchone()
                request_count = rate_row["request_count"] if rate_row else 0
                if request_count >= capability.budget.max_requests_per_minute:
                    raise RetrievalCapabilityError(
                        "rate_exceeded", "Retrieval request-rate budget is exhausted."
                    )
                row = connection.execute(
                    "SELECT query_count FROM retrieval_capabilities WHERE capability_id = ?",
                    (capability.capability_id,),
                ).fetchone()
                if row["query_count"] >= capability.budget.max_queries:
                    raise RetrievalCapabilityError(
                        "budget_exhausted", "Retrieval query-count budget is exhausted."
                    )
                active_requests = connection.execute(
                    """SELECT COUNT(*) AS active FROM retrieval_requests
                       WHERE capability_id = ? AND state = 'in_progress'""",
                    (capability.capability_id,),
                ).fetchone()["active"]
                if active_requests >= capability.budget.max_concurrency:
                    raise RetrievalCapabilityError(
                        "concurrency_exceeded", "Retrieval concurrency budget is exhausted."
                    )
                capability.query_count = row["query_count"] + 1
                connection.execute(
                    """UPDATE retrieval_capabilities SET query_count = ?
                       WHERE capability_id = ?""",
                    (capability.query_count, capability.capability_id),
                )
                connection.execute(
                    """INSERT INTO retrieval_requests
                       (capability_id, tool_call_id, state, lease_expires_at)
                       VALUES (?, ?, 'in_progress', ?)""",
                    (
                        capability.capability_id,
                        tool_call_id,
                        now + self._lease_seconds(capability.budget),
                    ),
                )
                connection.execute(
                    """INSERT INTO retrieval_rate_windows
                       (capability_id, window_started_at, request_count)
                       VALUES (?, ?, 1)
                       ON CONFLICT(capability_id, window_started_at)
                       DO UPDATE SET request_count = request_count + 1""",
                    (capability.capability_id, window_started_at),
                )
            return None

    def finish(
        self,
        capability: RetrievalCapability,
        tool_call_id: str,
        response: dict[str, Any],
    ) -> None:
        """Release the reservation and publish its terminal response."""
        with self._lock:
            capability.deduplicated[tool_call_id] = response
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO retrieval_requests
                       (capability_id, tool_call_id, state, lease_expires_at, response_json)
                       VALUES (?, ?, 'completed', NULL, ?)
                       ON CONFLICT(capability_id, tool_call_id) DO UPDATE SET
                           state = 'completed',
                           lease_expires_at = NULL,
                           response_json = excluded.response_json""",
                    (capability.capability_id, tool_call_id, json.dumps(response)),
                )

    def abort(self, capability: RetrievalCapability, tool_call_id: str) -> None:
        """Release a reservation that produced no terminal response."""
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """DELETE FROM retrieval_requests
                       WHERE capability_id = ? AND tool_call_id = ?
                         AND state = 'in_progress'""",
                    (capability.capability_id, tool_call_id),
                )

    def assert_active(self, capability_id: str) -> None:
        """Re-read durable authority so a result cannot outlive its capability.

        Called after retrieval returns but before the pack is stored or
        published, so a revocation, stop, delete, or cleanup that landed while
        the request was in flight still closes the authority.
        """
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT revoked_at, expires_at FROM retrieval_capabilities
                   WHERE capability_id = ?""",
                (capability_id,),
            ).fetchone()
        if row is None:
            raise RetrievalCapabilityError("invalid", "Invalid retrieval capability.")
        if row["revoked_at"] is not None:
            raise RetrievalCapabilityError(
                "revoked", "Retrieval capability was revoked while the request was in flight."
            )
        if time.time() >= row["expires_at"]:
            raise RetrievalCapabilityError(
                "expired", "Retrieval capability expired while the request was in flight."
            )

    def revoke(self, capability_id: str) -> RetrievalCapability:
        with self._lock:
            capability = self.get(capability_id)
            capability.revoked_at = time.time()
            with self._connect() as connection:
                connection.execute(
                    "UPDATE retrieval_capabilities SET revoked_at = ? WHERE capability_id = ?",
                    (capability.revoked_at, capability_id),
                )
            return capability

    @staticmethod
    def _require_exact_scope(
        *, run_id: str, host_id: str, session_id: str, step_id: str
    ) -> None:
        """Refuse a partial lifecycle scope instead of widening it to a wildcard.

        A missing identifier must never match every capability in the run: one
        incomplete session would otherwise revoke retrieval for its siblings.
        """
        missing = [
            name
            for name, value in (
                ("run_id", run_id),
                ("host_id", host_id),
                ("session_id", session_id),
                ("step_id", step_id),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise RetrievalCapabilityError(
                "incomplete_scope",
                "Retrieval lifecycle scope is incomplete; missing "
                + ", ".join(missing)
                + ".",
            )

    def live_scope_capability(
        self, *, run_id: str, host_id: str, session_id: str, step_id: str
    ) -> RetrievalCapability | None:
        """Return the live capability already owning a lifecycle scope, if any.

        Issuance consults this so a retried or repeated request cannot multiply
        the immutable query and rate allowance for one bridge session.
        """
        self._require_exact_scope(
            run_id=run_id, host_id=host_id, session_id=session_id, step_id=step_id
        )
        now = time.time()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM retrieval_capabilities
                   WHERE revoked_at IS NULL AND expires_at > ?
                   ORDER BY issued_at""",
                (now,),
            ).fetchall()
        for row in rows:
            budget = self._from_row(row).budget
            if (budget.run_id, budget.host_id, budget.session_id, budget.step_id) == (
                run_id,
                host_id,
                session_id,
                step_id,
            ):
                return self._from_row(row)
        return None

    def has_live_session_authority(self, *, session_id: str) -> bool:
        """Report whether any live capability still names an Omnigent session.

        Lifecycle boundaries use this to decide whether an unscopable session
        must block host mutation: authority that cannot be scoped precisely but
        is still live has to fail closed, while a session that provably owns no
        capability must not be blocked from cleanup.
        """
        key = str(session_id or "").strip()
        if not key:
            return False
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT budget_json FROM retrieval_capabilities
                   WHERE revoked_at IS NULL AND expires_at > ?""",
                (time.time(),),
            ).fetchall()
        return any(
            str(json.loads(row["budget_json"]).get("session_id") or "") == key
            for row in rows
        )

    def revoke_scope(
        self,
        *,
        run_id: str,
        host_id: str,
        session_id: str,
        step_id: str,
    ) -> list[str]:
        """Revoke every live capability owned by an exact lifecycle boundary."""
        self._require_exact_scope(
            run_id=run_id, host_id=host_id, session_id=session_id, step_id=step_id
        )
        revoked: list[str] = []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM retrieval_capabilities WHERE revoked_at IS NULL"
            ).fetchall()
            for row in rows:
                capability = self._from_row(row)
                budget = capability.budget
                if (
                    budget.run_id,
                    budget.host_id,
                    budget.session_id,
                    budget.step_id,
                ) != (run_id, host_id, session_id, step_id):
                    continue
                capability.revoked_at = time.time()
                connection.execute(
                    """UPDATE retrieval_capabilities SET revoked_at = ?
                       WHERE capability_id = ?""",
                    (capability.revoked_at, capability.capability_id),
                )
                self._capabilities[capability.capability_id] = capability
                revoked.append(capability.capability_id)
        return revoked

    def get(self, capability_id: str) -> RetrievalCapability:
        """Return a capability projection, hydrating it from durable state."""
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
            return capability

    def status(self, capability_id: str) -> dict[str, Any]:
        with self._lock:
            capability = self.get(capability_id)
            state = (
                "revoked"
                if capability.revoked_at is not None
                else "expired"
                if time.time() >= capability.expires_at
                else "active"
            )
            now = time.time()
            window_started_at = int(now // 60) * 60
            with self._connect() as connection:
                rate_row = connection.execute(
                    """SELECT request_count FROM retrieval_rate_windows
                       WHERE capability_id = ? AND window_started_at = ?""",
                    (capability_id, window_started_at),
                ).fetchone()
                active_requests = connection.execute(
                    """SELECT COUNT(*) AS active FROM retrieval_requests
                       WHERE capability_id = ? AND state = 'in_progress'
                         AND lease_expires_at > ?""",
                    (capability_id, now),
                ).fetchone()["active"]
            return {
                "capabilityId": capability.capability_id,
                "state": state,
                "expiresAt": capability.expires_at,
                "revokedAt": capability.revoked_at,
                "queryCount": capability.query_count,
                "maxQueries": capability.budget.max_queries,
                "activeRequests": active_requests,
                "maxConcurrency": capability.budget.max_concurrency,
                "requestsInCurrentMinute": (
                    rate_row["request_count"] if rate_row is not None else 0
                ),
                "maxRequestsPerMinute": capability.budget.max_requests_per_minute,
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

    def _result_path(self, capability_id: str, run_id: str, tool_call_id: str) -> Path:
        """Namespace a stored pack by capability so sessions cannot collide.

        Two sessions in one run may legitimately reuse a ``tool_call_id``; a
        run-scoped filename alone would let the later result overwrite the
        earlier one and leave both evidence records pointing at the same pack.
        """
        return (
            self.evidence_root
            / run_id
            / "results"
            / capability_id
            / f"result_{_digest(tool_call_id)[:24]}.json"
        )

    def store_result(
        self, capability: RetrievalCapability, tool_call_id: str, payload: dict[str, Any]
    ) -> str:
        """Persist a large ContextPack outside bridge and workflow payloads.

        Returns a reference the host can actually dereference: the
        capability-authorized result endpoint served by the Retrieval Gateway.
        """
        path = self._result_path(
            capability.capability_id, capability.budget.run_id, tool_call_id
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return (
            f"/retrieval/capabilities/{quote(capability.capability_id, safe='')}"
            f"/results/{quote(tool_call_id, safe='')}"
        )

    def read_result(self, capability: RetrievalCapability, tool_call_id: str) -> dict[str, Any]:
        """Return a stored ContextPack for the capability that produced it."""
        path = self._result_path(
            capability.capability_id, capability.budget.run_id, tool_call_id
        )
        if not path.is_file():
            raise KeyError(tool_call_id)
        return json.loads(path.read_text(encoding="utf-8"))

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
                """SELECT response_json FROM retrieval_requests
                   WHERE capability_id = ? AND tool_call_id = ?
                     AND state = 'completed'""",
                (capability_id, tool_call_id),
            ).fetchone()
            if row is None:
                raise KeyError(tool_call_id)
            response = json.loads(row["response_json"])
            response["deliveryState"] = state
            connection.execute(
                """UPDATE retrieval_requests SET response_json = ?
                   WHERE capability_id = ? AND tool_call_id = ?""",
                (json.dumps(response), capability_id, tool_call_id),
            )
            capability = self._capabilities.get(capability_id)
            if capability is not None:
                capability.deduplicated[tool_call_id] = response
            return response
