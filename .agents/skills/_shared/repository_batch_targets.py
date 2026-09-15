#!/usr/bin/env python3
"""Explicit bounded multi-repository batch targets for isolated fan-out.

MoonLadderStudios/MoonMind#1657 selects isolated fan-out as the first
multi-repository increment: one user-visible operation applies a bounded task
to an explicit set of repositories, with a separately admitted child workflow
and workspace for each repository.

This module is the portable authoring boundary for that operation. It uses
only the Python standard library so resolved skill snapshots can execute it
outside MoonMind. It owns:

* explicit bounded target lists (no wildcard discovery of every repository a
  token can see);
* endpoint-aware normalization (identical names on different hosts stay
  distinct; exact duplicates collapse with evidence);
* an immutable target manifest whose digest is the operator-approval boundary
  (a target injected after approval changes the digest and cannot expand the
  approved batch);
* preflight classification with fail-before-dispatch versus explicitly
  requested partial-batch behavior;
* budget descriptors (target cap, concurrency gate, per-child spend intent)
  enforced without a new scheduler;
* stable per-target child identities bound to the manifest digest, so parent
  restarts and lost admission acknowledgments reconcile instead of
  duplicating work;
* truthful aggregate results with per-target status and publication outcomes,
  where selected retry never republishes completed repositories;
* explicit dependency edges that consume verified revision/artifact evidence
  (a bare PR reference never satisfies a merged-code dependency);
* owned-child cancellation through the normal execution API.

The parent never publishes source changes and never forwards a raw
multi-repository token bundle: every child carries only its own explicit
``connectionRef`` and is re-admitted through the existing repository and
execution contracts at child launch.

Negative scope (unchanged by this module): no new multi-repository credential
store, no combined writable checkout, no cross-repository atomicity, no
silent target expansion, no replacement batch execution engine.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

REPOSITORY_BATCH_MANIFEST_SCHEMA = "moonmind.repository-batch-manifest.v1"
REPOSITORY_BATCH_RESULT_SCHEMA = "moonmind.repository-batch-result.v1"

DEFAULT_MAX_REPOSITORY_TARGETS = 10
HARD_MAX_REPOSITORY_TARGETS = 25
DEFAULT_MAX_CONCURRENCY = 3

ALLOWED_OPERATIONS = frozenset({"read", "branch", "pr", "pr_with_merge_automation"})
ALLOWED_EVIDENCE_KINDS = frozenset({"revision", "artifact"})

_GITHUB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FORBIDDEN_TARGET_KEYS = frozenset(
    {"tokens", "token_bundle", "tokenbundle", "credentials", "secrets", "pat"}
)


class RepositoryBatchError(ValueError):
    """Fail-closed multi-repository batch authoring failure with a code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Endpoint and repository identity.
# ---------------------------------------------------------------------------


def normalize_batch_endpoint(value: Any) -> str:
    """Normalize an explicit repository endpoint for identity comparison."""

    raw = _text(value) or "https://github.com"
    lowered = raw.lower()
    if "@" in lowered.split("/")[0] and "://" not in lowered:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_ENDPOINT_DENIED",
            "batch endpoint must not embed credentials",
        )
    candidate = lowered if "://" in lowered else f"https://{lowered}"
    if not re.fullmatch(r"https?://[a-z0-9_.-]+(?::\d+)?(?:/.*)?", candidate):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_ENDPOINT_INVALID",
            f"unsupported batch endpoint {raw!r}",
        )
    return candidate.rstrip("/")


def normalize_batch_repository(value: Any) -> str:
    """Validate an explicit ``owner/repository`` target (no wildcards)."""

    raw = _text(value) or ""
    if "*" in raw:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_WILDCARD_REJECTED",
            "batch repository targets must be explicit; wildcard discovery "
            f"is not supported: {raw!r}",
        )
    candidate = raw.removesuffix(".git").rstrip("/")
    if not _GITHUB_NAME_PATTERN.fullmatch(candidate):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_REPOSITORY_INVALID",
            f"batch repository must use owner/repository form: {raw!r}",
        )
    return candidate


def default_branch_validator(branch: str) -> bool:
    """Validate a branch with Git's canonical ref-name implementation."""

    try:
        completed = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BRANCH_VALIDATION_UNAVAILABLE",
            "Git branch validation is unavailable",
        ) from exc
    return completed.returncode == 0


