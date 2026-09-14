"""Read-only conversational deployment overview (MoonMind#424).

Bounded first increment: typed, scoped read projections that let an
authorized operator ask what is running, what is waiting, what recently
failed, and which deployment checks need attention — backed by existing
workflow evidence and the allowlisted ops-diagnostics collector.

Non-goals (enforced in code, not prose):

- No autonomous administration, arbitrary shell/SQL/URL access, environment
  dumps, raw Docker inspect, or reusable credentials reach the assistant.
- No new always-on chat/metrics service, agent controller, or mutation path.
  Mutation verbs (pause/cancel/retry/approve/deploy/credential rotation)
  leave this read-only flow and link to the existing explicit operations.
- Per-workflow live interaction stays on the native chat binding owned by
  ``docs/UI/WorkflowChatPanel.md``; this module only links to it.

All helpers are pure over caller-supplied, already-authorized source data so
production wiring passes real dispatch-path evidence while tests use
deterministic fixtures. Logs, repository text, exception messages, and tool
output are treated as untrusted data: redacted/allowlisted before model
disclosure and scanned for prompt-injection patterns.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from moonmind.utils.logging import redact_sensitive_payload

DEPLOYMENT_OVERVIEW_QUESTION_SET = (
    "running",
    "waiting",
    "recent_failure",
    "deployment_observation",
)

#: Verbs that must leave the read-only flow. A question containing one of
#: these is answered with a routing response, never a side effect.
MUTATION_VERBS = (
    "pause",
    "cancel",
    "retry",
    "approve",
    "deploy",
    "rotate",
    "delete",
    "restart",
)

#: Observation outcomes for every answer field. ``unavailable`` means the
#: probe was requested but produced no usable evidence; ``not_requested``
#: means the source was never queried in this turn.
OBSERVATION_OUTCOMES = frozenset({"succeeded", "failed", "unavailable", "not_requested"})

#: Services that only exist under optional Compose profiles. Absence or a
#: stopped state here is not a health failure.
OPTIONAL_PROFILE_SERVICES = frozenset({"temporal-ui", "docker-proxy"})

#: One-shot / init containers that are expected to exit after success.
ONE_SHOT_SERVICES = frozenset({"init-db"})

MAX_WORKFLOWS_PER_ANSWER = 25
MAX_LOG_TAIL_LINES = 200
MAX_QUESTION_CHARS = 2000
CACHE_FRESHNESS_SECONDS = 120

_OPERATOR_ROLES = frozenset({"admin", "operator"})

_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s*:\s*you are", re.IGNORECASE),
    re.compile(r"exfiltrat|send\s+(the\s+)?secret|reveal\s+(the\s+)?(secret|key|token|credential)", re.IGNORECASE),
    re.compile(r"(run|execute)\s+(arbitrary|any)\s+command", re.IGNORECASE),
)

_ARBITRARY_ACCESS_PATTERNS = (
    re.compile(r"\b(select|insert|update|delete|drop)\b\s+.+\bfrom\b", re.IGNORECASE),
    re.compile(r"(curl|wget|ssh|docker\s+(exec|inspect|run))\s+", re.IGNORECASE),
    re.compile(r"(printenv|env\b.*dump|/proc/|/etc/(passwd|shadow))", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class OverviewPrincipal:
    """Server-resolved caller identity for scope decisions."""

    subject: str
    roles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    workflows_visible: tuple[str, ...] | None = None
    """None means deployment-wide visibility (operator); otherwise an allowlist."""


def resolve_principal(raw: Mapping[str, Any]) -> OverviewPrincipal:
    """Resolve a principal from server-side auth state (never client claims)."""
    roles = tuple(str(r) for r in (raw.get("roles") or ()) if str(r).strip())
    capabilities = tuple(str(c) for c in (raw.get("capabilities") or ()) if str(c).strip())
    visible = raw.get("workflows_visible")
    visible_tuple = (
        tuple(str(w) for w in visible if str(w).strip())
        if isinstance(visible, (list, tuple))
        else None
    )
    return OverviewPrincipal(
        subject=str(raw.get("subject") or "unknown"),
        roles=roles,
        capabilities=capabilities,
        workflows_visible=visible_tuple,
    )


def is_operator(principal: OverviewPrincipal) -> bool:
    """Deployment-wide details remain operator-only."""
    if principal.workflows_visible is not None:
        return False
    if _OPERATOR_ROLES.intersection(r.lower() for r in principal.roles):
        return True
    caps = {c.lower() for c in principal.capabilities}
    return bool({"deployment_control", "docker_admin"} & caps)


def classify_question(text: str) -> str | None:
    """Map free-form operator text to one canonical question, or None."""
    normalized = str(text or "").strip()[:MAX_QUESTION_CHARS].lower()
    if not normalized:
        return None
    if any(verb in normalized for verb in ("running", "active", "in progress", "executing")):
        return "running"
    if any(verb in normalized for verb in ("waiting", "queued", "pending", "capacity", "blocked on")):
        return "waiting"
    if any(verb in normalized for verb in ("fail", "error", "recently failed", "last failure", "broken")):
        return "recent_failure"
    if any(
        verb in normalized
        for verb in ("deploy", "health", "check", "diagnos", "stack", "container", "needs attention")
    ):
        return "deployment_observation"
    return None


def is_mutation_request(text: str) -> str | None:
    """Return the matched mutation verb, or None when the text is read-only."""
    normalized = str(text or "").lower()
    for verb in MUTATION_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", normalized):
            return verb
    return None


def detect_prompt_injection(text: str) -> bool:
    """Detect instruction-override attempts in untrusted log/tool content."""
    candidate = str(text or "")
    return any(pattern.search(candidate) for pattern in _INJECTION_PATTERNS)


def requests_arbitrary_access(text: str) -> bool:
    """Detect requests for free-form command/SQL/URL/env/secret access."""
    candidate = str(text or "")
    lowered = candidate.lower()
    if any(
        marker in lowered
        for marker in ("arbitrary command", "raw docker", "environment dump", "printenv", "credential")
    ):
        return True
    return any(pattern.search(candidate) for pattern in _ARBITRARY_ACCESS_PATTERNS)


def sanitize_untrusted_text(text: str, *, max_chars: int = 2000) -> str:
    """Redact sensitive values and bound untrusted text before disclosure.

    Instruction-override patterns are removed (not passed through), so
    injected log/tool content cannot widen assistant authority.
    """
    redacted = redact_sensitive_payload(str(text or ""))
    cleaned = str(redacted) if isinstance(redacted, str) else str(text or "")
    if detect_prompt_injection(cleaned):
        for pattern in _INJECTION_PATTERNS:
            cleaned = pattern.sub("[removed possible instruction-override]", cleaned)
        cleaned = "[untrusted content: instruction patterns removed] " + cleaned
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…[truncated]"
    return cleaned


def workflow_detail_url(workflow_id: str) -> str:
    """Link to the existing Workflow Detail surface (never rebuilt here)."""
    safe = re.sub(r"[^A-Za-z0-9:_\-./]", "", str(workflow_id or ""))[:200]
    return f"/workflows/{safe}"


def workflow_chat_url(workflow_id: str) -> str:
    """Link to the existing native chat binding for one active workflow."""
    safe = re.sub(r"[^A-Za-z0-9:_\-./]", "", str(workflow_id or ""))[:200]
    return f"/workflows/{safe}/chat"


def _now_ms(now: float | None) -> int:
    return int((now if now is not None else time.time()) * 1000)


def _freshness(collected_at_ms: int | None, now_ms: int) -> str:
    if collected_at_ms is None:
        return "stale: never collected in this scope"
    age_s = max(0, (now_ms - collected_at_ms) / 1000)
    if age_s <= CACHE_FRESHNESS_SECONDS:
        return f"fresh: collected {int(age_s)}s ago (ttl {CACHE_FRESHNESS_SECONDS}s)"
    return f"stale: collected {int(age_s)}s ago, exceeds ttl {CACHE_FRESHNESS_SECONDS}s"


def _field(
    *,
    owner: str,
    permission: str,
    outcome: str,
    value: Any,
    evidence_ref: str | None,
    collected_at_ms: int | None,
    now_ms: int,
    stale: bool = False,
) -> dict[str, Any]:
    if outcome not in OBSERVATION_OUTCOMES:
        outcome = "unavailable"
    if stale and outcome == "succeeded":
        outcome = "unavailable"
    return {
        "owner": owner,
        "permission": permission,
        "outcome": outcome,
        "value": value,
        "evidenceRef": evidence_ref,
        "collectedAt": collected_at_ms,
        "freshness": _freshness(collected_at_ms, now_ms),
    }


def _scope_workflows(
    principal: OverviewPrincipal,
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], bool]:
    """Apply per-principal scoping. Returns (visible, denied_any)."""
    if is_operator(principal):
        return list(records), False
    allowed = set(principal.workflows_visible or ())
    visible = [r for r in records if str(r.get("workflowId") or r.get("id") or "") in allowed]
    return visible, len(visible) != len(records)


def answer_running(
    principal: OverviewPrincipal,
    workflows: Sequence[Mapping[str, Any]],
    *,
    collected_at_ms: int | None = None,
    now: float | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Answer 'what is running' from authorized workflow list/detail evidence."""
    now_ms = _now_ms(now)
    visible, denied = _scope_workflows(principal, workflows)
    running = [
        r for r in visible if str(r.get("status") or "").lower() in {"running", "executing", "active"}
    ][:MAX_WORKFLOWS_PER_ANSWER]
    items = [
        {
            "workflowId": str(r.get("workflowId") or r.get("id") or ""),
            "status": str(r.get("status") or ""),
            "detailUrl": workflow_detail_url(str(r.get("workflowId") or r.get("id") or "")),
            "chatUrl": workflow_chat_url(str(r.get("workflowId") or r.get("id") or "")),
        }
        for r in running
    ]
    permission = "operator:deployment-wide" if is_operator(principal) else "user:own-workflows"
    return {
        "question": "running",
        "scoped": denied,
        "fields": {
            "workflows": _field(
                owner="workflow list/detail API",
                permission=permission,
                outcome="succeeded" if collected_at_ms is not None else "unavailable",
                value=items,
                evidence_ref=evidence_ref,
                collected_at_ms=collected_at_ms,
                now_ms=now_ms,
            )
        },
        "observation": f"{len(items)} running workflow(s) visible to {principal.subject}.",
        "hypotheses": [],
        "nextChecks": [] if items else ["Check the workflow list for queued or recently terminal work."],
    }


