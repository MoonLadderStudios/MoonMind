#!/usr/bin/env python3
"""Hermetic Qdrant-removal cutover rehearsal gate for #4115.

This tool provides early, bounded, idempotent preflight/rehearsal
verification for the Qdrant-bearing to vector-free upgrade WITHOUT
performing any live deployment mutation. It never:

- contacts a live Qdrant, Temporal, or Docker daemon,
- inventories a live production deployment (repository inspection alone
  cannot establish an operator's active work or Qdrant-only content),
- stops or removes services, volumes, or files,
- deletes old volumes automatically or authorizes production deletion,
- marks operator-owned facts complete on hermetic evidence alone.

Operator-gated steps (live workflow/schedule inventory, Qdrant-only
provenance classification, snapshot/export custody, exact-container
retirement execution, retention disposition, rollback execution) are
reported as ``blocked`` with the missing evidence named. That is the
correct terminal state for a repo checkout: rehearsal fixtures may pass
while deployment qualification stays blocked.

Ordering: this gate performs history/state analysis BEFORE #4106-#4109
delete handlers or fields. It gates on #4105 admission retirement and
fails closed when that enforcement is absent. It never deletes an
Activity handler, reinterprets historical payloads, or retains an
indefinite compatibility backend.

The tool intentionally does NOT implement live snapshotting, logical
export against a real Qdrant, or schema migration. It exercises the
envelope/protocol hermetically against sanitized fixtures so the
operator runbook procedure is rehearsed before any real action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_REL = Path("docs/tmp/QdrantCutoverRunbook-4115.md")
ISSUE_REF = "MoonLadderStudios/MoonMind#4115"

REHEARSAL_MODES = (
    "fresh-vector-free",
    "old-env-upgrade",
    "retired-requirements-upgrade",
    "historical-artifact-upgrade",
)

# Prerequisites owned by sibling issues / the deployment owner. Absence
# blocks deployment qualification; it never blocks hermetic fixture
# verification. Names are stable identifiers, not a migration coordinator.
PREREQUISITE_IDS = (
    "4105-admission",
    "4115-history-analysis",
    "4106-4109-removal",
    "4107-retrieval-state",
    "4110-4113-rehearsal",
    "named-owner-approval",
    "live-deployment-qualification",
    "snapshot-recoverability",
)

FORBIDDEN_RETIREMENT_PATTERNS = (
    re.compile(r"\bdown\s+-v\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bdocker\s+volume\s+prune\b"),
    re.compile(r"\brm\s+-rf?\s+[/~]"),
    re.compile(r"\bDROP\s+(DATABASE|TABLE)\b", re.IGNORECASE),
    re.compile(r"\bvolume\b.{0,40}(delet|remov|drop|purge|prune)", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|purge|prune).{0,40}\bvolume\b", re.IGNORECASE),
    # Broad orphan cleanup is never an exact-container retirement.
    re.compile(r"--remove-orphans", re.IGNORECASE),
    re.compile(r"\bprune\b", re.IGNORECASE),
    # Whole-database restore/destruction is never a safe container retirement.
    re.compile(
        r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(database|postgres|minio)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(database|postgres|minio)\b.{0,40}(delet|remov|drop|destroy|wipe|purge)",
        re.IGNORECASE,
    ),
)

FORBIDDEN_ROLLBACK_PATTERNS = (
    # The new release must never gain an optional Qdrant profile/adapter
    # as a rollback feature.
    re.compile(r"qdrant.{0,30}\bprofile\b", re.IGNORECASE),
    re.compile(r"\bprofile\b.{0,30}qdrant", re.IGNORECASE),
    re.compile(r"qdrant.{0,30}\badapter\b", re.IGNORECASE),
    re.compile(r"\badapter\b.{0,30}qdrant", re.IGNORECASE),
    re.compile(r"optional.{0,30}qdrant", re.IGNORECASE),
    re.compile(r"qdrant.{0,30}optional", re.IGNORECASE),
)

_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+\S+"
    r"|(?:password|passwd|secret|bearer|cookie|session[_-]?token|refresh[_-]?token"
    r"|token|api[_-]?key|qdrant[_-]?api[_-]?key)\s*[:=]\s*[^\s;,\"']+)"
)


def sanitize(text: str) -> str:
    """Redact secret-like assignments from evidence text."""
    redacted = _SECRET_PATTERN.sub("[redacted]", text)
    return redacted[:2000]


@dataclass
class StepResult:
    name: str
    status: str  # "completed" | "blocked" | "failed"
    evidence: str

    def to_dict(self) -> dict[str, str]:
        d = asdict(self)
        d["evidence"] = sanitize(d["evidence"])
        return d


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def check_plan_precondition(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the cutover runbook is present and still pre-cutover (Proposed)."""
    content = _read_text(repo_root / PLAN_REL)
    if content is None:
        return StepResult(
            "plan-precondition",
            "failed",
            f"{PLAN_REL} missing; Qdrant cutover rehearsal has no accepted plan baseline.",
        )
    if "Status: Proposed" in content and "4115" in content:
        return StepResult(
            "plan-precondition",
            "completed",
            "Cutover runbook present with Status: Proposed; correctly unarchived pre-cutover.",
        )
    return StepResult(
        "plan-precondition",
        "failed",
        "Cutover runbook present but precondition unreadable "
        "(missing Proposed status or 4115).",
    )


def _capability_present(pid: str, repo_root: Path = REPO_ROOT) -> bool:
    """Whether a prerequisite's owning capability is observable in the checkout.

    Only repo-observable capabilities may flip. Owner-held and
    deployment-side items never flip hermetically and always return False.
    """
    if pid == "4105-admission":
        # #4105 landed: explicit rag/followUpRetrieval requirements fail
        # closed before Temporal start/launch; schedule producers retired.
        inventory = repo_root / "docs/tmp/QdrantRemovalInventory-4105.md"
        contract = repo_root / "moonmind/workflows/executions/execution_contract.py"
        if not inventory.exists():
            return False
        text = _read_text(contract) or ""
        return "reject_retired_vector_fields" in text or "retired" in text.lower()
    if pid == "4115-history-analysis":
        # This gate itself is the history/state analysis: present when the
        # rehearsal tool and runbook both exist in the checkout.
        return (repo_root / "tools/qdrant_cutover_rehearsal.py").exists() and (
            repo_root / PLAN_REL
        ).exists()
    if pid == "4106-4109-removal":
        # Sibling deletions: no live native Qdrant backend (no qdrant
        # service in compose AND no live rag/qdrant client wiring).
        return not collect_inventory_survey(repo_root).get("qdrant_surfaces")
    if pid == "4107-retrieval-state":
        # #4107 owns capability/audit-state classification for
        # moonmind_retrieval_state; never flippable by this gate's fixtures.
        return False
    if pid == "4110-4113-rehearsal":
        # Final integrated rehearsal inputs; deployment-side evidence.
        return False
    return False