# ---------------------------------------------------------------------------
# Target parsing and normalization.
# ---------------------------------------------------------------------------


@dataclass
class ParsedRepositoryTarget:
    target_ref: str
    endpoint: str
    repository: str
    connection_ref: str
    branch: str
    operation: str
    revision: str | None = None
    depends_on: tuple[str, ...] = ()
    evidence_kind: str | None = None


def target_identity_key(target: ParsedRepositoryTarget) -> str:
    """Stable routing discriminator (never a display alias)."""

    return (
        f"{target.endpoint}#{target.repository.lower()}@"
        f"{target.branch}#{target.operation}"
    )


def parse_repository_target_entry(
    entry: Any,
    *,
    branch_validator: Callable[[str], bool] = default_branch_validator,
) -> ParsedRepositoryTarget:
    """Parse and validate one explicit repository-target entry."""

    if not isinstance(entry, dict):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_TARGET_INVALID",
            "repository batch entries must be objects",
        )
    for forbidden in _FORBIDDEN_TARGET_KEYS:
        if forbidden in {str(key).lower().replace("-", "_") for key in entry}:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_TOKEN_BUNDLE_REJECTED",
                "repository batch targets must name a connectionRef; raw "
                "credential material is never carried in a batch manifest",
            )
    endpoint = normalize_batch_endpoint(entry.get("endpoint"))
    repository = normalize_batch_repository(entry.get("repository"))
    connection_ref = _text(entry.get("connectionRef"))
    if connection_ref is None:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_CONNECTION_REQUIRED",
            f"target {repository!r} must name an explicit connectionRef; "
            "ambient connection fallback is not supported for multi-repo "
            "batches",
        )
    branch = _text(entry.get("branch"))
    if branch is None or not branch_validator(branch):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BRANCH_INVALID",
            f"target {repository!r} names an unavailable or unsafe branch",
        )
    operation = (_text(entry.get("operation")) or "pr").strip().lower()
    if operation not in ALLOWED_OPERATIONS:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_OPERATION_UNSUPPORTED",
            f"target {repository!r} requests unsupported operation {operation!r}",
        )
    revision = _text(entry.get("revision"))
    if revision is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", revision):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_REVISION_INVALID",
            f"target {repository!r} carries an invalid revision intent",
        )
    depends_on: tuple[str, ...] = ()
    raw_depends = entry.get("dependsOn")
    if raw_depends is not None:
        if not isinstance(raw_depends, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_depends
        ):
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_DEPENDENCY_INVALID",
                f"target {repository!r} dependsOn must be a list of target refs",
            )
        depends_on = tuple(dict.fromkeys(item.strip() for item in raw_depends))
    evidence_kind = _text(entry.get("evidenceKind"))
    if evidence_kind is not None:
        evidence_kind = evidence_kind.strip().lower()
        if evidence_kind not in ALLOWED_EVIDENCE_KINDS:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_EVIDENCE_KIND_UNSUPPORTED",
                f"target {repository!r} requests unknown evidence kind",
            )
    target_ref = _text(entry.get("targetRef")) or f"{endpoint}#{repository}"
    return ParsedRepositoryTarget(
        target_ref=target_ref,
        endpoint=endpoint,
        repository=repository,
        connection_ref=connection_ref,
        branch=branch,
        operation=operation,
        revision=revision,
        depends_on=depends_on,
        evidence_kind=evidence_kind,
    )


@dataclass
class NormalizedRepositoryBatch:
    targets: list[ParsedRepositoryTarget] = field(default_factory=list)
    duplicates: list[dict[str, str]] = field(default_factory=list)