def answer_waiting(
    principal: OverviewPrincipal,
    workflows: Sequence[Mapping[str, Any]],
    *,
    collected_at_ms: int | None = None,
    now: float | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Answer 'what is waiting' from recorded wait reasons (never zero backlog from missing telemetry)."""
    now_ms = _now_ms(now)
    visible, denied = _scope_workflows(principal, workflows)
    waiting = [
        r
        for r in visible
        if str(r.get("status") or "").lower() in {"waiting", "queued", "pending", "capacity_wait"}
    ][:MAX_WORKFLOWS_PER_ANSWER]
    unknown_wait = [r for r in waiting if not str(r.get("waitReason") or "").strip()]
    items = [
        {
            "workflowId": str(r.get("workflowId") or r.get("id") or ""),
            "waitReason": str(r.get("waitReason") or "recorded wait reason unavailable"),
            "waitReasonRecorded": bool(str(r.get("waitReason") or "").strip()),
            "detailUrl": workflow_detail_url(str(r.get("workflowId") or r.get("id") or "")),
        }
        for r in waiting
    ]
    outcome = "succeeded" if collected_at_ms is not None else "unavailable"
    if collected_at_ms is None:
        note = "Missing wait telemetry is reported as unavailable, not as zero backlog."
    elif unknown_wait:
        note = (
            f"{len(unknown_wait)} waiting workflow(s) have no recorded wait reason; "
            "backlog size is reported from observed records only."
        )
    else:
        note = f"{len(items)} waiting workflow(s) with recorded wait reasons."
    permission = "operator:deployment-wide" if is_operator(principal) else "user:own-workflows"
    return {
        "question": "waiting",
        "scoped": denied,
        "fields": {
            "waiting": _field(
                owner="workflow scheduler / capacity wait ledger",
                permission=permission,
                outcome=outcome,
                value=items,
                evidence_ref=evidence_ref,
                collected_at_ms=collected_at_ms,
                now_ms=now_ms,
            )
        },
        "observation": note,
        "hypotheses": [],
        "nextChecks": ["Inspect the capacity wait ledger for the oldest waiting entry."] if items else [],
    }


def answer_recent_failure(
    principal: OverviewPrincipal,
    terminal_outcomes: Sequence[Mapping[str, Any]],
    *,
    collected_at_ms: int | None = None,
    now: float | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Answer 'what recently failed' from recorded terminal outcomes with linked evidence."""
    now_ms = _now_ms(now)
    visible, denied = _scope_workflows(principal, terminal_outcomes)
    failures = [
        r
        for r in visible
        if str(r.get("outcome") or r.get("status") or "").lower()
        in {"failed", "error", "execution_error", "terminal_failure"}
    ][:MAX_WORKFLOWS_PER_ANSWER]
    items = [
        {
            "workflowId": str(r.get("workflowId") or r.get("id") or ""),
            "outcome": str(r.get("outcome") or r.get("status") or ""),
            "summary": sanitize_untrusted_text(str(r.get("summary") or ""), max_chars=500),
            "detailUrl": workflow_detail_url(str(r.get("workflowId") or r.get("id") or "")),
            "evidenceRef": r.get("artifactRef") or evidence_ref,
        }
        for r in failures
    ]
    permission = "operator:deployment-wide" if is_operator(principal) else "user:own-workflows"
    return {
        "question": "recent_failure",
        "scoped": denied,
        "fields": {
            "failures": _field(
                owner="workflow terminal-evidence ledger",
                permission=permission,
                outcome="succeeded" if collected_at_ms is not None else "unavailable",
                value=items,
                evidence_ref=evidence_ref,
                collected_at_ms=collected_at_ms,
                now_ms=now_ms,
            )
        },
        "observation": f"{len(items)} recent failure(s) visible to {principal.subject}. Summaries are observations, not root-cause diagnoses.",
        "hypotheses": [],
        "nextChecks": ["Open the linked Workflow Detail evidence for the newest failure."] if items else [],
    }


def classify_collector_include(include: str, result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Truthfully relabel one collector include into a supported observation.

    A running container (``docker compose ps``) proves process presence only —
    never API reachability, worker queue polling, artifact round-trip success,
    or measured CPU/memory availability. Optional profile-gated services and
    one-shot init containers are reported distinctly from real health.
    """
    status = str((result or {}).get("status") or "").strip().upper()
    if not result or status in {"", "FAILED"}:
        return {
            "observation": "unavailable",
            "outcome": "unavailable",
            "label": f"{include}: probe produced no usable evidence",
        }
    if include in {"api_health", "worker_health", "temporal_connectivity", "artifact_store_health"}:
        return {
            "observation": "process_presence_only",
            "outcome": "succeeded",
            "label": (
                f"{include}: container presence observed via compose ps; "
                "application reachability NOT proven (requires an app-level probe)"
            ),
        }
    if include == "disk_memory_cpu":
        return {
            "observation": "storage_reporting_only",
            "outcome": "succeeded",
            "label": (
                f"{include}: Docker storage reporting observed; "
                "measured CPU/memory availability NOT proven"
            ),
        }
    return {"observation": "collected", "outcome": "succeeded", "label": f"{include}: collected"}


def classify_container_state(service: str, state: str, health: str) -> str:
    """Distinguish stopped/optional/one-shot containers from confirmed health."""
    name = str(service or "").strip()
    state_l = str(state or "").lower()
    health_l = str(health or "").lower()
    if name in OPTIONAL_PROFILE_SERVICES and ("exit" in state_l or not state_l or "not found" in state_l):
        return "optional_absent"
    if name in ONE_SHOT_SERVICES and ("exit" in state_l or "complete" in state_l):
        return "one_shot_complete"
    if "running" in state_l and health_l in {"", "healthy", "none", "starting"}:
        return "running"
    if "running" in state_l:
        return "running_degraded"
    return "stopped"


def answer_deployment_observation(
    principal: OverviewPrincipal,
    diagnosis: Mapping[str, Any],
    *,
    collected_at_ms: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Answer 'which deployment checks need attention' for operators only.

    Ordinary users receive a denied projection: deployment-wide diagnostics
    never leak through this path, including via cache reuse or stale reads.
    """
    now_ms = _now_ms(now)
    if not is_operator(principal):
        return {
            "question": "deployment_observation",
            "scoped": True,
            "fields": {
                "diagnosis": _field(
                    owner="deployment-control worker",
                    permission="denied: operator-only",
                    outcome="unavailable",
                    value=[],
                    evidence_ref=None,
                    collected_at_ms=None,
                    now_ms=now_ms,
                )
            },
            "observation": "Deployment-wide diagnostics require the operator role; no deployment data is disclosed.",
            "hypotheses": [],
            "nextChecks": ["Ask about your own workflows instead."],
        }
    evidence = diagnosis.get("evidence") if isinstance(diagnosis, Mapping) else None
    evidence = evidence if isinstance(evidence, Mapping) else {}
    checks: list[dict[str, Any]] = []
    for include, result in evidence.items():
        classified = classify_collector_include(
            str(include), result if isinstance(result, Mapping) else None
        )
        checks.append(
            {
                "include": str(include),
                "classification": classified["label"],
                "outcome": classified["outcome"],
            }
        )
    succeeded = sum(1 for c in checks if c["outcome"] == "succeeded")
    stale = collected_at_ms is not None and (now_ms - collected_at_ms) / 1000 > CACHE_FRESHNESS_SECONDS
    if not checks:
        summary = "No deployment observations were collected in this scope; no all-clear is claimed."
    elif succeeded < len(checks):
        summary = (
            f"Partial probe: {succeeded}/{len(checks)} check(s) produced evidence. "
            "No global all-clear is claimed from a partial probe."
        )
    else:
        summary = (
            f"{succeeded}/{len(checks)} check(s) produced container-presence evidence. "
            "Presence is not proof of application health; see per-check classifications."
        )
    if stale:
        summary += " Cached observations are stale and re-collection is required."
    return {
        "question": "deployment_observation",
        "scoped": False,
        "fields": {
            "diagnosis": _field(
                owner="deployment-control worker (moonmind.ops_diagnose_stack)",
                permission="operator:deployment-control",
                outcome="succeeded" if checks and not stale else "unavailable",
                value=checks,
                evidence_ref=str(diagnosis.get("artifactRef") or diagnosis.get("artifact_ref") or "")
                or None,
                collected_at_ms=collected_at_ms,
                now_ms=now_ms,
                stale=stale,
            )
        },
        "observation": summary,
        "hypotheses": [],
        "nextChecks": ["Re-run the allowlisted diagnostics when observations are stale or partial."],
    }


def answer_question(
    principal: OverviewPrincipal,
    text: str,
    sources: Mapping[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Route one operator question through the read-only flow (no side effects).

    Mutation requests, arbitrary-access requests, and unscoped deployment
    questions return safe routing/denial responses. This function performs no
    workflow-control, deployment, credential, or publication mutation.
    """
    question = str(text or "").strip()[:MAX_QUESTION_CHARS]
    mutation = is_mutation_request(question)
    if mutation:
        return {
            "question": None,
            "readOnly": True,
            "refused": True,
            "reasonCode": "mutation_requires_explicit_operation",
            "observation": (
                f"Read-only overview cannot '{mutation}'. Use the existing explicit "
                "operation (pause/cancel/retry/approve/deploy/credential rotation) "
                "with its authorization, confirmation, and expected-state checks."
            ),
            "links": [],
            "hypotheses": [],
            "nextChecks": [],
        }
    if requests_arbitrary_access(question):
        return {
            "question": None,
            "readOnly": True,
            "refused": True,
            "reasonCode": "arbitrary_access_denied",
            "observation": (
                "Free-form commands, SQL, arbitrary URLs, environment dumps, raw "
                "Docker inspect, and credential disclosure are outside this "
                "read-only overview and are never forwarded to a shell."
            ),
            "links": [],
            "hypotheses": [],
            "nextChecks": ["Ask one of the supported questions: running, waiting, recent failures, deployment checks."],
        }
    kind = classify_question(question)
    if kind is None:
        return {
            "question": None,
            "readOnly": True,
            "refused": True,
            "reasonCode": "unsupported_question",
            "observation": (
                "Supported questions: what is running, what is waiting, what "
                "recently failed, which deployment checks need attention."
            ),
            "links": [],
            "hypotheses": [],
            "nextChecks": [],
        }
    collected = sources.get("collectedAtMs")
    collected_ms = int(collected) if isinstance(collected, (int, float)) else None
    evidence_ref = sources.get("evidenceRef")
    evidence_ref = str(evidence_ref) if evidence_ref else None
    if kind == "running":
        answer = answer_running(
            principal,
            sources.get("workflows") or (),
            collected_at_ms=collected_ms,
            now=now,
            evidence_ref=evidence_ref,
        )
    elif kind == "waiting":
        answer = answer_waiting(
            principal,
            sources.get("workflows") or (),
            collected_at_ms=collected_ms,
            now=now,
            evidence_ref=evidence_ref,
        )
    elif kind == "recent_failure":
        answer = answer_recent_failure(
            principal,
            sources.get("terminalOutcomes") or sources.get("workflows") or (),
            collected_at_ms=collected_ms,
            now=now,
            evidence_ref=evidence_ref,
        )
    else:
        answer = answer_deployment_observation(
            principal,
            sources.get("diagnosis") or {},
            collected_at_ms=collected_ms,
            now=now,
        )
    answer["readOnly"] = True
    answer["refused"] = False
    return answer


def _overview_auth_principal(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the server-resolved auth principal from handler context."""
    for key in ("authenticated_principal", "auth_principal", "principal"):
        candidate = context.get(key)
        if isinstance(candidate, Mapping) and candidate:
            return candidate
    return None


def _overview_sources_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Build answer_question sources from server-supplied context only.

    Client inputs never supply workflow/diagnosis evidence: inputs carry the
    question text alone, while the deployment-control worker injects the
    already-authorized projections into context.
    """
    workflows = context.get("overview_workflows", context.get("workflows", ()))
    terminal = context.get(
        "overview_terminal_outcomes",
        context.get("terminalOutcomes", context.get("terminal_outcomes", ())),
    )
    diagnosis = context.get("overview_diagnosis", context.get("diagnosis", {}))
    collected = context.get(
        "overview_collected_at_ms",
        context.get("collectedAtMs", context.get("collected_at_ms")),
    )
    evidence_ref = context.get(
        "overview_evidence_ref",
        context.get("evidenceRef", context.get("evidence_ref")),
    )
    return {
        "workflows": workflows if isinstance(workflows, (list, tuple)) else (),
        "terminalOutcomes": terminal if isinstance(terminal, (list, tuple)) else (),
        "diagnosis": diagnosis if isinstance(diagnosis, Mapping) else {},
        "collectedAtMs": collected if isinstance(collected, (int, float)) else None,
        "evidenceRef": str(evidence_ref) if evidence_ref else None,
    }


def build_deployment_overview_handler() -> Any:
    """Build the ``mm.tool.execute`` handler for ``moonmind.deployment_overview``.

    Server-side principal resolution only: the caller identity comes from
    handler context (populated by the trusted worker from auth state), never
    from tool inputs. Sources likewise come from context. The read path
    performs no workflow-control, deployment, credential, or publication
    mutation; every result carries an explicit audit record proving that.
    """
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult

    async def _handler(
        inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
    ) -> ToolResult:
        ctx = dict(context or {})
        auth_raw = _overview_auth_principal(ctx)
        if auth_raw is None:
            raise ToolFailure(
                error_code="PERMISSION_DENIED",
                message="Deployment overview requires server-resolved authentication.",
                retryable=False,
                details={"failureClass": "permission_denied"},
            )
        principal = resolve_principal(auth_raw)
        if not principal.subject or principal.subject == "unknown":
            raise ToolFailure(
                error_code="PERMISSION_DENIED",
                message="Deployment overview requires an authenticated subject.",
                retryable=False,
                details={"failureClass": "permission_denied"},
            )
        raw_question: Any = None
        if isinstance(inputs, Mapping):
            raw_question = inputs.get("question")
        question = str(raw_question or "").strip()
        if not question:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message="Deployment overview input 'question' is required.",
                retryable=False,
                details={"failureClass": "invalid_input"},
            )
        if len(question) > MAX_QUESTION_CHARS:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message=(
                    "Deployment overview question must be at most "
                    f"{MAX_QUESTION_CHARS} characters."
                ),
                retryable=False,
                details={"failureClass": "invalid_input"},
            )
        sources = _overview_sources_from_context(ctx)
        now = ctx.get("overview_now")
        now_value = float(now) if isinstance(now, (int, float)) else None
        answer = answer_question(principal, question, sources, now=now_value)
        refused = bool(answer.get("refused", False))
        audit = {
            "readOnly": True,
            "mutations": [],
            "sideEffects": [],
            "toolsCalled": [],
            "principal": principal.subject,
            "operator": is_operator(principal),
            "refused": refused,
            "reasonCode": answer.get("reasonCode"),
        }
        return ToolResult(
            status="COMPLETED",
            outputs={
                "status": "REFUSED" if refused else "SUCCEEDED",
                "question": question[:MAX_QUESTION_CHARS],
                "answer": answer,
                "audit": audit,
            },
            progress={
                "percent": 100,
                "state": "REFUSED" if refused else "SUCCEEDED",
                "message": str(answer.get("observation") or "")[:500],
            },
        )

    return _handler


def register_deployment_overview_tool_handler(dispatcher: Any) -> None:
    """Register the overview handler on a ``ToolActivityDispatcher``."""
    dispatcher.register_skill(
        skill_name="moonmind.deployment_overview",
        handler=build_deployment_overview_handler(),
    )


__all__ = [
    "CACHE_FRESHNESS_SECONDS",
    "DEPLOYMENT_OVERVIEW_QUESTION_SET",
    "MAX_LOG_TAIL_LINES",
    "MAX_WORKFLOWS_PER_ANSWER",
    "MUTATION_VERBS",
    "OBSERVATION_OUTCOMES",
    "ONE_SHOT_SERVICES",
    "OPTIONAL_PROFILE_SERVICES",
    "OverviewPrincipal",
    "answer_deployment_observation",
    "answer_question",
    "answer_recent_failure",
    "answer_running",
    "answer_waiting",
    "build_deployment_overview_handler",
    "classify_collector_include",
    "classify_container_state",
    "classify_question",
    "detect_prompt_injection",
    "is_mutation_request",
    "is_operator",
    "register_deployment_overview_tool_handler",
    "requests_arbitrary_access",
    "resolve_principal",
    "sanitize_untrusted_text",
    "workflow_chat_url",
    "workflow_detail_url",
]