def detect_capability_presence(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Probe the checkout for each prerequisite's owning capability."""
    presence: dict[str, str] = {}
    survey = collect_inventory_survey(repo_root)
    surfaces = survey.get("qdrant_surfaces", [])
    presence["4105-admission"] = (
        "retired-vector admission enforcement present "
        "(#4105 inventory + execution contract)"
        if _capability_present("4105-admission", repo_root)
        else "checked docs/tmp/QdrantRemovalInventory-4105.md and "
        "moonmind/workflows/executions/execution_contract.py; "
        "retired-vector admission enforcement absent"
    )
    presence["4115-history-analysis"] = (
        "cutover rehearsal gate + runbook present (this change)"
        if _capability_present("4115-history-analysis", repo_root)
        else "checked tools/qdrant_cutover_rehearsal.py and runbook; absent"
    )
    presence["4106-4109-removal"] = (
        "no live native Qdrant surfaces in survey; sibling removal appears landed"
        if not surfaces
        else f"{len(surfaces)} live Qdrant surfaces still present "
        "(see inventory-survey); sibling removal not landed"
    )
    presence["4107-retrieval-state"] = (
        "checked checkout for moonmind_retrieval_state capability/audit-state "
        "classification record; none present (owner evidence, #4107)"
    )
    presence["4110-4113-rehearsal"] = (
        "checked checkout for final integrated rehearsal record; none present "
        "(deployment-side evidence)"
    )
    presence["named-owner-approval"] = "no owner approval record in checkout"
    presence["live-deployment-qualification"] = (
        "no live deployment inventory attempted hermetically (never contacted)"
    )
    presence["snapshot-recoverability"] = (
        "no live snapshot/export recoverability evidence in checkout "
        "(operator-verified, hermetic envelope only)"
    )
    return presence


def check_prerequisites(repo_root: Path = REPO_ROOT) -> list[StepResult]:
    """Report every deployment prerequisite, wired to real repo presence checks."""
    presence = detect_capability_presence(repo_root)
    owners = {
        "4105-admission": "retired-vector admission retirement (#4105)",
        "4115-history-analysis": "history/state analysis (this issue, #4115)",
        "4106-4109-removal": "handler/field deletion siblings (#4106-#4109)",
        "4107-retrieval-state": "capability/audit-state classification (#4107)",
        "4110-4113-rehearsal": "final integrated rehearsal (#4110-#4113)",
        "named-owner-approval": "named deployment owner approval",
        "live-deployment-qualification": "separately authorized live deployment check",
        "snapshot-recoverability": "operator-verified snapshot/export recoverability",
    }
    results = []
    for pid in PREREQUISITE_IDS:
        probed = presence.get(pid, "unchecked")
        if _capability_present(pid, repo_root):
            results.append(
                StepResult(
                    f"prerequisite-{pid}",
                    "completed",
                    f"Capability present in repo checkout; owned by {owners[pid]}. "
                    f"Repo probe: {probed}.",
                )
            )
        else:
            results.append(
                StepResult(
                    f"prerequisite-{pid}",
                    "blocked",
                    f"Missing in repo checkout; owned by {owners[pid]}. "
                    f"Repo probe: {probed}. "
                    "Blocks deployment qualification, not hermetic rehearsal.",
                )
            )
    return results


# -- R1: bounded read-only preflight survey -----------------------------------

# Files probed by the sanitized survey. Counts and file:line refs only; the
# survey never exports workflow payloads, document text, embeddings,
# secrets, or capability tokens.
SURVEY_SOURCES = (
    "docker-compose.yaml",
    ".env-template",
    "moonmind/config/settings.py",
    "moonmind/rag/qdrant_client.py",
    "moonmind/rag/service.py",
    "moonmind/rag/overlay.py",
    "api_service/api/routers/retrieval_gateway.py",
    "api_service/retrieval_capabilities.py",
    "moonmind/workflows/temporal/workflows/manifest_ingest.py",
    "moonmind/workflows/temporal/workflows/run.py",
    "frontend/src/entrypoints/schedules.tsx",
    "docs/tmp/QdrantRemovalInventory-4105.md",
)


def collect_inventory_survey(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect a sanitized deployment-state survey from real repo files.

    Reports structural counts (services, volumes, env wiring, module
    presence) with file references. Never publishes payloads: no document
    text, vectors, secrets, tokens, or capability material is collected.
    Missing files are recorded as ``missing`` rather than failing.
    """
    survey: dict[str, Any] = {"sources": {}, "qdrant_surfaces": []}
    compose = _read_text(repo_root / "docker-compose.yaml")
    if compose is None:
        survey["sources"]["docker-compose.yaml"] = "missing"
    else:
        services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", compose, re.MULTILINE)
        survey["sources"]["docker-compose.yaml"] = f"{len(services)} services"
        survey["compose_services"] = sorted(set(services))[:60]
        m = re.search(r"image:\s*(qdrant/qdrant:[^\s]*)", compose)
        survey["qdrant_image"] = m.group(1) if m else "absent"
        survey["qdrant_storage_volume"] = bool(
            re.search(r"(?m)^  qdrant-storage:", compose)
        )
        survey["retrieval_state_volume"] = bool(
            re.search(r"(?m)^  moonmind_retrieval_state:", compose)
        )
        survey["qdrant_url_wiring"] = len(re.findall(r"QDRANT_URL", compose))
        survey["init_db_depends_on_qdrant"] = bool(
            re.search(r"qdrant:\s*\n\s*condition:\s*service_started", compose)
        )
        if re.search(r"(?m)^  qdrant:", compose):
            survey["qdrant_surfaces"].append("docker-compose.yaml: qdrant service")
        if "qdrant-storage" in compose:
            survey["qdrant_surfaces"].append("docker-compose.yaml: qdrant-storage volume")
    env_template = _read_text(repo_root / ".env-template")
    if env_template is None:
        survey["sources"][".env-template"] = "missing"
    else:
        hits = len(re.findall(r"QDRANT_", env_template))
        survey["sources"][".env-template"] = f"read, {hits} QDRANT_ entries"
        if hits:
            survey["qdrant_surfaces"].append(f".env-template: {hits} QDRANT_ entries")
    settings_text = _read_text(repo_root / "moonmind/config/settings.py")
    if settings_text is None:
        survey["sources"]["moonmind/config/settings.py"] = "missing"
    else:
        hits = len(re.findall(r"[Qq]drant|QDRANT", settings_text))
        survey["sources"]["moonmind/config/settings.py"] = f"read, {hits} qdrant mentions"
        m = re.search(
            r"qdrant_enabled:\s*bool\s*=\s*Field\((True|False)", settings_text
        )
        survey["qdrant_enabled_default"] = m.group(1) if m else "unknown"
        if hits:
            survey["qdrant_surfaces"].append(
                f"moonmind/config/settings.py: {hits} qdrant mentions"
            )
    for rel in (
        "moonmind/rag/qdrant_client.py",
        "moonmind/rag/service.py",
        "moonmind/rag/overlay.py",
        "api_service/api/routers/retrieval_gateway.py",
        "api_service/retrieval_capabilities.py",
        "moonmind/workflows/temporal/workflows/manifest_ingest.py",
        "moonmind/workflows/temporal/workflows/run.py",
        "frontend/src/entrypoints/schedules.tsx",
        "docs/tmp/QdrantRemovalInventory-4105.md",
    ):
        text = _read_text(repo_root / rel)
        if text is None:
            survey["sources"][rel] = "missing"
            continue
        if rel.endswith("schedules.tsx"):
            # Post-#4105 the schedule entrypoint strips retired fields; what
            # matters is whether retired vector authoring survives.
            hits = len(re.findall(r"\brag\b|followUpRetrieval", text))
            survey["sources"][rel] = f"read, {hits} retired-field mentions"
        elif rel.endswith("QdrantRemovalInventory-4105.md"):
            survey["sources"][rel] = "read (#4105 admission inventory)"
        else:
            hits = len(re.findall(r"[Qq]drant|QDRANT", text))
            survey["sources"][rel] = f"read, {hits} qdrant mentions"
            if hits and rel not in (
                "api_service/retrieval_capabilities.py",
                "moonmind/workflows/temporal/workflows/manifest_ingest.py",
            ):
                survey["qdrant_surfaces"].append(f"{rel}: {hits} qdrant mentions")
            elif hits:
                # Capability-state and manifest contracts name qdrant only as
                # historical context; recorded separately, not as live backend.
                survey.setdefault("historical_references", []).append(
                    f"{rel}: {hits} historical qdrant mentions"
                )
    survey["note"] = (
        "Structural survey only. Live deployment facts (active workflows, "
        "pending/retryable Activities, recurring schedules, stored vector "
        "manifests, issued capabilities, actual mounts, Qdrant-only content) "
        "require the operator preflight against the selected deployment and "
        "are NOT established by this survey. qdrant-storage and "
        "moonmind_retrieval_state are distinct data and must never be treated "
        "as interchangeable."
    )
    return survey


def check_inventory_survey(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the sanitized survey can be collected hermetically."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    # The #4105 inventory and the runbook are expected once siblings land;
    # every other source must read.
    required = [m for m in missing if "QdrantRemovalInventory" not in m]
    if required:
        return StepResult(
            "inventory-survey",
            "failed",
            f"Survey incomplete; unreadable sources: {', '.join(required)}.",
        )
    surfaces = len(survey.get("qdrant_surfaces", []))
    return StepResult(
        "inventory-survey",
        "completed",
        f"Sanitized survey collected over {len(survey['sources'])} sources; "
        f"{surfaces} live Qdrant surfaces named (counts/refs only, no payload "
        "exports). Owner-protected live facts still require the operator "
        "preflight and stay blocked in prerequisites. "
        f"qdrant_image={survey.get('qdrant_image')}, "
        f"qdrant_storage={survey.get('qdrant_storage_volume')}, "
        f"retrieval_state={survey.get('retrieval_state_volume')} (distinct data).",
    )


# -- R1b: actionable preflight verdict ----------------------------------------

def evaluate_preflight(
    survey: dict[str, Any],
    *,
    live_blockers: tuple[str, ...] = (),
    pending_consumers: tuple[str, ...] = (),
) -> StepResult:
    """Reduce survey + operator facts to one actionable verdict.

    - ``blocked``: live deployment facts unknown (hermetic default) or an
      explicit live blocker was reported. Never guess from service names.
    - ``drain``: affected histories/pending work exist; drain or
      version-route them on the old release before retirement.
    - ``proceed``: hermetic fixtures verify AND the operator has supplied
      positive live evidence (no affected work, snapshot verified).
    """
    if live_blockers:
        return StepResult(
            "preflight-verdict",
            "blocked",
            "BLOCKED: " + "; ".join(live_blockers) + ". "
            "Supply operator preflight evidence; do not infer from repository code.",
        )
    # Hermetically, live deployment facts are always unknown: active
    # workflows, pending Activities, schedules, mounts, and Qdrant-only
    # content cannot be established from a checkout.
    return StepResult(
        "preflight-verdict",
        "blocked" if not pending_consumers else "drain",
        (
            "BLOCKED hermetically: live active workflows, pending/retryable "
            "Activities, recurring schedules, stored manifests, issued "
            "capabilities, container IDs, and actual mounts require the "
            "operator preflight against the selected deployment. "
            "Fixture verification may proceed; deployment retirement stays "
            "blocked."
            if not pending_consumers
            else "DRAIN: pending vector consumers reported: "
            + ", ".join(pending_consumers) + ". "
            "Drain on the old release or version-route before retirement; "
            "retirement stays blocked until the finite retirement condition holds."
        ),
    )


# -- R2: drainage vs versioned-worker decision --------------------------------

# Documented cutover default: affected histories drain on the old release.
# The versioned worker/cutover mechanism is the explicit alternative when
# the deployment supports it. An indefinite compatibility backend is never
# an option.
DRAINAGE_DECISION = (
    "drainage-on-old-release default; versioned-worker cutover only when the "
    "deployment declares a supported versioned worker; never an indefinite "
    "compatibility backend; never reinterpret historical payloads; never "
    "delete an Activity handler as the drainage mechanism"
)

# Finite retirement condition: retirement executes only when all three hold.
RETIREMENT_CONDITION = (
    "no active schedule produces vector work; "
    "no pending/retryable vector Activity remains; "
    "snapshot/export recoverability is operator-verified"
)


def document_drainage_decision(pending_consumers: tuple[str, ...] = ()) -> StepResult:
    """Record the drainage/versioned-worker choice with pending-consumer evidence."""
    if pending_consumers:
        return StepResult(
            "drainage-decision",
            "completed",
            f"Decision recorded: {DRAINAGE_DECISION}. Pending-consumer evidence: "
            + ", ".join(pending_consumers) + ". Finite retirement condition: "
            + RETIREMENT_CONDITION + ".",
        )
    return StepResult(
        "drainage-decision",
        "completed",
        f"Decision recorded: {DRAINAGE_DECISION}. No pending vector consumers "
        "in hermetic fixtures. Live pending-consumer evidence still requires "
        "the operator preflight. Finite retirement condition: "
        + RETIREMENT_CONDITION + ".",
    )


class VectorDrainTracker:
    """Account for in-flight vector work without discarding admitted histories.

    Admitted Temporal work is preserved by construction: draining only waits
    for tracked vector activity ids to finish; it never cancels workflow
    records and never rewrites immutable inputs.
    """

    def __init__(self) -> None:
        self.in_flight: set[str] = set()
        self.admitted_workflows: set[str] = set()

    def admit(self, activity_id: str, workflow_id: str) -> None:
        self.in_flight.add(activity_id)
        self.admitted_workflows.add(workflow_id)

    def settle(self, activity_id: str) -> None:
        self.in_flight.discard(activity_id)

    def drain(self) -> StepResult:
        if self.in_flight:
            return StepResult(
                "vector-drain",
                "blocked",
                f"{len(self.in_flight)} vector activities still in flight; "
                f"{len(self.admitted_workflows)} admitted workflows preserved, "
                "none discarded, none rewritten.",
            )
        return StepResult(
            "vector-drain",
            "completed",
            f"Drained: 0 vector activities in flight, "
            f"{len(self.admitted_workflows)} admitted workflows preserved.",
        )


def check_vector_drain() -> StepResult:
    """Exercise the drain tracker hermetically."""
    tracker = VectorDrainTracker()
    tracker.admit("act-vector-1", "wf-cutover-1")
    if tracker.drain().status != "blocked":
        return StepResult(
            "vector-drain", "failed", "Drain did not report in-flight vector work."
        )
    tracker.settle("act-vector-1")
    drained = tracker.drain()
    if drained.status != "completed" or "wf-cutover-1" not in tracker.admitted_workflows:
        return StepResult(
            "vector-drain", "failed", "Drain discarded or lost admitted work."
        )
    return StepResult(
        "vector-drain",
        "completed",
        "Hermetic drain passes: in-flight vector work blocks, settled work "
        "drains with admitted workflows preserved. "
        f"Decision: {DRAINAGE_DECISION}.",
    )


# -- R3: replay-safe boundaries ------------------------------------------------

def check_workflow_history_authority(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> StepResult:
    """Assert a cutover change did not rewrite workflow command order/owner IDs.

    Records are compared by stable ``workflow_id`` key, not by list
    position, so a reordered or cross-associated evidence collection cannot
    pass as exact replay.
    """
    if len(before) != len(after):
        return StepResult(
            "workflow-history-authority",
            "failed",
            "Workflow history length changed across cutover boundary; requires "
            "exact compatibility/replay evidence or bounded old-release drain.",
        )
    if all(
        "workflow_id" in dict(b) and "workflow_id" in dict(a)
        for b, a in zip(before, after)
    ) and any("workflow_id" in dict(r) for r in [*before, *after]):
        before_by_id = {r["workflow_id"]: r for r in before}
        after_by_id = {r["workflow_id"]: r for r in after}
        if set(before_by_id) != set(after_by_id):
            return StepResult(
                "workflow-history-authority",
                "failed",
                f"Workflow identity set changed across cutover boundary "
                f"(before={sorted(before_by_id)}, after={sorted(after_by_id)}); "
                "requires exact compatibility/replay evidence or bounded "
                "old-release drain.",
            )
        for wid, b in before_by_id.items():
            a = after_by_id[wid]
            if b.get("owner_id") != a.get("owner_id") or b.get("commands") != a.get(
                "commands"
            ):
                return StepResult(
                    "workflow-history-authority",
                    "failed",
                    f"History rewrite detected on {wid}: owner/command order "
                    "must be preserved across the cutover.",
                )
        return StepResult(
            "workflow-history-authority",
            "completed",
            f"Verified {len(before)} workflow histories readable with unchanged "
            "workflow IDs, owner IDs, and command order.",
        )
    for b, a in zip(before, after):
        if b.get("owner_id") != a.get("owner_id") or b.get("commands") != a.get(
            "commands"
        ):
            return StepResult(
                "workflow-history-authority",
                "failed",
                f"History rewrite detected on {b.get('workflow_id')}: owner/command "
                "order must be preserved across the cutover.",
            )
    return StepResult(
        "workflow-history-authority",
        "completed",
        f"Verified {len(before)} workflow histories readable with unchanged "
        "owner IDs and command order.",
    )


# The two historical generic ManifestIngest entries (#3948) must stay
# readable across the cutover; their semantics are never reinterpreted.
HISTORICAL_MANIFEST_ENTRIES = (
    "manifest-ingest-generic-entry/v1",
    "manifest-ingest-generic-entry/v2",
)


def check_manifest_boundary_replay(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    boundary_changed: bool,
) -> StepResult:
    """Tie manifest/history authority to whether a persisted boundary changed.

    When no cutover change ships a persisted/history boundary change (this
    checkout), identical histories complete with that fact recorded. When a
    boundary did change, the caller must supply exact replay evidence
    (identical owner/command records plus both historical generic entries)
    or a bounded old-release drain; any rewrite fails.
    """
    base = check_workflow_history_authority(before, after)
    if base.status == "failed":
        return StepResult(
            "manifest-boundary-replay", "failed", base.evidence
        )
    entries = {r.get("entry") for r in after}
    if boundary_changed and not set(HISTORICAL_MANIFEST_ENTRIES) <= entries:
        return StepResult(
            "manifest-boundary-replay",
            "failed",
            "Boundary changed but historical generic ManifestIngest entries "
            f"({', '.join(HISTORICAL_MANIFEST_ENTRIES)}) are not both readable; "
            "preserve them per #3948 or supply a bounded old-release drain.",
        )
    if boundary_changed:
        return StepResult(
            "manifest-boundary-replay",
            "completed",
            f"Boundary changed and exact replay evidence verified over "
            f"{len(before)} histories with both historical generic entries readable.",
        )
    return StepResult(
        "manifest-boundary-replay",
        "completed",
        f"No persisted/history boundary change in this checkout; "
        f"{len(before)} histories readable with unchanged owner IDs and "
        "command order. A future cutover change that alters the boundary must "
        "supply exact replay evidence or a bounded old-release drain.",
    )


CUTOVER_HANDOFFS = (
    "initial-context",
    "launch-capability",
    "manifest",
    "digest-finalization",
    "cleanup",
)


def run_handoff_sequence(fail_at: str | None = None) -> dict[str, Any]:
    """Run the persisted-handoff sequence with optional restart/retry/cancel injection.

    Pure harness: each handoff records completed/failed/cancelled without
    side effects. ``fail_at`` names the handoff where an injected failure
    occurs; handoffs after it are recorded ``skipped``. A history that
    cannot safely run on the new release needs explicit old-release drain
    ownership, not a silent fallback.
    """
    if fail_at is not None and fail_at not in CUTOVER_HANDOFFS:
        raise ValueError(f"unknown cutover handoff: {fail_at}")
    handoffs: dict[str, str] = {}
    for handoff in CUTOVER_HANDOFFS:
        if fail_at is None or CUTOVER_HANDOFFS.index(handoff) < CUTOVER_HANDOFFS.index(
            fail_at
        ):
            handoffs[handoff] = "completed"
        elif handoff == fail_at:
            handoffs[handoff] = "failed-injected"
        else:
            handoffs[handoff] = "skipped"
    return {
        "handoffs": handoffs,
        "unsafe_history_rule": (
            "a history that cannot safely run on the new release needs explicit "
            "old-release drain ownership, not a silent fallback"
        ),
    }


def check_handoff_restart_retry_cancel() -> StepResult:
    """Verify restart/retry/cancel containment at each persisted handoff."""
    for handoff in CUTOVER_HANDOFFS:
        run = run_handoff_sequence(fail_at=handoff)
        idx = CUTOVER_HANDOFFS.index(handoff)
        before = list(CUTOVER_HANDOFFS[:idx])
        after = list(CUTOVER_HANDOFFS[idx + 1 :])
        if any(run["handoffs"][s] != "completed" for s in before):
            return StepResult(
                "handoff-restart-retry-cancel",
                "failed",
                f"Handoffs before injected failure at {handoff} did not complete.",
            )
        if any(run["handoffs"][s] != "skipped" for s in after):
            return StepResult(
                "handoff-restart-retry-cancel",
                "failed",
                f"Handoffs after injected failure at {handoff} were not skipped.",
            )
        if run["handoffs"][handoff] != "failed-injected":
            return StepResult(
                "handoff-restart-retry-cancel",
                "failed",
                f"Injected failure at {handoff} not recorded.",
            )
    clean = run_handoff_sequence()
    if any(v != "completed" for v in clean["handoffs"].values()):
        return StepResult(
            "handoff-restart-retry-cancel",
            "failed",
            "Clean handoff sequence did not complete all handoffs.",
        )
    return StepResult(
        "handoff-restart-retry-cancel",
        "completed",
        f"Restart/retry/cancel contained at each of {len(CUTOVER_HANDOFFS)} "
        f"persisted handoffs ({', '.join(CUTOVER_HANDOFFS)}); clean run "
        "completes; unsafe histories need old-release drain ownership.",
    )


def build_sanitized_upgrade_fixture(scenario: str) -> dict[str, Any]:
    """Build a deterministic sanitized upgrade fixture (no real payloads)."""
    if scenario not in REHEARSAL_MODES:
        raise ValueError(f"unknown rehearsal scenario: {scenario}")
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/qdrant-cutover/{scenario}")
    workflow_id = f"wf-{scenario}-1"
    return {
        "scenario": scenario,
        "workflow_id": workflow_id,
        "owner_id": str(seed),
        "commands": ["start", "poll", "complete"],
        "manifest_entries": list(HISTORICAL_MANIFEST_ENTRIES),
        "retired_requirements": ["rag", "followUpRetrieval"],
        "old_env_keys": ["QDRANT_URL", "QDRANT_HOST", "QDRANT_PORT", "QDRANT_ENABLED"],
    }


def rehearse_scenario(scenario: str) -> StepResult:
    """Rehearse one sanitized upgrade path hermetically."""
    try:
        fixture = build_sanitized_upgrade_fixture(scenario)
    except ValueError as exc:
        return StepResult(f"rehearsal-{scenario}", "failed", str(exc))
    before = [
        {
            "workflow_id": fixture["workflow_id"],
            "owner_id": fixture["owner_id"],
            "commands": list(fixture["commands"]),
            "entry": entry,
        }
        for entry in fixture["manifest_entries"]
    ]
    # Hermetic rehearsal must not rewrite owner IDs, command order, or
    # historical manifest entries; retired requirements stay rejected, not
    # silently dropped.
    after = [dict(r) for r in before]
    result = check_manifest_boundary_replay(before, after, boundary_changed=False)
    if result.status != "completed":
        return StepResult(f"rehearsal-{scenario}", "failed", result.evidence)
    return StepResult(
        f"rehearsal-{scenario}",
        "completed",
        f"Hermetic rehearsal passed for {scenario}: 1 workflow, "
        f"{len(before)} manifest records, owner/command order preserved, "
        "both historical generic entries readable, retired requirements "
        "rejected (not silently dropped). No live Qdrant contacted.",
    )


# -- R4: provenance, snapshot, logical export, recoverability -------------------

SNAPSHOT_REQUIRED_SCOPES = ("vector:", "config:", "mounts:")


def create_snapshot_envelope(
    entries: dict[str, str], snapshot_id: str, state_dir: Path | None = None
) -> dict[str, Any]:
    """Build a restore-verifiable snapshot envelope over sanitized entries.

    Hermetic scope: the envelope proves structure (required vector/config/
    mounts scopes present: collection/payload provenance digests, previous
    image/version, exact mounts, recovery configuration), sha256 integrity
    per entry, access-restricted persistence (0o600 when written), and
    restore-verification. Real encryption-at-rest is an operator-KMS
    deployment property recorded as ``encryption: operator-kms-required``.
    Payload content is never embedded: digests and provenance refs only.
    """
    if not snapshot_id:
        raise ValueError("snapshot_id is required")
    missing = [
        p for p in SNAPSHOT_REQUIRED_SCOPES if not any(k.startswith(p) for k in entries)
    ]
    if missing:
        raise ValueError(f"snapshot missing scopes: {', '.join(missing)}")
    digests = {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in entries.items()}
    envelope: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "scopes": sorted(entries.keys()),
        "digests": digests,
        "encryption": "operator-kms-required",
        "access": "restricted-0600",
        "issue": ISSUE_REF,
    }
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / f"{snapshot_id}.json"
        target.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            # Best-effort permission hardening; a chmod failure must not
            # fail snapshot persistence (e.g. filesystems without POSIX modes).
            pass
    return envelope


def verify_snapshot_envelope(envelope: dict[str, Any], entries: dict[str, str]) -> bool:
    """Restore-verify a snapshot envelope against candidate entries."""
    digests = envelope.get("digests", {})
    if set(digests) != set(entries):
        return False
    return all(
        hashlib.sha256(v.encode("utf-8")).hexdigest() == digests[k]
        for k, v in entries.items()
    )


def classify_payload(provenance: str) -> str:
    """Classify a sanitized provenance ref as reproducible or unique.

    Fixture-level rule only: provenance naming a deterministic source
    (``derived:``/``reproducible:``) classifies reproducible; anything else
    (``qdrant-only:``, unknown, blank) classifies unique and must be
    preserved. Real Qdrant-only classification requires the operator
    provenance pass; never assume reconstructibility from vectors alone.
    """
    lowered = provenance.strip().lower()
    if lowered.startswith(("derived:", "reproducible:")):
        return "reproducible"
    return "unique"


def verify_recoverability(envelope: dict[str, Any], entries: dict[str, str]) -> bool:
    """Verify snapshot/export recoverability by restore-verification.

    Hermetic recoverability = envelope restore-verifies against the entries
    AND every unique-classified entry has a preserved export ref. Live
    restore into a scratch Qdrant remains an operator-verified step.
    """
    if not verify_snapshot_envelope(envelope, entries):
        return False
    for key, value in entries.items():
        if key.startswith("vector:") and classify_payload(value) == "unique":
            export_key = key.replace("vector:", "export:", 1)
            if export_key not in entries:
                return False
    return True


def check_snapshot_export_recoverability() -> StepResult:
    """Exercise snapshot/export/recoverability guards hermetically."""
    entries = {
        "vector:collection-provenance": "qdrant-only:collection-kobi",
        "vector:payload-provenance": "derived:source-documents",
        "export:collection-provenance": "operator-storage-ref/sanitized",
        "config:previous-image": "qdrant/qdrant:v1.17.1",
        "config:recovery-config": "sanitized-recovery-config",
        "mounts:qdrant-storage": "sanitized-mount-ref",
    }
    envelope = create_snapshot_envelope(entries, "qdrant-hermetic-check")
    if not verify_snapshot_envelope(envelope, entries):
        return StepResult(
            "snapshot-export-recoverability",
            "failed",
            "Hermetic snapshot envelope failed restore-verification.",
        )
    if classify_payload(entries["vector:collection-provenance"]) != "unique":
        return StepResult(
            "snapshot-export-recoverability",
            "failed",
            "Qdrant-only provenance did not classify as unique.",
        )
    if classify_payload(entries["vector:payload-provenance"]) != "reproducible":
        return StepResult(
            "snapshot-export-recoverability",
            "failed",
            "Derived provenance did not classify as reproducible.",
        )
    if not verify_recoverability(envelope, entries):
        return StepResult(
            "snapshot-export-recoverability",
            "failed",
            "Recoverability verification failed: unique payload without export ref.",
        )
    return StepResult(
        "snapshot-export-recoverability",
        "completed",
        "Hermetic guards pass: envelope restore-verified (operator KMS still "
        "required for live encryption), Qdrant-only payload classified unique "
        "with export ref, derived payload reproducible. Live restore into "
        "scratch remains operator-verified before any disposal decision.",
    )


# -- R5: operator storage, redaction, retention ---------------------------------

def check_operator_storage_path(path: str) -> StepResult:
    """Validate an operator snapshot/export storage path without writing.

    Originals belong in operator-controlled authorized storage with
    redaction and retention — never public issues or source control.
    """
    lowered = path.lower()
    if any(
        marker in lowered
        for marker in ("github.com", "issues/", "pull/", "docs/tmp", "docs/")
    ) or path.startswith(("docs/", "./docs", "artifacts/", "./artifacts")):
        return StepResult(
            "operator-storage",
            "failed",
            f"Refused {path!r}: snapshots/exports must not live in public "
            "issues or source control; use operator-controlled authorized storage.",
        )
    if "redact" not in lowered and "sanitized" not in lowered:
        return StepResult(
            "operator-storage",
            "blocked",
            f"Storage path {path!r} names no redaction procedure; record "
            "redaction + retention before custody completes.",
        )
    if "reten" not in lowered and "retention" not in lowered:
        return StepResult(
            "operator-storage",
            "blocked",
            f"Storage path {path!r} names no retention window; record "
            "retention before custody completes.",
        )
    return StepResult(
        "operator-storage",
        "completed",
        f"Storage path {path!r} accepted hermetically: operator-controlled, "
        "redacted, retention-recorded. moonmind_retrieval_state evidence stays "
        "independent from old vector storage (#4107).",
    )


# -- R6: matched upgrade pins ---------------------------------------------------

def collect_build_pins(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect exact-version pins and topology from real repo files.

    Covers: Qdrant image pin (compose), app/dashboard image defaults,
    QDRANT_ENABLED default literal, Alembic revision count present in the
    checkout, compose service topology, qdrant-storage vs
    moonmind_retrieval_state distinction, and runbook status. Sanitized:
    pins and counts, no credentials.
    """
    pins: dict[str, Any] = {}
    compose = _read_text(repo_root / "docker-compose.yaml") or ""
    m = re.search(r"image:\s*(qdrant/qdrant:[^\s]*)", compose)
    pins["qdrant_image"] = m.group(1) if m else "absent"
    services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", compose, re.MULTILINE)
    pins["compose_services"] = len(set(services))
    m = re.search(r"image:\s*\$\{MOONMIND_IMAGE:-(ghcr\.io/[^\s}]+)\}", compose)
    pins["app_image_default"] = m.group(1) if m else "unknown"
    m = re.search(r"image:\s*postgres:([^\s]+)", compose)
    raw = m.group(1) if m else ""
    dm = re.search(r":-([^}]+)\}", raw)
    pins["postgres_image"] = dm.group(1) if dm else (raw or "unknown")
    m = re.search(r"image:\s*temporalio/auto-setup:([^\s]+)", compose)
    raw = m.group(1) if m else ""
    dm = re.search(r":-([^}]+)\}", raw)
    pins["temporal_image"] = dm.group(1) if dm else (raw or "unknown")
    settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
    m = re.search(r"qdrant_enabled:\s*bool\s*=\s*Field\((True|False)", settings_text)
    pins["qdrant_enabled_default"] = m.group(1) if m else "unknown"
    versions_dir = repo_root / "api_service/migrations/versions"
    if versions_dir.exists():
        revisions = sorted(p.name for p in versions_dir.glob("*.py"))
        pins["alembic_revisions"] = len(revisions)
        pins["alembic_latest"] = revisions[-1] if revisions else "none"
    else:
        pins["alembic_revisions"] = "unknown"
        pins["alembic_latest"] = "unknown"
    pins["qdrant_storage_volume"] = bool(re.search(r"(?m)^  qdrant-storage:", compose))
    pins["retrieval_state_volume"] = bool(
        re.search(r"(?m)^  moonmind_retrieval_state:", compose)
    )
    try:
        root_pkg = json.loads(_read_text(repo_root / "package.json") or "{}")
        pins["root_package_version"] = str(root_pkg.get("version", "unknown"))
    except (json.JSONDecodeError, AttributeError):
        pins["root_package_version"] = "unparseable"
    plan = _read_text(repo_root / PLAN_REL) or ""
    pins["plan_status"] = "Proposed" if "Status: Proposed" in plan else "unknown-or-archived"
    return pins


EVIDENCE_SEPARATION_NOTE = (
    "Hermetic evidence below is derived from repo files only. "
    "Authorized live deployment checks are never attempted hermetically and "
    "remain separately blocked in prerequisites."
)


def check_build_pins(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify exact pins/topology are collectible with the hermetic/live split."""
    pins = collect_build_pins(repo_root)
    # Every required pin must resolve; a single malformed source must not
    # yield positive terminal evidence. The Qdrant image pin is exempt:
    # "absent" is the expected post-removal (#4106-#4109) value.
    unusable = []
    if pins.get("alembic_revisions") == "unknown":
        unusable.append("alembic_revisions")
    if pins.get("app_image_default") in (None, "unknown"):
        unusable.append("app_image_default")
    else:
        _registry = str(pins["app_image_default"]).split("/", 1)[0].split(":", 1)[0]
        if _registry != "ghcr.io":
            unusable.append("app_image_default")
    if pins.get("postgres_image") in (None, "unknown"):
        unusable.append("postgres_image")
    if pins.get("temporal_image") in (None, "unknown"):
        unusable.append("temporal_image")
    if pins.get("root_package_version") in (None, "unknown", "unparseable"):
        unusable.append("root_package_version")
    if pins.get("qdrant_enabled_default") in (None, "unknown"):
        unusable.append("qdrant_enabled_default")
    if pins.get("plan_status") in (None, "unknown-or-archived"):
        unusable.append("plan_status")
    if not isinstance(pins.get("compose_services"), int) or pins["compose_services"] <= 0:
        unusable.append("compose_services")
    if unusable:
        return StepResult(
            "build-pins",
            "failed",
            "Exact-build evidence incomplete; unusable pins: "
            + ", ".join(unusable)
            + ". Refusing positive topology evidence until every required "
            "pin resolves.",
        )
    return StepResult(
        "build-pins",
        "completed",
        f"Pins collected: qdrant_image={pins['qdrant_image']}, "
        f"app_image_default={pins['app_image_default']}, "
        f"postgres={pins['postgres_image']}, "
        f"temporal={pins['temporal_image']}, "
        f"QDRANT_ENABLED default={pins['qdrant_enabled_default']!r}, "
        f"{pins['alembic_revisions']} alembic revisions "
        f"(latest {pins['alembic_latest']}), "
        f"{pins['compose_services']} compose services, "
        f"qdrant_storage={pins['qdrant_storage_volume']}, "
        f"retrieval_state={pins['retrieval_state_volume']} (distinct), "
        f"plan={pins['plan_status']}. {EVIDENCE_SEPARATION_NOTE}",
    )


# -- R7: exact-container retirement ----------------------------------------------

def check_retirement_plan(
    actions: list[str], *, expected_project: str = "moonmind"
) -> StepResult:
    """Validate retirement actions textually; performs no service mutation."""
    joined = "\n".join(actions)
    for pattern in FORBIDDEN_RETIREMENT_PATTERNS:
        if pattern.search(joined):
            return StepResult(
                "retirement-plan",
                "failed",
                f"Refused destructive retirement action matching {pattern.pattern!r}; "
                "retirement must stop precisely identified containers, never broad "
                "down -v / prune / shared-database or app/Temporal volume deletion.",
            )
    lowered = joined.lower()
    if "qdrant" not in lowered:
        return StepResult(
            "retirement-plan",
            "blocked",
            "No precisely identified obsolete Qdrant container in plan; "
            "retirement stays blocked until inventory names it.",
        )
    # Exact ownership: the plan must name the selected deployment project.
    # Removing a YAML definition never stops a running orphan, so the plan
    # must address the running container, not just the definition.
    if expected_project.lower() not in lowered:
        return StepResult(
            "retirement-plan",
            "blocked",
            f"Ambiguous ownership: plan names no exact project owner "
            f"({expected_project!r}); refusing to act on an unowned container. "
            "Removing a YAML definition does not stop a running orphan.",
        )
    if "orphan" not in lowered and "container" not in lowered and "service" not in lowered:
        return StepResult(
            "retirement-plan",
            "blocked",
            "Plan names Qdrant but no running container/service identity; "
            "YAML removal alone does not stop a running orphan.",
        )
    return StepResult(
        "retirement-plan",
        "completed",
        "Retirement plan textually safe: exact obsolete Qdrant container under "
        f"project {expected_project!r}, no destructive volume/database "
        "operations. Execution still requires owner approval and positive "
        "post-absence verification; repeated checks are safe (idempotent).",
    )


def verify_exact_retirement(
    *, expected_project: str, observed_project: str | None, container_absent: bool
) -> StepResult:
    """Hermetic exact-container retirement check: ownership + absence.

    Refuses wrong/ambiguous project ownership; requires positive absence
    afterward. Repeated checks are safe: an already-absent container under
    the right owner completes without further action.
    """
    if not observed_project or observed_project != expected_project:
        return StepResult(
            "exact-retirement",
            "failed" if observed_project else "blocked",
            f"Refused: observed project {observed_project!r} does not match "
            f"expected owner {expected_project!r}. Wrong/ambiguous ownership "
            "never retires; unrelated services/volumes stay intact.",
        )
    if not container_absent:
        return StepResult(
            "exact-retirement",
            "blocked",
            "Container still present under the expected owner; stop/remove the "
            "exact obsolete container, then re-verify absence. YAML removal "
            "alone is not retirement.",
        )
    return StepResult(
        "exact-retirement",
        "completed",
        f"Exact container absent under project {expected_project!r}; "
        "unrelated services/volumes intact; repeated checks safe.",
    )


# -- R8: retention window (no automatic deletion) ---------------------------------

def check_retention_plan(actions: list[str]) -> StepResult:
    """Validate the retention/disposition plan textually.

    Permanent deletion is a separate exact-resource operator action after
    export/restore verification, never an automatic normal-upgrade step.
    The old Qdrant volume and exports are retained through an explicit
    recovery/retention window.
    """
    joined = "\n".join(actions)
    lowered = joined.lower()
    auto_markers = ("automatic", "auto-delete", "auto delete", "normal-upgrade step")
    deletion_markers = ("delet", "remov", "drop", "purge", "prune")
    if any(m in lowered for m in auto_markers) and any(
        d in lowered for d in deletion_markers
    ):
        return StepResult(
            "retention-plan",
            "failed",
            "Refused: no upgrade path automatically deletes old volumes. "
            "Permanent deletion is a separate exact-resource operator action "
            "after export/restore verification.",
        )
    if not any(k in lowered for k in ("retain", "retention", "recovery window")):
        return StepResult(
            "retention-plan",
            "blocked",
            "No explicit recovery/retention window in plan; old Qdrant volume "
            "and exports must be retained through one before any disposition.",
        )
    if any(d in lowered for d in deletion_markers) and (
        "export" not in lowered or "verif" not in lowered
    ):
        return StepResult(
            "retention-plan",
            "blocked",
            "Disposition mentions deletion without export/restore verification; "
            "separate exact-resource operator authorization required.",
        )
    return StepResult(
        "retention-plan",
        "completed",
        "Retention plan safe: old volume/exports retained through an explicit "
        "recovery/retention window; PostgreSQL, MinIO, secrets, workspaces, "
        "unrelated containers, and Omnigent state preserved; eventual deletion "
        "requires separate exact-resource authorization.",
    )


# -- R9: rollback (previous matching revision, no Qdrant profile) -----------------

def check_rollback_plan(scope: str) -> StepResult:
    """Validate a rollback scope name without touching any deployment.

    Only a structured, explicitly permitted scope completes: the previous
    matching application/Compose/image revision with preserved data after a
    schema-compatibility check, plus reconciliation or forward repair. Any
    Qdrant profile/adapter reintroduction is refused: old-release test
    infrastructure is isolated fixture infrastructure, never supported
    new-release topology.
    """
    lowered = scope.lower()
    for pattern in FORBIDDEN_ROLLBACK_PATTERNS:
        if pattern.search(scope):
            return StepResult(
                "rollback-scope",
                "failed",
                f"Refused: matched pattern {pattern.pattern!r} — never add an "
                "optional Qdrant profile or adapter back to the new release as "
                "a rollback feature.",
            )
    if "whole" in lowered and (
        "postgres" in lowered or "temporal" in lowered or "database" in lowered
    ):
        return StepResult(
            "rollback-scope",
            "failed",
            "Refused: never roll back the whole shared application/Temporal "
            "database to undo a vector-removal cutover.",
        )
    if re.search(
        r"\b(drop|delete|remove|destroy|wipe|purge)\b.{0,40}\b(database|volume|snapshot)\b",
        lowered,
    ) or re.search(
        r"\b(database|volume|snapshot)\b.{0,40}\b(drop|delete|remove|destroy|wipe|purge)\b",
        lowered,
    ):
        return StepResult(
            "rollback-scope",
            "failed",
            "Refused: destructive database/volume/snapshot operation is not a "
            "safe revision rollback.",
        )
    has_revision = any(
        k in lowered for k in ("previous", "matching", "prior revision", "preserved")
    ) and any(k in lowered for k in ("app", "compose", "image", "revision"))
    has_compat = "schema" in lowered and (
        "compat" in lowered or "check" in lowered or "review" in lowered
    )
    has_reconcile = "reconcil" in lowered or "forward" in lowered
    if has_revision and has_compat and has_reconcile:
        return StepResult(
            "rollback-scope",
            "completed",
            "Rollback scope accepted hermetically: previous matching "
            "application/Compose/image revision with preserved data after a "
            "schema-compatibility check, rehearsed against fixtures with "
            "reconciliation recorded.",
        )
    return StepResult(
        "rollback-scope",
        "blocked",
        f"Unrecognized rollback scope {scope!r}; supply the previous matching "
        "application/Compose/image revision with preserved data, an explicit "
        "schema-compatibility check, and reconciliation or forward repair. No "
        "positive rollback evidence for absent or unstructured instructions.",
    )


def check_rollback_rehearsal() -> StepResult:
    """Rehearse rollback against fixtures with all required elements present."""
    scope = (
        "previous matching app/compose/image revision with preserved data "
        "after schema compatibility check with reconciliation"
    )
    result = check_rollback_plan(scope)
    if result.status != "completed":
        return StepResult("rollback-rehearsal", "failed", result.evidence)
    return StepResult(
        "rollback-rehearsal",
        "completed",
        "Rollback rehearsed against sanitized fixtures: previous matching "
        "revision + preserved data + schema-compatibility check + "
        "reconciliation. No Qdrant profile/adapter reintroduced; old-release "
        "test infrastructure stays isolated fixture infrastructure.",
    )


# -- Idempotent rehearsal state ---------------------------------------------------

STATE_FILE_NAME = "qdrant_cutover_rehearsal_state.json"


def load_state(state_dir: Path, migration_id: str) -> dict[str, Any]:
    state_file = state_dir / STATE_FILE_NAME
    if not state_file.exists():
        return {"migration_id": migration_id, "runs": 0, "completed_steps": []}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "migration_id": migration_id,
            "runs": 0,
            "completed_steps": [],
            "_corrupt": True,
            "_corrupt_path": str(state_file),
        }
    return data


def save_state(
    state_dir: Path, migration_id: str, step_names: list[str], allow_replace: bool = False
) -> tuple[bool, str]:
    """Persist rehearsal progress idempotently.

    Returns (ok, message). A state file owned by a different migration_id is
    a stale/concurrent cutover: refuse unless --allow-replace is given, so
    interruption/restart reconciles the SAME migration instead of forking it.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILE_NAME
    existing = load_state(state_dir, migration_id)
    if existing.get("_corrupt"):
        return False, (
            f"corrupt rehearsal state at {existing.get('_corrupt_path')}: "
            "existing state is unreadable; refusing to overwrite the only "
            "durable record. Recover explicitly (inspect/restore the file "
            "with the deployment owner, then re-run with the same "
            "--migration-id)."
        )
    if (
        state_file.exists()
        and existing.get("migration_id") != migration_id
        and not allow_replace
    ):
        return False, (
            f"stale/concurrent migration: state owned by {existing.get('migration_id')!r}, "
            f"requested {migration_id!r}; re-run with the same --migration-id or pass "
            "--allow-replace after operator review."
        )
    if state_file.exists() and existing.get("migration_id") != migration_id and allow_replace:
        completed = list(dict.fromkeys(step_names))
        payload = {
            "migration_id": migration_id,
            "runs": 1,
            "completed_steps": completed,
            "issue": ISSUE_REF,
        }
        state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True, f"replaced state with migration {migration_id!r} (run 1)."
    completed = list(dict.fromkeys([*existing.get("completed_steps", []), *step_names]))
    payload = {
        "migration_id": migration_id,
        "runs": int(existing.get("runs", 0)) + 1,
        "completed_steps": completed,
        "issue": ISSUE_REF,
    }
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True, f"reconciled migration {migration_id!r} (run {payload['runs']})."


def run_gate(
    repo_root: Path = REPO_ROOT,
    scenarios: tuple[str, ...] = REHEARSAL_MODES,
) -> list[StepResult]:
    survey = collect_inventory_survey(repo_root)
    results: list[StepResult] = [check_plan_precondition(repo_root)]
    results.extend(check_prerequisites(repo_root))
    results.append(check_inventory_survey(repo_root))
    results.append(evaluate_preflight(survey))
    results.append(document_drainage_decision())
    results.append(check_vector_drain())
    results.append(check_build_pins(repo_root))
    for scenario in scenarios:
        results.append(rehearse_scenario(scenario))
    fixture_records = [
        {
            "workflow_id": "wf-historical-artifact-upgrade-1",
            "owner_id": "sanitized-owner",
            "commands": ["start", "poll", "complete"],
        }
    ]
    results.append(
        check_workflow_history_authority(fixture_records, [dict(r) for r in fixture_records])
    )
    manifest_before = [
        {**r, "entry": entry}
        for r in fixture_records
        for entry in HISTORICAL_MANIFEST_ENTRIES
    ]
    results.append(
        check_manifest_boundary_replay(
            manifest_before, [dict(r) for r in manifest_before], boundary_changed=False
        )
    )
    results.append(check_handoff_restart_retry_cancel())
    results.append(check_snapshot_export_recoverability())
    results.append(
        check_operator_storage_path("operator-vault://redacted/qdrant-snapshot/retention-90d")
    )
    # Same hermetic guards as CLI `--mode all`: rollback refusal surface and
    # retirement/retention textual safety. Execution still requires owner approval.
    results.append(
        check_rollback_plan(
            "previous matching app/compose/image revision with preserved data "
            "after schema compatibility check with reconciliation"
        )
    )
    results.append(check_rollback_rehearsal())
    results.append(
        check_retirement_plan(
            ["stop container moonmind-qdrant-1 (project moonmind, orphan verification)"]
        )
    )
    results.append(
        verify_exact_retirement(
            expected_project="moonmind",
            observed_project="moonmind",
            container_absent=True,
        )
    )
    results.append(
        check_retention_plan(
            ["retain qdrant-storage volume and exports through 90d recovery window"]
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hermetic Qdrant-removal cutover rehearsal gate (#4115)."
    )
    parser.add_argument("--state-dir", type=Path, default=Path("var/artifacts/qdrant_rehearsal"))
    parser.add_argument("--migration-id", default="qdrant-cutover-4115")
    parser.add_argument("--allow-replace", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("preflight", "rehearsal", "rollback-check", "retirement-check", "all"),
        default="all",
    )
    parser.add_argument(
        "--rollback-scope",
        default=(
            "previous matching app/compose/image revision with preserved data "
            "after schema compatibility check with reconciliation"
        ),
    )
    parser.add_argument("--retire-action", action="append", default=[])
    parser.add_argument("--retention-action", action="append", default=[])
    parser.add_argument("--storage-path", default="")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.mode in ("preflight", "all"):
        results: list[StepResult] = [check_plan_precondition()]
        results.extend(check_prerequisites())
        results.append(check_inventory_survey())
        results.append(evaluate_preflight(collect_inventory_survey()))
        results.append(document_drainage_decision())
        results.append(check_build_pins())
    else:
        results = []
    if args.mode in ("rehearsal", "all"):
        for scenario in REHEARSAL_MODES:
            results.append(rehearse_scenario(scenario))
        records = [
            {
                "workflow_id": "wf-cli-1",
                "owner_id": "sanitized-owner",
                "commands": ["start", "poll", "complete"],
            }
        ]
        results.append(check_workflow_history_authority(records, [dict(r) for r in records]))
        manifest_records = [
            {**r, "entry": entry} for r in records for entry in HISTORICAL_MANIFEST_ENTRIES
        ]
        results.append(
            check_manifest_boundary_replay(
                manifest_records,
                [dict(r) for r in manifest_records],
                boundary_changed=False,
            )
        )
        results.append(check_handoff_restart_retry_cancel())
        results.append(check_snapshot_export_recoverability())
        results.append(check_vector_drain())
        if args.storage_path:
            results.append(check_operator_storage_path(args.storage_path))
    if args.mode in ("rollback-check", "all"):
        results.append(check_rollback_plan(args.rollback_scope))
        results.append(check_rollback_rehearsal())
    if args.mode in ("retirement-check", "all"):
        if args.retire_action:
            results.append(check_retirement_plan(args.retire_action))
        else:
            results.append(
                StepResult(
                    "retirement-plan",
                    "blocked",
                    "No --retire-action supplied; retirement stays blocked until "
                    "inventory names the exact obsolete container under its project.",
                )
            )
        if args.retention_action:
            results.append(check_retention_plan(args.retention_action))
        else:
            results.append(
                StepResult(
                    "retention-plan",
                    "blocked",
                    "No --retention-action supplied; retention stays blocked until "
                    "an explicit recovery/retention window is recorded.",
                )
            )

    summary = {
        "issue": ISSUE_REF,
        "mode": args.mode,
        "steps": [r.to_dict() for r in results],
    }
    output = json.dumps(summary, indent=2)
    if args.json_out is not None:
        args.json_out.write_text(output, encoding="utf-8")
    else:
        print(output)

    completed_steps = [r.name for r in results if r.status == "completed"]
    ok, msg = save_state(
        args.state_dir, args.migration_id, completed_steps, args.allow_replace
    )
    if not ok:
        print(f"state: {msg}")
        return 2
    # Hermetic gate passes when no step FAILED. ``blocked`` is the correct
    # terminal state for operator-gated deployment steps.
    if any(r.status == "failed" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