def normalize_repository_batch(
    entries: Any,
    *,
    max_targets: int = DEFAULT_MAX_REPOSITORY_TARGETS,
    branch_validator: Callable[[str], bool] = default_branch_validator,
) -> NormalizedRepositoryBatch:
    """Normalize a bounded explicit target list.

    Exact duplicates (same endpoint, repository, branch, operation, and
    connection) collapse with evidence. Identical repository names on
    different endpoints stay distinct. The same repository with conflicting
    connection selections fails closed instead of silently picking one.
    """

    if not isinstance(entries, list) or not entries:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_EMPTY",
            "repository batch requires a non-empty explicit target list",
        )
    limit = max(0, int(max_targets))
    if limit < 1 or limit > HARD_MAX_REPOSITORY_TARGETS:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_LIMIT_INVALID",
            f"max_targets must be within 1..{HARD_MAX_REPOSITORY_TARGETS}",
        )
    if len(entries) > limit:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_LIMIT_EXCEEDED",
            f"repository batch lists {len(entries)} targets but admits "
            f"at most {limit}",
        )
    normalized = NormalizedRepositoryBatch()
    seen: dict[str, ParsedRepositoryTarget] = {}
    connection_by_repo: dict[str, str] = {}
    for entry in entries:
        target = parse_repository_target_entry(
            entry, branch_validator=branch_validator
        )
        repo_key = f"{target.endpoint}#{target.repository.lower()}"
        incumbent_connection = connection_by_repo.get(repo_key)
        if incumbent_connection is None:
            connection_by_repo[repo_key] = target.connection_ref
        elif incumbent_connection != target.connection_ref:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_CONNECTION_CONFLICT",
                f"repository {target.repository!r} selects conflicting "
                "connections within one batch; declare one explicit "
                "connection per repository",
            )
        key = f"{target_identity_key(target)}#{target.connection_ref}"
        if key in seen:
            normalized.duplicates.append(
                {
                    "targetRef": target.target_ref,
                    "duplicateOf": seen[key].target_ref,
                    "repository": target.repository,
                }
            )
            continue
        seen[key] = target
        normalized.targets.append(target)
    refs = [target.target_ref for target in normalized.targets]
    if len(set(refs)) != len(refs):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_TARGET_REF_CONFLICT",
            "batch target refs must be unique within one manifest",
        )
    known = set(refs)
    for target in normalized.targets:
        for dependency in target.depends_on:
            if dependency not in known:
                raise RepositoryBatchError(
                    "REPOSITORY_BATCH_DEPENDENCY_UNKNOWN",
                    f"target {target.target_ref!r} depends on unknown target "
                    f"{dependency!r}",
                )
            if dependency == target.target_ref:
                raise RepositoryBatchError(
                    "REPOSITORY_BATCH_DEPENDENCY_CYCLE",
                    f"target {target.target_ref!r} cannot depend on itself",
                )
    _reject_dependency_cycles(normalized.targets)
    return normalized


def _reject_dependency_cycles(targets: list[ParsedRepositoryTarget]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    edges = {target.target_ref: set(target.depends_on) for target in targets}

    def _visit(ref: str, trail: list[str]) -> None:
        if ref in visited:
            return
        if ref in visiting:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_DEPENDENCY_CYCLE",
                "batch dependency cycle: " + " -> ".join([*trail, ref]),
            )
        visiting.add(ref)
        for dependency in sorted(edges.get(ref, ())):
            _visit(dependency, [*trail, ref])
        visiting.discard(ref)
        visited.add(ref)

    for target in targets:
        _visit(target.target_ref, [])


# ---------------------------------------------------------------------------
# Immutable manifest (operator-approval boundary).
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RepositoryBatchBudget:
    max_targets: int = DEFAULT_MAX_REPOSITORY_TARGETS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    max_child_spend_usd: float | None = None
    max_attempts_per_child: int = 3


def parse_batch_budget(value: Any) -> RepositoryBatchBudget:
    """Parse budget descriptors (recorded intent, enforced without a scheduler)."""

    raw = value if isinstance(value, dict) else {}
    max_targets = raw.get("maxTargets", DEFAULT_MAX_REPOSITORY_TARGETS)
    max_concurrency = raw.get("maxConcurrency", DEFAULT_MAX_CONCURRENCY)
    max_attempts = raw.get("maxAttemptsPerChild", 3)
    try:
        max_targets = int(max_targets)
        max_concurrency = int(max_concurrency)
        max_attempts = int(max_attempts)
    except (TypeError, ValueError) as exc:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BUDGET_INVALID", "batch budget values must be numeric"
        ) from exc
    if not 1 <= max_targets <= HARD_MAX_REPOSITORY_TARGETS:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BUDGET_INVALID",
            f"maxTargets must be within 1..{HARD_MAX_REPOSITORY_TARGETS}",
        )
    if max_concurrency < 1 or max_concurrency > HARD_MAX_REPOSITORY_TARGETS:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BUDGET_INVALID", "maxConcurrency is out of range"
        )
    if max_attempts < 1 or max_attempts > 10:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_BUDGET_INVALID", "maxAttemptsPerChild is out of range"
        )
    spend = raw.get("maxChildSpendUsd")
    if spend is not None:
        try:
            spend = float(spend)
        except (TypeError, ValueError) as exc:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_BUDGET_INVALID",
                "maxChildSpendUsd must be numeric",
            ) from exc
        if spend <= 0:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_BUDGET_INVALID",
                "maxChildSpendUsd must be positive when declared",
            )
    return RepositoryBatchBudget(
        max_targets=max_targets,
        max_concurrency=max_concurrency,
        max_child_spend_usd=spend,
        max_attempts_per_child=max_attempts,
    )


def freeze_repository_batch_manifest(
    normalized: NormalizedRepositoryBatch,
    *,
    task: dict[str, Any],
    budget: RepositoryBatchBudget,
) -> dict[str, Any]:
    """Freeze the exact approved set as an immutable manifest artifact."""

    if not isinstance(task, dict):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_TASK_INVALID", "batch task snapshot must be an object"
        )
    run_ref = _text(task.get("runRef"))
    if run_ref is None:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_TASK_INVALID",
            "batch task snapshot must name an explicit runRef",
        )
    body = {
        "schemaVersion": REPOSITORY_BATCH_MANIFEST_SCHEMA,
        "runRef": run_ref,
        "constraints": _text(task.get("constraints")) or "",
        "publishMode": (_text(task.get("publishMode")) or "pr").strip().lower(),
        "targets": [
            {
                "targetRef": target.target_ref,
                "endpoint": target.endpoint,
                "repository": target.repository,
                "connectionRef": target.connection_ref,
                "branch": target.branch,
                "operation": target.operation,
                "revision": target.revision,
                "dependsOn": list(target.depends_on),
                "evidenceKind": target.evidence_kind,
            }
            for target in normalized.targets
        ],
        "budget": {
            "maxTargets": budget.max_targets,
            "maxConcurrency": budget.max_concurrency,
            "maxChildSpendUsd": budget.max_child_spend_usd,
            "maxAttemptsPerChild": budget.max_attempts_per_child,
        },
        "duplicatesNormalized": list(normalized.duplicates),
    }
    # frozenAt records when this copy was materialized; it is metadata, not
    # approval scope, so the digest stays stable when the same logical batch
    # is re-frozen for dispatch, resume, or selected retry.
    digest = "sha256:" + hashlib.sha256(_canonical_json(body).encode()).hexdigest()
    return {**body, "frozenAt": _utc_now(), "digest": digest}


def verify_manifest_unchanged(manifest: dict[str, Any], expected_digest: str) -> None:
    """Reject a target set injected after operator approval.

    The digest is recomputed from the manifest body rather than trusting the
    embedded digest field, so appended, removed, or edited targets always
    fail the comparison against the approved digest.
    """

    approved = (_text(expected_digest) or "").strip()
    if not approved:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_APPROVAL_REQUIRED",
            "dispatch requires the approved manifest digest; preflight only "
            "writes the manifest for operator review",
        )
    if not isinstance(manifest, dict):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_MANIFEST_MISMATCH",
            "approved manifest digest does not match the frozen target set; "
            "a target injected after approval cannot expand the batch",
        )
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"digest", "frozenAt"}
    }
    recomputed = "sha256:" + hashlib.sha256(_canonical_json(body).encode()).hexdigest()
    if recomputed != approved:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_MANIFEST_MISMATCH",
            "approved manifest digest does not match the frozen target set; "
            "a target injected after approval cannot expand the batch",
        )


# ---------------------------------------------------------------------------
# Preflight.
# ---------------------------------------------------------------------------


@dataclass
class PreflightTarget:
    target_ref: str
    accessible: bool
    reason: str


@dataclass
class PreflightReport:
    targets: list[PreflightTarget] = field(default_factory=list)
    blocked: bool = False
    failure: str | None = None


def default_accessibility_probe(target: ParsedRepositoryTarget) -> tuple[bool, str]:
    """Structural admissibility probe (server admission rechecks authority).

    Real repository permission and revocation are rechecked at child admission
    through the existing repository contract; this probe fails closed on
    anything the batch authoring boundary can already prove inadmissible.
    """

    if not target.connection_ref:
        return False, "missing_connection"
    if target.operation not in ALLOWED_OPERATIONS:
        return False, "unsupported_operation"
    return True, "structurally_admissible"


def preflight_repository_batch(
    normalized: NormalizedRepositoryBatch,
    *,
    probe: Callable[[ParsedRepositoryTarget], tuple[bool, str]] = (
        default_accessibility_probe
    ),
    allow_partial: bool = False,
    publish_mode: str = "pr",
) -> PreflightReport:
    """Classify every target before launch.

    The default is fail-before-dispatch: any inaccessible or unsupported
    target blocks the whole batch. ``allow_partial`` must be explicitly
    requested by the operator and records excluded targets as skipped with
    their preflight reason instead of dispatching them.
    """

    normalized_publish = (publish_mode or "pr").strip().lower()
    report = PreflightReport()
    for target in normalized.targets:
        try:
            accessible, reason = probe(target)
        except Exception as exc:  # noqa: BLE001 - probe failures block dispatch
            report.targets.append(
                PreflightTarget(
                    target_ref=target.target_ref,
                    accessible=False,
                    reason=f"probe_error:{exc}",
                )
            )
            continue
        if (
            accessible
            and target.operation == "read"
            and normalized_publish != "none"
        ):
            accessible, reason = False, "read_only_publish_mismatch"
        report.targets.append(
            PreflightTarget(
                target_ref=target.target_ref,
                accessible=bool(accessible),
                reason=str(reason or ("accessible" if accessible else "unsupported")),
            )
        )
    inadmissible = [item for item in report.targets if not item.accessible]
    if inadmissible and not allow_partial:
        report.blocked = True
        report.failure = (
            "preflight found "
            f"{len(inadmissible)} inaccessible/unsupported target(s); "
            "re-run with explicit allow_partial to proceed with the "
            "accessible subset"
        )
    return report


# ---------------------------------------------------------------------------
# Dependency phases and verified-evidence gating (R6).
# ---------------------------------------------------------------------------


def resolve_target_phases(
    targets: list[ParsedRepositoryTarget],
) -> list[list[ParsedRepositoryTarget]]:
    """Order targets into dependency phases (independent targets first)."""

    remaining = {target.target_ref: target for target in targets}
    phased: list[list[ParsedRepositoryTarget]] = []
    satisfied: set[str] = set()
    while remaining:
        ready = sorted(
            (
                target
                for target in remaining.values()
                if set(target.depends_on) <= satisfied
            ),
            key=lambda item: item.target_ref,
        )
        if not ready:
            raise RepositoryBatchError(
                "REPOSITORY_BATCH_DEPENDENCY_CYCLE",
                "batch dependencies cannot be ordered into phases",
            )
        phased.append(ready)
        for target in ready:
            satisfied.add(target.target_ref)
            del remaining[target.target_ref]
    return phased


def gate_dependent_targets(
    phase_targets: list[ParsedRepositoryTarget],
    *,
    verify_upstream_evidence: Callable[[str], dict[str, Any] | None] | None = None,
) -> tuple[list[ParsedRepositoryTarget], list[dict[str, str]]]:
    """Release dependents only on verified upstream revision/artifact evidence.

    ``verify_upstream_evidence`` must return a mapping with ``verified: True``
    plus a ``revision`` or ``artifactRef``. A bare PR number or URL, an
    unverified claim, or a missing verifier keeps the dependent blocked:
    PR creation alone never satisfies a merged-code dependency.
    """

    releasable: list[ParsedRepositoryTarget] = []
    blocked: list[dict[str, str]] = []
    for target in phase_targets:
        if not target.depends_on:
            releasable.append(target)
            continue
        unsatisfied: list[str] = []
        for dependency in target.depends_on:
            evidence = (
                verify_upstream_evidence(dependency)
                if verify_upstream_evidence is not None
                else None
            )
            if not _evidence_is_verified_merge(evidence):
                unsatisfied.append(dependency)
        if unsatisfied:
            blocked.append(
                {
                    "targetRef": target.target_ref,
                    "reason": "upstream_evidence_unverified",
                    "unsatisfied": ",".join(sorted(unsatisfied)),
                }
            )
        else:
            releasable.append(target)
    return releasable, blocked


def _evidence_is_verified_merge(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    if evidence.get("verified") is not True:
        return False
    kind = str(evidence.get("kind") or "").strip().lower()
    if kind not in ALLOWED_EVIDENCE_KINDS:
        return False
    if kind == "revision":
        return bool(_text(evidence.get("revision")))
    return bool(_text(evidence.get("artifactRef")))


# ---------------------------------------------------------------------------
# Stable per-target child identity bound to the approved manifest.
# ---------------------------------------------------------------------------


def multi_repo_child_idempotency_key(
    *,
    manifest_digest: str,
    target: ParsedRepositoryTarget,
    run_ref: str,
    attempt: int = 0,
) -> str:
    """Bind a child request to the approved manifest and target authority.

    The first admission uses ``attempt=0``. Selected retry of a failed,
    canceled, or unknown target advances the attempt so the retry admits a
    fresh child instead of resolving to the terminal prior admission; the
    execution API still dedupes lost acknowledgments within one attempt.
    Completed repositories are never assigned a new attempt.
    """

    canonical = _canonical_json(
        {
            "attempt": int(attempt),
            "batchDigest": manifest_digest,
            "connectionRef": target.connection_ref,
            "endpoint": target.endpoint,
            "operation": target.operation,
            "repository": target.repository.lower(),
            "runRef": run_ref,
            "targetRef": target.target_ref,
        }
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "_", target.repository)[:32]
    return f"repository-batch:{safe_repo}:sha256:{digest}"


def slug_for_target(target: ParsedRepositoryTarget) -> str:
    """Filesystem-safe per-target namespace slug."""

    host = re.sub(r"[^A-Za-z0-9]+", "_", target.endpoint.split("://", 1)[-1])[:32]
    repo = re.sub(r"[^A-Za-z0-9]+", "_", target.repository)[:48]
    return f"{host}_{repo}".strip("_").lower() or "repository_target"


def build_multi_repo_child_request(
    target: ParsedRepositoryTarget,
    *,
    manifest_digest: str,
    run_ref: str,
    goal: str,
    constraints: str,
    publish_mode: str,
    runtime_mode: str | None,
    runtime_model: str | None,
    runtime_effort: str | None,
    runtime_provider_profile: str | None,
    max_attempts: int,
    attempt: int = 0,
) -> dict[str, Any]:
    """Build one isolated child request for a single repository target."""

    normalized_goal = _text(goal) or f"Apply the batch task to {target.repository}."
    scoped_goal = (
        f"{normalized_goal} [repository-batch target {target.target_ref} "
        f"in {target.repository} via {target.connection_ref}]"
    )
    normalized_publish = (publish_mode or "pr").strip().lower()
    if target.operation == "read" and normalized_publish != "none":
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_READ_ONLY_PUBLISH_MISMATCH",
            f"read-only target {target.target_ref!r} must use publishMode none",
        )
    task_payload: dict[str, Any] = {
        "goal": scoped_goal,
        "instructions": scoped_goal,
        "inputs": {
            "repository_batch_target": target.target_ref,
            "repository": target.repository,
            "repository_endpoint": target.endpoint,
            "repository_connection_ref": target.connection_ref,
            "repository_branch": target.branch,
            "target_revision": target.revision,
            "constraints": constraints or "",
            "artifact_namespace": slug_for_target(target),
        },
        "publish": {"mode": normalized_publish},
    }
    kind, _, slug = run_ref.partition(":")
    if kind.strip().lower() == "skill" and slug.strip():
        task_payload["tool"] = {"type": "skill", "name": slug.strip()}
    elif kind.strip().lower() == "preset" and slug.strip():
        task_payload["taskTemplate"] = {"slug": slug.strip(), "scope": "global"}
    else:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_RUN_REF_INVALID",
            "batch runRef must use skill:<name> or preset:<slug>",
        )
    payload: dict[str, Any] = {
        "requiredCapabilities": ["git", "gh"],
        "repository": {
            "provider": "git",
            "connectionRef": target.connection_ref,
            "repository": {"name": target.repository},
            "branch": {"name": target.branch},
        },
        "runtimeInheritance": "caller",
        "task": task_payload,
        "batchDigest": manifest_digest,
        "batchTarget": {
            "targetRef": target.target_ref,
            "endpoint": target.endpoint,
            "repository": target.repository,
            "connectionRef": target.connection_ref,
            "branch": target.branch,
            "operation": target.operation,
        },
        "idempotencyKey": multi_repo_child_idempotency_key(
            manifest_digest=manifest_digest,
            target=target,
            run_ref=run_ref,
            attempt=attempt,
        ),
    }
    runtime_payload: dict[str, Any] = {}
    if runtime_mode:
        runtime_payload["mode"] = runtime_mode
        payload["targetRuntime"] = runtime_mode
    if runtime_model:
        runtime_payload["model"] = runtime_model
    if runtime_effort:
        runtime_payload["effort"] = runtime_effort
    if runtime_provider_profile:
        runtime_payload["executionProfileRef"] = runtime_provider_profile
    if runtime_payload:
        task_payload["runtime"] = runtime_payload
    envelope = {
        "type": "task",
        "priority": 0,
        "maxAttempts": int(max_attempts),
        "payload": payload,
    }
    if not isinstance(envelope["payload"].get("task"), dict):
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_CHILD_INVALID",
            "batch child envelope requires payload.task",
        )
    return envelope


# ---------------------------------------------------------------------------
# Restart reconciliation, capacity gating, cancellation, aggregate results.
# ---------------------------------------------------------------------------

TERMINAL_PER_TARGET = frozenset({"succeeded", "failed", "blocked", "canceled"})
RETRYABLE_PER_TARGET = frozenset({"failed", "canceled", "unknown"})


def reconcile_with_prior_result(
    *,
    manifest_digest: str,
    targets: list[ParsedRepositoryTarget],
    prior_result: dict[str, Any] | None,
    retry_failed_only: bool = False,
) -> tuple[list[tuple[ParsedRepositoryTarget, int]], list[dict[str, str]]]:
    """Reconcile dispatch against a prior aggregate result.

    A parent restart discovers already-accepted children (same manifest
    digest plus a recorded workflowId) instead of submitting the same work
    again. Ambiguous prior attempts (errors without a workflowId) resubmit
    under the same idempotency key, which the execution API dedupes.
    Completed repositories are never resubmitted: selected retry covers only
    failed, canceled, or unknown targets, and each resubmission advances the
    attempt so it admits a fresh child instead of resolving to the terminal
    prior admission.
    """

    if not prior_result or not isinstance(prior_result, dict):
        return [(target, 0) for target in targets], []
    if _text(prior_result.get("manifestDigest")) != manifest_digest:
        return [(target, 0) for target in targets], []
    prior_by_ref = {
        str(item.get("targetRef")): item
        for item in (prior_result.get("targets") or [])
        if isinstance(item, dict) and _text(item.get("targetRef"))
    }
    to_submit: list[tuple[ParsedRepositoryTarget, int]] = []
    reused: list[dict[str, str]] = []
    for target in targets:
        prior = prior_by_ref.get(target.target_ref)
        if prior is None:
            to_submit.append((target, 0))
            continue
        status = str(prior.get("status") or "").strip().lower()
        workflow_id = _text(prior.get("workflowId"))
        try:
            prior_attempt = int(prior.get("attempt") or 0)
        except (TypeError, ValueError):
            prior_attempt = 0
        if status == "succeeded":
            reused.append(
                {
                    "targetRef": target.target_ref,
                    "reason": "retry_skipped_completed",
                    "attempt": prior_attempt,
                }
            )
            continue
        if workflow_id and status in {"queued", "running", "unknown", "succeeded"}:
            reused.append(
                {
                    "targetRef": target.target_ref,
                    "reason": "already_queued",
                    "workflowId": workflow_id,
                    "attempt": prior_attempt,
                }
            )
            continue
        if status in TERMINAL_PER_TARGET and not retry_failed_only and status in {
            "failed",
            "blocked",
        }:
            # Without an explicit selected retry, terminal failures stay put
            # so a blind rerun cannot republish or duplicate work.
            reused.append(
                {
                    "targetRef": target.target_ref,
                    "reason": "terminal_preserved",
                    "attempt": prior_attempt,
                }
            )
            continue
        # A prior admission (workflowId present) advances the attempt so a
        # selected retry admits a fresh child; an unconfirmed submission
        # without an admission keeps its key so the execution API dedupes a
        # lost acknowledgment instead of duplicating work.
        to_submit.append((target, prior_attempt + 1 if workflow_id else prior_attempt))
    return to_submit, reused


def gate_on_capacity(
    *,
    running_owned: int,
    max_concurrency: int,
    target_ref: str,
) -> dict[str, str] | None:
    """Return a waiting verdict instead of exceeding admitted concurrency."""

    if running_owned < max_concurrency:
        return None
    return {
        "targetRef": target_ref,
        "reason": "capacity_waiting",
        "detail": (
            f"{running_owned} owned children running at cap {max_concurrency}; "
            "target waits without duplicating work"
        ),
    }


def build_batch_aggregate_result(
    *,
    manifest: dict[str, Any],
    per_target: list[dict[str, Any]],
    batch_status: str,
    failure: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the inspectable aggregate result for one approved manifest."""

    normalized_status = (batch_status or "").strip().lower()
    if normalized_status not in {
        "queued",
        "partial",
        "failed",
        "blocked",
        "canceled",
        "no_op",
    }:
        raise RepositoryBatchError(
            "REPOSITORY_BATCH_STATUS_INVALID",
            f"unknown batch aggregate status {batch_status!r}",
        )
    counts: dict[str, int] = {}
    for item in per_target:
        status = str(item.get("status") or "unknown").strip().lower()
        counts[status] = counts.get(status, 0) + 1
    return {
        "schemaVersion": REPOSITORY_BATCH_RESULT_SCHEMA,
        "contractId": "repository_batch_fanout.v1",
        "timestamp": _utc_now(),
        "manifestDigest": _text(manifest.get("digest")),
        "runRef": _text(manifest.get("runRef")),
        "status": normalized_status,
        "requested": len(per_target),
        "counts": counts,
        "targets": per_target,
        "failure": failure,
    }


def per_target_entry(
    target: ParsedRepositoryTarget,
    *,
    status: str,
    workflow_id: str | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
    publication: dict[str, Any] | None = None,
    error: str | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    """One truthful per-target row of the aggregate result."""

    entry: dict[str, Any] = {
        "targetRef": target.target_ref,
        "repository": target.repository,
        "endpoint": target.endpoint,
        "connectionRef": target.connection_ref,
        "branch": target.branch,
        "operation": target.operation,
        "status": status,
        "attempt": int(attempt),
    }
    if workflow_id:
        entry["workflowId"] = workflow_id
    if idempotency_key:
        entry["idempotencyKey"] = idempotency_key
    if reason:
        entry["reason"] = reason
    if evidence is not None:
        entry["evidence"] = evidence
    if publication is not None:
        entry["publication"] = publication
    if error is not None:
        entry["error"] = error[:2048]
    return entry


def selected_retry_targets(
    prior_result: dict[str, Any],
    *,
    selected_refs: list[str] | None = None,
) -> list[str]:
    """Resolve an explicit selected retry to retryable targets only."""

    wanted = (
        {ref.strip() for ref in (selected_refs or []) if str(ref).strip()}
        or None
    )
    retryable: list[str] = []
    for item in prior_result.get("targets") or []:
        if not isinstance(item, dict):
            continue
        ref = _text(item.get("targetRef"))
        status = str(item.get("status") or "").strip().lower()
        if not ref or status not in RETRYABLE_PER_TARGET:
            continue
        if wanted is not None and ref not in wanted:
            continue
        retryable.append(ref)
    return sorted(retryable)


__all__ = [
    "ALLOWED_EVIDENCE_KINDS",
    "ALLOWED_OPERATIONS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_MAX_REPOSITORY_TARGETS",
    "HARD_MAX_REPOSITORY_TARGETS",
    "REPOSITORY_BATCH_MANIFEST_SCHEMA",
    "REPOSITORY_BATCH_RESULT_SCHEMA",
    "RETRYABLE_PER_TARGET",
    "TERMINAL_PER_TARGET",
    "NormalizedRepositoryBatch",
    "ParsedRepositoryTarget",
    "PreflightReport",
    "PreflightTarget",
    "RepositoryBatchBudget",
    "RepositoryBatchError",
    "build_batch_aggregate_result",
    "build_multi_repo_child_request",
    "default_accessibility_probe",
    "default_branch_validator",
    "freeze_repository_batch_manifest",
    "gate_dependent_targets",
    "gate_on_capacity",
    "multi_repo_child_idempotency_key",
    "normalize_batch_endpoint",
    "normalize_batch_repository",
    "normalize_repository_batch",
    "parse_batch_budget",
    "parse_repository_target_entry",
    "per_target_entry",
    "preflight_repository_batch",
    "reconcile_with_prior_result",
    "resolve_target_phases",
    "selected_retry_targets",
    "slug_for_target",
    "target_identity_key",
    "verify_manifest_unchanged",
]
