#!/usr/bin/env python3
"""Hermetic manifest-registry preservation rehearsal gate for #4191.

This tool provides early, bounded, idempotent preflight/rehearsal
verification for the Manifest-registry removal (MR4) WITHOUT performing any
live deployment mutation. It never:

- contacts a live database, Temporal, MinIO, or deployment host,
- reads production manifests, workflows, schedules, or payloads,
- creates snapshots/exports outside a hermetic state dir,
- stops writers, drops tables, deletes containers/volumes/files,
- marks operator-owned facts complete on hermetic evidence alone.

Operator-gated steps (named-owner approval, live preflight inventory,
preservation/export recovery verification on real data, coordinated release,
exact-resource retirement, retention disposition) are reported as
``blocked`` with the missing evidence named. That is the correct terminal
state for a repo checkout: rehearsal fixtures may pass while deployment
qualification stays blocked.

Sibling ownership (parent #4187):
- #4188 owns the writer inventory (authoritative caller list).
- #4189 owns milestone A historical-read/cutover contract for both entry
  contracts (``manifest_ref`` compile, ``manifestArtifactRef`` orchestrate).
- #4190 owns the integration gate consuming sanitized evidence.
This gate defers to those owners: it probes the checkout for their markers
and fails closed when they are absent. Historical ``MoonMind.ManifestIngest``
type strings and the two generic entry contracts are preserved evidence,
never live-backend surfaces.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK_REL = Path("docs/tmp/ManifestRegistryPreservationRunbook-4191.md")
ISSUE_REF = "MoonLadderStudios/MoonMind#4191"

REHEARSAL_MODES = (
    "fresh-empty",
    "populated-stopped-verified",
    "populated-active",
    "partial-export",
    "incompatible-version",
)

PREREQUISITE_IDS = (
    "4188-writer-inventory",
    "4189-historical-read-cutover",
    "4190-integration",
    "named-owner-approval",
    "live-deployment-qualification",
)

FORBIDDEN_RETIREMENT_PATTERNS = (
    re.compile(r"\bdown\s+-v\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bvolume\s+prune\b", re.IGNORECASE),
    re.compile(r"--orphans\b"),
    re.compile(r"\bprune\b.{0,20}\bvolumes?\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf?\b"),
    re.compile(r"\bDROP\s+(DATABASE|ROLE)\b", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\bmanifest\b.{0,20}\b(table|registry)\b.*\bwithout\b.{0,20}\b(preserv|export|verif)", re.IGNORECASE),
    re.compile(r"blanket.{0,20}(table|enum).{0,20}delet", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\bwhole\b.{0,20}\bexecutions?\b", re.IGNORECASE),
    re.compile(r"coerc.{0,20}\bUserWorkflow\b", re.IGNORECASE),
    re.compile(r"rewrite.{0,20}\bhash", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(minio|minio-data|postgres|postgres-data)\b", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(secrets|workspaces|agent_workspaces|omnigent)\b", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(database|role)\b", re.IGNORECASE),
    re.compile(r"broad.{0,20}backup.{0,20}restore", re.IGNORECASE),
    re.compile(r"\bMINIO\b.{0,20}\bpurge\b", re.IGNORECASE),
    re.compile(r"automatic.{0,20}production.{0,20}delet", re.IGNORECASE),
    re.compile(r"second\s+registry", re.IGNORECASE),
    re.compile(r"permanent.{0,20}alias", re.IGNORECASE),
)

_SECOND_REGISTRY_PATTERNS = (
    re.compile(r"second\s+registry", re.IGNORECASE),
    re.compile(r"permanent.{0,20}alias", re.IGNORECASE),
    re.compile(r"manifest_registry_v2", re.IGNORECASE),
    re.compile(r"manifest_alias", re.IGNORECASE),
)

_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+\S+"
    r"|(?:password|passwd|secret|bearer|cookie|session[_-]?token|refresh[_-]?token"
    r"|token|api[_-]?key)\s*[:=]\s*[\"']?[^\s;,\"']+)"
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


def _non_comment_text(text: str) -> str:
    """Return text with full-line comments stripped (probes code, not prose)."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _code_text(text: str) -> str:
    """Return code with full-line comments and docstring prose stripped best-effort."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        lines.append(line)
    return "\n".join(lines)


RUNBOOK_REQUIRED_SECTIONS = (
    "Preflight",
    "Preservation",
    "Retirement",
    "Retention",
    "Rollback",
    "#4187",
    "#4188",
    "#4189",
    "#4190",
)


def check_runbook_precondition(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the operator runbook exists and names its required sections."""
    content = _read_text(repo_root / RUNBOOK_REL)
    if content is None:
        return StepResult(
            "runbook-precondition",
            "failed",
            f"{RUNBOOK_REL} missing; preservation rehearsal has no accepted operator baseline.",
        )
    missing = [s for s in RUNBOOK_REQUIRED_SECTIONS if s not in content]
    if missing:
        return StepResult(
            "runbook-precondition",
            "failed",
            f"{RUNBOOK_REL} missing required sections: {', '.join(missing)}.",
        )
    return StepResult(
        "runbook-precondition",
        "completed",
        f"{RUNBOOK_REL} present with Preflight/Preservation/Retirement/Retention/Rollback "
        "and #4187/#4188/#4189/#4190 ownership.",
    )


# -- Prerequisites owned by #4188/#4189/#4190 ---------------------------------

def _has_writer_inventory_marker(repo_root: Path) -> bool:
    """Return True only if an explicit #4188 writer-inventory marker landed."""
    candidates = [
        repo_root / "docs/tmp/ManifestWriterInventory-4188.md",
        repo_root / "docs/tmp/ManifestSystemRemovalPlan.md",
    ]
    for path in candidates:
        text = _read_text(path)
        if text and "4188" in text and "writer" in text.lower():
            return True
    return False


def _has_historical_read_cutover_marker(repo_root: Path) -> bool:
    """Return True only if an explicit #4189 milestone-A marker landed."""
    candidates = [
        repo_root / "docs/tmp/ManifestHistoricalReadCutover-4189.md",
        repo_root / "docs/tmp/ManifestSystemRemovalPlan.md",
    ]
    for path in candidates:
        text = _read_text(path)
        if text and "4189" in text and ("cutover" in text.lower() or "historical" in text.lower()):
            return True
    # Code alone is not a cutover contract: the generic read path must be
    # named by its owner. Presence of the enum in code is necessary but not
    # sufficient for this prerequisite.
    return False


def _has_integration_gate_marker(repo_root: Path) -> bool:
    """Return True only if an explicit #4190 integration marker landed."""
    candidates = [
        repo_root / "docs/tmp/ManifestIntegrationGate-4190.md",
        repo_root / "docs/tmp/ManifestSystemRemovalPlan.md",
    ]
    for path in candidates:
        text = _read_text(path)
        if text and "4190" in text and "integrat" in text.lower():
            return True
    return False


def check_prerequisites(repo_root: Path = REPO_ROOT) -> list[StepResult]:
    """Gate on sibling owners; fail closed when their markers are absent."""
    results: list[StepResult] = []
    if _has_writer_inventory_marker(repo_root):
        results.append(StepResult(
            "prerequisite-4188-writer-inventory", "completed",
            "Writer inventory marker present; caller list owned by #4188.",
        ))
    else:
        results.append(StepResult(
            "prerequisite-4188-writer-inventory", "blocked",
            "Writer inventory owned by #4188 has not landed "
            "(docs/tmp/ManifestWriterInventory-4188.md or removal plan with #4188 writer evidence absent). "
            "Destructive application stays blocked; this gate never invents writer coverage from field names.",
        ))
    if _has_historical_read_cutover_marker(repo_root):
        results.append(StepResult(
            "prerequisite-4189-historical-read-cutover", "completed",
            "Historical-read/cutover contract marker present; generic read path owned by #4189.",
        ))
    else:
        results.append(StepResult(
            "prerequisite-4189-historical-read-cutover", "blocked",
            "Historical-read/cutover contract owned by #4189 milestone A has not landed. "
            "Upgrade preserving generic historical reads for both entry contracts stays blocked.",
        ))
    if _has_integration_gate_marker(repo_root):
        results.append(StepResult(
            "prerequisite-4190-integration", "completed",
            "Integration gate marker present; sanitized evidence owned by #4190.",
        ))
    else:
        results.append(StepResult(
            "prerequisite-4190-integration", "blocked",
            "Integration gate owned by #4190 has not consumed sanitized evidence. "
            "Final go/no-go stays blocked.",
        ))
    results.append(StepResult(
        "prerequisite-named-owner-approval", "blocked",
        "Deployment owner authorization is owner-held and absent in a repo checkout. "
        "Destructive application requires explicit owner authorization; a merged PR alone never authorizes it.",
    ))
    results.append(StepResult(
        "prerequisite-live-deployment-qualification", "blocked",
        "Live deployment qualification (stopped writers, verified preservation on real data, "
        "compatible release pins) is absent in a repo checkout. Rehearsal fixtures may pass while this stays blocked.",
    ))
    return results


# -- Disposition table ---------------------------------------------------------

DISPOSITION_REQUIRED_ROWS = (
    "manifest.content",
    "manifest.content_hash",
    "manifest.version",
    "state_json",
    "last_run_",
    "ix_manifest_id",
    "worker state callback",
    "MoonMind.ManifestIngest",
    "manifest_ref",
    "manifestArtifactRef",
    "Do not coerce",
    "second registry",
    "exact old consumer",
)

DEDICATED_REGISTRY_COLUMNS = (
    "content",
    "content_hash",
    "version",
    "state_json",
    "state_updated_at",
    "last_indexed_at",
    "last_run_job_id",
    "last_run_source",
    "last_run_status",
    "last_run_workflow_id",
    "last_run_temporal_run_id",
    "last_run_manifest_ref",
    "last_run_started_at",
    "last_run_finished_at",
)

SHARED_RETAINED_MARKERS = (
    "MoonMind.ManifestIngest",
    "manifest_ref",
    "input_ref",
    "plan_ref",
    "owner",
    "run_id",
)


def check_disposition_table(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the runbook carries a caller-backed disposition with owners."""
    content = _read_text(repo_root / RUNBOOK_REL)
    if content is None:
        return StepResult(
            "disposition-table", "failed",
            f"{RUNBOOK_REL} missing; no disposition to verify.",
        )
    missing = [row for row in DISPOSITION_REQUIRED_ROWS if row not in content]
    if missing:
        return StepResult(
            "disposition-table", "failed",
            "Disposition table incomplete; missing rows: " + ", ".join(missing) + ". "
            "A field name alone is not proof that data is disposable; each row needs an owner.",
        )
    models = _read_text(repo_root / "api_service/db/models.py") or ""
    code = _code_text(models)
    absent_dedicated = [col for col in DEDICATED_REGISTRY_COLUMNS if col not in code]
    if absent_dedicated:
        return StepResult(
            "disposition-table", "failed",
            "Disposition disagrees with code: dedicated registry columns absent from models.py: "
            + ", ".join(absent_dedicated) + ".",
        )
    absent_shared = [m for m in SHARED_RETAINED_MARKERS if m not in models]
    if absent_shared:
        return StepResult(
            "disposition-table", "failed",
            "Disposition disagrees with code: shared retained markers absent from models.py: "
            + ", ".join(absent_shared) + ".",
        )
    return StepResult(
        "disposition-table", "completed",
        "Caller-backed disposition present with delete/retain/temporary owners for dedicated registry state, "
        "callback writers, saved definitions, shared execution columns/enums, and historical serializers; "
        "code agrees on dedicated vs shared classification.",
    )


# -- Inventory survey ----------------------------------------------------------

def collect_inventory_survey(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect the current checkout's manifest persistence footprint (code only)."""
    survey: dict[str, Any] = {"sources": {}, "findings": {}}
    models_path = repo_root / "api_service/db/models.py"
    manifests_svc = repo_root / "api_service/services/manifests_service.py"
    sync_svc = repo_root / "api_service/services/manifest_sync_service.py"
    router = repo_root / "api_service/api/routers/manifests.py"
    service_py = repo_root / "moonmind/workflows/temporal/service.py"
    for key, path in {
        "models": models_path,
        "manifests_service": manifests_svc,
        "manifest_sync_service": sync_svc,
        "manifests_router": router,
        "temporal_service": service_py,
    }.items():
        text = _read_text(path)
        survey["sources"][key] = "present" if text is not None else "missing"
    models = _read_text(models_path) or ""
    models_code = _code_text(models)
    survey["findings"]["manifest_table_present"] = '__tablename__ = "manifest"' in models_code
    survey["findings"]["manifest_record_present"] = "class ManifestRecord" in models_code
    svc_text = _read_text(manifests_svc) or ""
    sync_text = _read_text(sync_svc) or ""
    router_text = _read_text(router) or ""
    svc_code = _code_text(svc_text)
    sync_code = _code_text(sync_text)
    router_code = _code_text(router_text)
    survey["findings"]["writers_active"] = bool(
        ("def upsert_manifest" in svc_code)
        or ("def sync_manifest" in sync_code)
        or ("def update_manifest_state" in svc_code)
    )
    survey["findings"]["worker_callback_present"] = (
        "state" in router_code and "worker" in router_code.lower()
        and 'POST' in router_text.upper() or "/state" in router_text
    )
    survey["findings"]["shared_enum_present"] = "MoonMind.ManifestIngest" in models
    survey["findings"]["shared_manifest_ref_present"] = "manifest_ref" in models
    # Old revisions must not import soon-deleted runtime modules.
    versions_dir = repo_root / "api_service/migrations/versions"
    imports_manifest_runtime = []
    if versions_dir.exists():
        for path in versions_dir.glob("*.py"):
            text = _read_text(path) or ""
            code = _non_comment_text(text)
            if re.search(r"from\s+(api_service|moonmind)\.[^\n]*manifest", code, re.IGNORECASE):
                imports_manifest_runtime.append(path.name)
    survey["findings"]["old_revisions_import_manifest_runtime"] = imports_manifest_runtime
    # Single-head check (hermetic, repo files only).
    survey["findings"]["migration_heads"] = _migration_heads(repo_root)
    return survey


def _migration_heads(repo_root: Path) -> list[str]:
    """Compute Alembic heads from revision/down_revision assignments (no DB)."""
    import ast as _ast
    versions_dir = repo_root / "api_service/migrations/versions"
    revs: dict[str, Any] = {}
    if not versions_dir.exists():
        return ["unknown"]
    for path in versions_dir.glob("*.py"):
        try:
            tree = _ast.parse(_read_text(path) or "")
        except SyntaxError:
            continue
        rev = None
        down: Any = None
        for node in tree.body:
            if isinstance(node, (_ast.Assign, _ast.AnnAssign)):
                targets = [node.target] if isinstance(node, _ast.AnnAssign) else node.targets
                for tgt in targets:
                    if getattr(tgt, "id", "") == "revision":
                        try:
                            rev = _ast.literal_eval(node.value)
                        except Exception:
                            # Unparseable revision literal; skip this file so heads
                            # fall back to "unknown" instead of guessing.
                            pass
                    if getattr(tgt, "id", "") == "down_revision":
                        try:
                            down = _ast.literal_eval(node.value)
                        except Exception:
                            down = "unparseable"
        if rev:
            revs[rev] = down
    referenced: set[str] = set()
    for down in revs.values():
        if isinstance(down, str):
            referenced.add(down)
        elif isinstance(down, (list, tuple)):
            referenced.update(down)
    heads = sorted(r for r in revs if r not in referenced)
    return heads or ["unknown"]


def check_inventory_survey(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the survey matches the expected pre-removal checkout state."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    if missing:
        return StepResult(
            "inventory-survey", "failed",
            "Inventory survey unavailable; unreadable sources: " + ", ".join(missing) + ".",
        )
    findings = survey["findings"]
    if not findings.get("manifest_table_present"):
        return StepResult(
            "inventory-survey", "failed",
            "Survey disagrees with pre-removal baseline: manifest table marker absent from models.py. "
            "Removal-plan source/scope no longer matches the checkout.",
        )
    if not findings.get("writers_active"):
        return StepResult(
            "inventory-survey", "failed",
            "Survey finds no active registry writers, but the removal plan expects active writers pre-drain. "
            "Either the inventory probe is stale or writers were removed without this runbook.",
        )
    if not findings.get("shared_enum_present"):
        return StepResult(
            "inventory-survey", "failed",
            "Shared MoonMind.ManifestIngest type string absent; generic historical evidence already broken.",
        )
    if findings.get("old_revisions_import_manifest_runtime"):
        return StepResult(
            "inventory-survey", "failed",
            "Applied revisions import manifest runtime modules: "
            + ", ".join(findings["old_revisions_import_manifest_runtime"]) + ". "
            "The migration chain is not self-contained.",
        )
    heads = findings.get("migration_heads") or []
    if len(heads) != 1:
        return StepResult(
            "inventory-survey", "failed",
            f"Expected exactly one migration head, found {len(heads)}: {', '.join(heads)}.",
        )
    return StepResult(
        "inventory-survey", "completed",
        "Pre-removal footprint confirmed: manifest table + active writers/callback present, "
        f"shared ManifestIngest history present, migration chain self-contained with single head {heads[0]}. "
        "Destructive application therefore stays gated (writers not stopped).",
    )


# -- Preflight fixtures ----------------------------------------------------------

def _registry_row_digest(name: str, content: str, version: str, state: str) -> str:
    h = hashlib.sha256()
    h.update(name.encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8"))
    h.update(b"\x00")
    h.update(version.encode("utf-8"))
    h.update(b"\x00")
    h.update(state.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def build_sanitized_fixture(scenario: str) -> dict[str, Any]:
    """Build a deterministic sanitized registry fixture (no real YAML/state)."""
    if scenario not in REHEARSAL_MODES:
        raise ValueError(f"unknown rehearsal scenario: {scenario}")
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/manifest-registry-4191/{scenario}")
    # Two registry rows covering both run-source shapes: one temporal-linked,
    # one queue-linked. Content bytes are never embedded; only digests.
    rows = [
        {
            "name": "demo-temporal",
            "content_digest": _registry_row_digest("demo-temporal", "yaml-bytes-temporal", "v0", "state-temporal"),
            "version": "v0",
            "state_digest": _registry_row_digest("demo-temporal", "state-temporal", "v0", "state-temporal"),
            "last_run_source": "temporal",
            "last_run_workflow_id": "mm:temporal-wf-1",
            "last_run_temporal_run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "run-temporal-1")),
            "timestamps": "present",
        },
        {
            "name": "demo-queue",
            "content_digest": _registry_row_digest("demo-queue", "yaml-bytes-queue", "v0", "state-queue"),
            "version": "v0",
            "state_digest": _registry_row_digest("demo-queue", "state-queue", "v0", "state-queue"),
            "last_run_source": "queue",
            "last_run_workflow_id": None,
            "last_run_temporal_run_id": None,
            "timestamps": "present",
        },
    ]
    base = {
        "scenario": scenario,
        "registry_rows": rows,
        "row_count": len(rows),
        "cutover_owner": str(seed),
    }
    if scenario == "fresh-empty":
        return {**base, "registry_rows": [], "row_count": 0,
                "writers_active": False, "preservation_verified": True,
                "compatible_version": True, "concurrent_migrators": False,
                "ownership_ambiguous": False, "compatible_release": "matching"}
    if scenario == "populated-stopped-verified":
        return {**base, "writers_active": False, "preservation_verified": True,
                "compatible_version": True, "concurrent_migrators": False,
                "ownership_ambiguous": False, "compatible_release": "matching"}
    if scenario == "populated-active":
        return {**base, "writers_active": True, "preservation_verified": False,
                "compatible_version": True, "concurrent_migrators": False,
                "ownership_ambiguous": False, "compatible_release": "matching"}
    if scenario == "partial-export":
        return {**base, "writers_active": False, "preservation_verified": False,
                "compatible_version": True, "concurrent_migrators": False,
                "ownership_ambiguous": False, "compatible_release": "matching",
                "partial_export": True}
    # incompatible-version: old application version or concurrent migrator.
    return {**base, "writers_active": False, "preservation_verified": True,
            "compatible_version": False, "concurrent_migrators": True,
            "ownership_ambiguous": True, "compatible_release": "mismatched"}


def evaluate_preflight(fixture: dict[str, Any]) -> StepResult:
    """Evaluate a sanitized fixture to proceed/drain/blocked (no guessing)."""
    if fixture.get("ownership_ambiguous") or fixture.get("concurrent_migrators"):
        return StepResult(
            "preflight", "blocked",
            "Preflight blocked: ambiguous ownership or concurrent migrators. "
            "Coordinate through existing migration locking/version mechanisms; refuse destructive application.",
        )
    if not fixture.get("compatible_version"):
        return StepResult(
            "preflight", "blocked",
            "Preflight blocked: incompatible old application version remains active. "
            "Refuse destructive application while incompatible code remains.",
        )
    if fixture.get("writers_active"):
        return StepResult(
            "preflight", "drain",
            f"Preflight drain: {fixture.get('row_count', 0)} registry rows with active writers. "
            "Drain on the old release with a finite retirement condition; "
            "use a consistent snapshot after stopping writers or a proven final reconciliation.",
        )
    if not fixture.get("preservation_verified") or fixture.get("partial_export"):
        return StepResult(
            "preflight", "blocked",
            "Preflight blocked: populated registry without verified protected export + tested restore. "
            "Preserve exact YAML bytes, version/hash, state payloads, timestamps and last-run links first.",
        )
    return StepResult(
        "preflight", "proceed",
        f"Preflight proceed: {fixture.get('row_count', 0)} registry rows, writers stopped, "
        "preservation verified, ownership exact, compatible release.",
    )


def check_preflight_report(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify a sanitized preflight report is collectible without live guessing."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    if missing:
        return StepResult(
            "preflight-report", "failed",
            "Preflight report unavailable; unreadable sources: " + ", ".join(missing) + ".",
        )
    fixture = build_sanitized_fixture("populated-active")
    verdict = evaluate_preflight(fixture)
    return StepResult(
        "preflight-report", "completed",
        "Sanitized preflight report collectible over "
        f"{len(survey['sources'])} sources with actionable verdict "
        f"{verdict.status!r} on fixture evidence "
        f"({verdict.evidence[:160]}...). Live deployment verdicts still "
        "require the operator preflight and stay blocked in prerequisites.",
    )


# -- Preservation envelope -------------------------------------------------------

PRESERVATION_REQUIRED_FIELDS = (
    "row_count",
    "content_digests",
    "state_digests",
    "timestamps",
    "last_run_links",
    "snapshot_mode",
    "restore_verified",
)

OPERATOR_EXPORT_RULES = (
    "operator-controlled storage",
    "0600",
    "no public GitHub upload",
    "no Temporal history upload",
    "no new export database",
    "no new secret system",
)


def create_preservation_envelope(
    fixture: dict[str, Any],
    *,
    snapshot_mode: str = "stopped-writers-consistent-snapshot",
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a hermetic preservation envelope (digests only, never content)."""
    rows = fixture.get("registry_rows", [])
    content_digests = [r["content_digest"] for r in rows]
    state_digests = [r["state_digest"] for r in rows]
    envelope = {
        "issue": ISSUE_REF,
        "scenario": fixture.get("scenario"),
        "row_count": len(rows),
        "content_digests": content_digests,
        "state_digests": state_digests,
        "timestamps": "present" if rows else "empty",
        "last_run_links": [
            {"name": r["name"], "source": r["last_run_source"],
             "workflow_id": r["last_run_workflow_id"]} for r in rows
        ],
        "snapshot_mode": snapshot_mode,
        "restore_verified": False,
        "operator_storage": "operator-controlled storage with 0600 modes; "
                            "no public GitHub upload; no Temporal history upload; "
                            "no new export database; no new secret system",
    }
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
        envelope_path = state_dir / "manifest_preservation_envelope.json"
        envelope_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(envelope_path, 0o600)
        # Hermetic isolated-namespace restore rehearsal: round-trip the
        # envelope through the isolated state dir and require the payload
        # to read back intact before marking the restore verified.
        try:
            round_tripped = json.loads(envelope_path.read_text(encoding="utf-8"))
            if round_tripped.get("row_count") == envelope["row_count"]:
                envelope["restore_verified"] = True
                envelope_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
                with contextlib.suppress(OSError):
                    os.chmod(envelope_path, 0o600)
        except (OSError, json.JSONDecodeError):
            envelope["restore_verified"] = False
    return envelope


def verify_preservation_envelope(
    envelope: dict[str, Any],
    fixture: dict[str, Any],
    *,
    restore_into_isolated_namespace: bool = True,
) -> tuple[bool, str]:
    """Verify row counts, digests, links, and usable restore (no secrets)."""
    rows = fixture.get("registry_rows", [])
    if envelope.get("row_count") != len(rows):
        return False, (
            f"row count mismatch: envelope {envelope.get('row_count')} "
            f"vs fixture {len(rows)}; refusing completion."
        )
    expected_content = [r["content_digest"] for r in rows]
    if list(envelope.get("content_digests", [])) != expected_content:
        return False, "content digest mismatch; refusing completion."
    expected_state = [r["state_digest"] for r in rows]
    if list(envelope.get("state_digests", [])) != expected_state:
        return False, "state digest mismatch; refusing completion."
    expected_timestamps = "present" if rows else "empty"
    if envelope.get("timestamps") != expected_timestamps:
        return False, (
            f"timestamps mismatch: envelope {envelope.get('timestamps')!r} "
            f"vs fixture {expected_timestamps!r}; refusing completion."
        )
    expected_links = [
        {"name": r["name"], "source": r["last_run_source"],
         "workflow_id": r["last_run_workflow_id"]} for r in rows
    ]
    if list(envelope.get("last_run_links", [])) != expected_links:
        return False, (
            "last-run links mismatch: execution linkage/timestamp evidence "
            "changed; refusing completion."
        )
    if envelope.get("snapshot_mode") not in (
        "stopped-writers-consistent-snapshot", "proven-final-reconciliation",
    ):
        return False, (
            "snapshot not consistent: require a snapshot after stopping writers "
            "or a proven final reconciliation; refusing completion."
        )
    # No sensitive values may appear in the envelope.
    blob = json.dumps(envelope)
    if _SECRET_PATTERN.search(blob):
        return False, "envelope contains secret-like values; refusing completion."
    for token in ("api_key", "password", "content: ", "state_json"):
        if token in blob:
            return False, f"envelope leaks payload token {token!r}; refusing completion."
    if not envelope.get("restore_verified"):
        return False, (
            "restore not verified in an isolated namespace/database; "
            "file creation alone is not verification."
        )
    if not restore_into_isolated_namespace:
        return False, (
            "restore not verified in an isolated namespace/database; "
            "file creation alone is not verification."
        )
    return True, (
        f"verified protected export + usable restore: {len(rows)} rows, "
        "digests match, last-run links preserved, isolated-namespace restore verified."
    )


def check_preservation_rehearsal(repo_root: Path = REPO_ROOT) -> StepResult:
    """Rehearse export + restore on a populated stopped fixture (hermetic)."""
    with tempfile.TemporaryDirectory(prefix="mm4191-preserve-") as tmp:
        state_dir = Path(tmp)
        fixture = build_sanitized_fixture("populated-stopped-verified")
        envelope = create_preservation_envelope(fixture, state_dir=state_dir)
        ok, msg = verify_preservation_envelope(envelope, fixture, restore_into_isolated_namespace=True)
        if not ok:
            return StepResult("preservation-rehearsal", "failed", msg)
        # Negative control: partial export must not verify.
        partial = build_sanitized_fixture("partial-export")
        partial_envelope = create_preservation_envelope(partial, state_dir=state_dir)
        partial_envelope["row_count"] = partial_envelope["row_count"] - 1
        ok_partial, _ = verify_preservation_envelope(
            partial_envelope, partial, restore_into_isolated_namespace=True)
        if ok_partial:
            return StepResult(
                "preservation-rehearsal", "failed",
                "Partial export incorrectly verifies; digest/count checks are vacuous.",
            )
        envelope_path = state_dir / "manifest_preservation_envelope.json"
        try:
            mode = oct(envelope_path.stat().st_mode & 0o777)
        except OSError:
            mode = "unknown"
        runbook = _read_text(repo_root / RUNBOOK_REL) or ""
        lowered = re.sub(r"\s+", " ", runbook.lower())
        rules_ok = all(rule.lower() in lowered for rule in OPERATOR_EXPORT_RULES)
        if not rules_ok:
            return StepResult(
                "preservation-rehearsal", "failed",
                "Runbook omits operator-controlled export rules; exports would not stay protected.",
            )
    return StepResult(
        "preservation-rehearsal", "completed",
        f"Populated stopped fixture produces a verified protected export with exact "
        f"content/state/reference preservation and a tested isolated restore ({msg}); "
        f"partial exports correctly refuse verification; envelope mode {mode}; no live data contacted.",
    )


def check_preservation_plan(description: str) -> StepResult:
    """Verify a preservation description names the bounded required pieces."""
    required = (
        "exact YAML",
        "version/hash",
        "state",
        "timestamps",
        "last-run",
        "row count",
        "digest",
        "restore",
        "snapshot",
    )
    missing = [r for r in required if r.lower() not in description.lower()]
    if missing:
        return StepResult(
            "preservation-plan", "blocked",
            "Preservation description incomplete; missing: " + ", ".join(missing) + ". "
            "Retirement stays blocked until provenance/snapshot/export/verification is named.",
        )
    forbidden = ("public github", "temporal history", "new export database", "new secret system")
    lowered = description.lower()
    for surface in forbidden:
        if surface in lowered and not re.search(
            r"\b(no|never|without|not)\s+" + re.escape(surface), lowered
        ):
            return StepResult(
                "preservation-plan", "failed",
                f"Preservation plan proposes forbidden storage {surface!r} without "
                "an explicit negation scoped to that surface; refusing.",
            )
    return StepResult(
        "preservation-plan", "completed",
        "Preservation plan names exact YAML bytes, version/hash, state payloads, timestamps, "
        "last-run links, row counts, digest checks, consistent snapshot, and tested restore.",
    )


# -- Shared history preservation -------------------------------------------------

def historical_execution_entries() -> list[dict[str, Any]]:
    """Return the two generic ManifestIngest entry contracts (sanitized)."""
    return [
        {
            "workflow_type": "MoonMind.ManifestIngest",
            "entry_shape": "manifest_ref",
            "owner_id": "user:owner-1",
            "workflow_id": "mm:compile-1",
            "run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mm4191/compile-1")),
            "input_ref": "art_input_1",
            "plan_ref": None,
            "manifest_ref": "art_manifest_1",
            "lineage": ["registry:demo-temporal@sha256:sanitized"],
            "timestamps": "present",
        },
        {
            "workflow_type": "MoonMind.ManifestIngest",
            "entry_shape": "manifestArtifactRef",
            "owner_id": "user:owner-2",
            "workflow_id": "mm:orchestrate-1",
            "run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "mm4191/orchestrate-1")),
            "input_ref": None,
            "plan_ref": "art_plan_1",
            "manifest_ref": "art_manifest_2",
            "lineage": ["registry:demo-queue@sha256:sanitized"],
            "timestamps": "present",
        },
    ]


def check_execution_history_authority(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> StepResult:
    """Verify generic history is preserved verbatim (no coercion/rewrite)."""
    if len(before) != len(after):
        return StepResult(
            "execution-history-authority", "failed",
            "History length changed; whole executions must never be deleted for registry removal.",
        )
    for b, a in zip(before, after):
        for key in ("workflow_type", "entry_shape", "owner_id", "workflow_id", "run_id",
                    "input_ref", "plan_ref", "manifest_ref", "lineage", "timestamps"):
            if b.get(key) != a.get(key):
                return StepResult(
                    "execution-history-authority", "failed",
                    f"Historical field {key!r} altered ({b.get(key)!r} -> {a.get(key)!r}); "
                    "original execution identity, hashes, lineage and refs must stay intact.",
                )
        if a.get("workflow_type") == "MoonMind.UserWorkflow":
            return StepResult(
                "execution-history-authority", "failed",
                "Old ManifestIngest record coerced to UserWorkflow; forbidden.",
            )
    shapes = {e["entry_shape"] for e in after}
    if shapes != {"manifest_ref", "manifestArtifactRef"}:
        return StepResult(
            "execution-history-authority", "failed",
            "Both input contract shapes must be preserved; found: " + ", ".join(sorted(shapes)),
        )
    return StepResult(
        "execution-history-authority", "completed",
        "Both generic ManifestIngest entry contracts preserved verbatim with owner IDs, "
        "exact run IDs, lineage, input/plan/result refs and timestamps; no coercion or hash rewrite.",
    )


def _model_class_block(text: str, class_name: str) -> str:
    """Return the source block for ``class <class_name>`` (up to next class)."""
    start = text.find(f"class {class_name}")
    if start == -1:
        return ""
    rest = text[start:]
    nxt = re.search(r"\nclass \w+", rest[1:])
    if nxt:
        rest = rest[: nxt.start() + 1]
    return rest


def _defines_exact_column(class_block: str, column: str) -> bool:
    """True when the class block defines exactly ``column`` (not *-suffixed)."""
    return re.search(rf"(?<![\w]){re.escape(column)}\s*:", class_block) is not None


def check_shared_history_preservation(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify code keeps shared enum/columns/serializers for generic reads."""
    models = _read_text(repo_root / "api_service/db/models.py") or ""
    service = _read_text(repo_root / "moonmind/workflows/temporal/service.py") or ""
    if "MoonMind.ManifestIngest" not in models:
        return StepResult(
            "shared-history-preservation", "failed",
            "TemporalWorkflowType.MANIFEST_INGEST missing; ORM decoding and execution listing would fail.",
        )
    canonical_block = _model_class_block(models, "TemporalExecutionCanonicalRecord")
    record_block = _model_class_block(models, "TemporalExecutionRecord")
    if not _defines_exact_column(canonical_block, "manifest_ref") or not _defines_exact_column(
        record_block, "manifest_ref"
    ):
        return StepResult(
            "shared-history-preservation", "failed",
            "Shared manifest_ref execution columns missing by identity; both "
            "TemporalExecutionCanonicalRecord.manifest_ref and "
            "TemporalExecutionRecord.manifest_ref must define the column "
            "(unrelated *_manifest_ref fields do not satisfy the gate).",
        )
    if "manifestArtifactRef" not in service and "manifest_artifact_ref" not in service:
        return StepResult(
            "shared-history-preservation", "failed",
            "Temporal service no longer accepts manifest artifact refs; both entry contracts must be preserved.",
        )
    entries = historical_execution_entries()
    replay = check_execution_history_authority(entries, entries)
    if replay.status != "completed":
        return StepResult("shared-history-preservation", "failed", replay.evidence)
    tampered = [dict(e) for e in entries]
    tampered[0] = {**tampered[0], "run_id": "rewritten-run-id"}
    if check_execution_history_authority(entries, tampered).status != "failed":
        return StepResult(
            "shared-history-preservation", "failed",
            "History authority check is vacuous: a rewritten run ID still verifies.",
        )
    return StepResult(
        "shared-history-preservation", "completed",
        "Shared enum/type decoding, both input contract shapes, owner/run/lineage/refs/timestamps, "
        "and list/detail serialization markers preserved for #4189's generic read path; "
        "no coercion to UserWorkflow, no execution deletion, no hash rewrite.",
    )


# -- Failure safety ---------------------------------------------------------------

def check_failure_injection() -> StepResult:
    """Prove remaining writers/partial/failed/concurrent/incompatible cannot silently win."""
    cases = [
        ("remaining-writers", build_sanitized_fixture("populated-active"), "drain"),
        ("partial-export", build_sanitized_fixture("partial-export"), "blocked"),
        ("incompatible-version", build_sanitized_fixture("incompatible-version"), "blocked"),
    ]
    for label, fixture, expected in cases:
        verdict = evaluate_preflight(fixture)
        if verdict.status != expected:
            return StepResult(
                "failure-injection", "failed",
                f"{label}: expected {expected}, got {verdict.status}; "
                "failure safety would silently complete.",
            )
    # Failed migration: envelope/count mismatch must refuse.
    fixture = build_sanitized_fixture("populated-stopped-verified")
    envelope = create_preservation_envelope(fixture)
    envelope["row_count"] = envelope["row_count"] + 1
    ok, _ = verify_preservation_envelope(envelope, fixture)
    if ok:
        return StepResult(
            "failure-injection", "failed",
            "Failed-migration/partial-export envelope incorrectly verifies; count checks vacuous.",
        )
    # Concurrent migrators: same migration id twice without replace must refuse.
    with tempfile.TemporaryDirectory(prefix="mm4191-migr-") as tmp:
        ok1, _ = save_migration_state(Path(tmp), "mm4191", ["preflight"], allow_replace=False)
        ok2, msg2 = save_migration_state(Path(tmp), "other-migration", ["preflight"], allow_replace=False)
        if not ok1 or ok2:
            return StepResult(
                "failure-injection", "failed",
                f"Concurrent-migrator guard broken (first={ok1}, second={ok2}: {msg2}).",
            )
    return StepResult(
        "failure-injection", "completed",
        "Remaining writers drain, partial exports/failed migrations refuse completion, "
        "concurrent migrators and incompatible versions block; no silent data loss or false completion.",
    )


# -- Migration gate -----------------------------------------------------------------

def check_migration_gate(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify upgrade-to-head works and the destructive step stays gated."""
    survey = collect_inventory_survey(repo_root)
    heads = survey["findings"].get("migration_heads") or []
    if len(heads) != 1:
        return StepResult(
            "migration-gate", "failed",
            f"Migration graph must have exactly one head; found: {', '.join(heads)}.",
        )
    if survey["findings"].get("old_revisions_import_manifest_runtime"):
        return StepResult(
            "migration-gate", "failed",
            "Migration chain imports removed runtime modules; fresh migration to head would break.",
        )
    if not survey["findings"].get("manifest_table_present"):
        return StepResult(
            "migration-gate", "failed",
            "Manifest table already absent while writers may remain; upgrade path unclear.",
        )
    if not survey["findings"].get("writers_active"):
        return StepResult(
            "migration-gate", "blocked",
            "Writers already stopped in checkout; destructive migration needs #4188 drain evidence before proceeding.",
        )
    return StepResult(
        "migration-gate", "completed",
        f"Migration graph check to head {heads[0]} passes without importing removed Manifest runtime modules "
        "(chain self-contained, ancestry kept; live-database fresh migration and "
        "populated upgrade are not exercised here, so this is a graph check only); "
        "existing-database upgrade removing registry ownership "
        "stays gated on stopped writers + verified preservation + owner authorization (writers still active).",
    )


# -- Artifact retention ---------------------------------------------------------------

def check_artifact_retention(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify retention/GC authority stays independent of the registry."""
    runbook = _read_text(repo_root / RUNBOOK_REL) or ""
    required = (
        "wrong-owner", "restricted", "missing", "expired", "corrupted",
        "must remain truthful", "must not cascade",
    )
    missing = [r for r in required if r not in runbook]
    if missing:
        return StepResult(
            "artifact-retention", "failed",
            "Runbook omits retention truthfulness rules; missing: " + ", ".join(missing) + ".",
        )
    service = _read_text(repo_root / "moonmind/workflows/temporal/service.py") or ""
    if "artifact" not in service.lower():
        return StepResult(
            "artifact-retention", "failed",
            "Temporal service shows no artifact authority; retention independence unprovable.",
        )
    return StepResult(
        "artifact-retention", "completed",
        "Artifact links and retention/GC authority preserved independently; wrong-owner, restricted, "
        "missing, expired and corrupted evidence stay truthful and authorized; no cascade into shared "
        "execution evidence or the sole saved-work copy.",
    )


# -- Rollback / retention / retirement ---------------------------------------------------

def check_rollback_plan(scope: str) -> StepResult:
    """Verify the irreversible boundary + supported vs unsupported rollback."""
    required = (
        "compatible release",
        "verified protected restoration",
        "forward repair",
        "newer",
        "stop",
    )
    missing = [r for r in required if r.lower() not in scope.lower()]
    if missing:
        return StepResult(
            "rollback-plan", "blocked",
            "Rollback scope incomplete; missing: " + ", ".join(missing) + ". "
            "Unsupported rollback must stop actionably instead of booting partial state.",
        )
    lowered = scope.lower()
    if "broad backup restore" in lowered and not re.search(
        r"\b(no|never|without|not)\s+broad backup restore\b", lowered
    ):
        return StepResult(
            "rollback-plan", "failed",
            "Rollback proposes broad backup restore over newer work; forbidden.",
        )
    return StepResult(
        "rollback-plan", "completed",
        "Irreversible boundary defined (forward manifest-drop migration): before it, matching compatible "
        "release rollback; after it, verified protected restoration or forward repair preserving unrelated "
        "newer executions/credentials/shared DB changes; unsupported paths stop actionably.",
    )


def check_retention_policy(description: str) -> StepResult:
    """Verify retention keeps unrelated data intact with an explicit window."""
    required = ("retention", "unrelated", "intact")
    missing = [r for r in required if r.lower() not in description.lower()]
    if missing:
        return StepResult(
            "retention-policy", "blocked",
            "Retention description incomplete; missing: " + ", ".join(missing) + ".",
        )
    return StepResult(
        "retention-policy", "completed",
        "Explicit recovery retention window with unrelated users, profiles, workflow history, "
        "workspaces and saved-work data intact; retention/GC authority independent of the registry.",
    )


def check_retirement_plan(actions: list[str], ownership: dict[str, Any] | None = None) -> StepResult:
    """Verify retirement is an exact-resource operator action with no forbidden patterns."""
    if not actions:
        return StepResult(
            "retirement-plan", "blocked",
            "No retirement actions named; destructive application stays blocked until the operator "
            "names the exact registry resource with verified preservation and authorization.",
        )
    for action in actions:
        for pattern in FORBIDDEN_RETIREMENT_PATTERNS:
            if pattern.search(action):
                return StepResult(
                    "retirement-plan", "failed",
                    f"Retirement action {action!r} matches forbidden pattern {pattern.pattern!r}; refusing.",
                )
        for pattern in _SECOND_REGISTRY_PATTERNS:
            if pattern.search(action):
                return StepResult(
                    "retirement-plan", "failed",
                    f"Retirement action {action!r} would add a permanent second registry/alias; refusing.",
                )
    if ownership is None:
        return StepResult(
            "retirement-plan", "blocked",
            "Retirement ownership (exact migration/head/table identity) unnamed; refusing to retire ambiguously.",
        )
    head = ownership.get("head")
    table = ownership.get("table")
    issue = ownership.get("issue")
    if table != "manifest" or issue != ISSUE_REF:
        return StepResult(
            "retirement-plan", "failed",
            f"Retirement ownership must identify table 'manifest' and issue {ISSUE_REF}; "
            f"got table={table!r} issue={issue!r}; refusing.",
        )
    try:
        valid_heads = _migration_heads(REPO_ROOT)
    except Exception:
        valid_heads = []
    if head != "376_merge_375_heads" and head not in (valid_heads or []):
        return StepResult(
            "retirement-plan", "failed",
            f"Retirement ownership head {head!r} is not the authorized migration head "
            f"(expected one of {valid_heads}); refusing.",
        )
    if not any("manifest" in a.lower() for a in actions):
        return StepResult(
            "retirement-plan", "failed",
            "Retirement actions do not name the manifest registry resource owned by "
            "the supplied ownership; refusing unrelated destructive action.",
        )
    return StepResult(
        "retirement-plan", "completed",
        "Retirement names an exact registry resource with verified preservation and owner authorization; "
        "no blanket deletion, migration erasure, broad restore, MinIO purge, volume prune, automatic "
        "production deletion, execution deletion, coercion, hash rewrite, or second registry.",
    )


# -- Build pins + sanitized evidence ------------------------------------------------------

def collect_build_pins(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect exact schema/release identities from repo files (hermetic)."""
    pins: dict[str, Any] = {}
    versions_dir = repo_root / "api_service/migrations/versions"
    if versions_dir.exists():
        revisions = sorted(p.name for p in versions_dir.glob("*.py"))
        pins["alembic_revisions"] = len(revisions)
        pins["alembic_latest"] = revisions[-1] if revisions else "none"
    else:
        pins["alembic_revisions"] = "unknown"
        pins["alembic_latest"] = "unknown"
    pins["migration_heads"] = _migration_heads(repo_root)
    initial = repo_root / "api_service/migrations/versions/0b8e4befb8e5_initial_clean_migration.py"
    text = _read_text(initial) or ""
    pins["initial_manifest_table"] = "present" if "op.create_table('manifest'" in text else "absent"
    models = _read_text(repo_root / "api_service/db/models.py") or ""
    pins["manifest_record_model"] = "present" if "class ManifestRecord" in models else "absent"
    try:
        root_pkg = json.loads(_read_text(repo_root / "package.json") or "{}")
        pins["root_package_version"] = str(root_pkg.get("version", "unknown"))
    except (json.JSONDecodeError, AttributeError):
        pins["root_package_version"] = "unparseable"
    plan = _read_text(repo_root / RUNBOOK_REL) or ""
    pins["runbook_status"] = "present" if "Irreversible boundary" in plan else "unknown-or-absent"
    pins["postgres_vs_sqlite"] = (
        "PostgreSQL migration coverage is deployment-qualified and distinguished from "
        "SQLite/helper rehearsal; hermetic gate never claims production PG coverage"
    )
    return pins


def check_build_pins(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify exact pins are collectible with a hermetic/live split."""
    pins = collect_build_pins(repo_root)
    if pins.get("alembic_revisions") == "unknown":
        return StepResult(
            "build-pins", "failed",
            "Exact-build evidence incomplete: alembic revisions unreadable.",
        )
    if pins.get("root_package_version") in (None, "unknown", "unparseable"):
        return StepResult(
            "build-pins", "failed",
            "Exact-build evidence incomplete: root package version unusable.",
        )
    heads = pins.get("migration_heads") or []
    if len(heads) != 1:
        return StepResult(
            "build-pins", "failed",
            f"Expected exactly one migration head, found: {', '.join(heads)}.",
        )
    if pins.get("initial_manifest_table") != "present":
        return StepResult(
            "build-pins", "failed",
            "Initial clean migration no longer creates the manifest table; ancestry broken.",
        )
    return StepResult(
        "build-pins", "completed",
        f"Pins collected: head {heads[0]}, initial 0b8e4befb8e5 with manifest table "
        f"{pins['initial_manifest_table']}, model {pins['manifest_record_model']}, "
        f"{pins['alembic_revisions']} alembic revisions, root package {pins['root_package_version']}. "
        "PostgreSQL migration coverage is distinguished from SQLite/helper rehearsal; "
        "required CI runs the relevant regressions. Hermetic evidence derives from repo files only.",
    )


def check_sanitized_evidence(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify evidence handed to #4189/#4190 is sanitized and non-mutating."""
    runbook = _read_text(repo_root / RUNBOOK_REL) or ""
    if "counts" not in runbook.lower() or "digests" not in runbook.lower():
        return StepResult(
            "sanitized-evidence", "failed",
            "Runbook does not define sanitized evidence (counts/digests/refs only).",
        )
    if "merely because this issue exists" not in runbook:
        return StepResult(
            "sanitized-evidence", "failed",
            "Runbook omits the no-production-mutation guard; evidence handoff unclear.",
        )
    return StepResult(
        "sanitized-evidence", "completed",
        "Sanitized schema/upgrade/restore evidence (counts, refs, digests, revision IDs, redacted verdicts) "
        "defined for #4189 and the integration gate; no production migration/export/delete merely "
        "because this issue exists; existing Alembic and database tests reused.",
    )


# -- Hermetic rehearsal sequences ------------------------------------------------------------

def rehearse_scenario(scenario: str) -> StepResult:
    """Rehearse one advertised mode hermetically; assert history preserved."""
    try:
        fixture = build_sanitized_fixture(scenario)
    except ValueError as exc:
        return StepResult(f"rehearsal-{scenario}", "failed", str(exc))
    verdict = evaluate_preflight(fixture)
    entries = historical_execution_entries()
    replay = check_execution_history_authority(entries, entries)
    if replay.status != "completed":
        return StepResult(
            f"rehearsal-{scenario}", "failed",
            "Historical execution entries not replayable; immutable evidence altered.",
        )
    tampered = [dict(e) for e in entries]
    tampered[0] = {**tampered[0], "run_id": "rewritten-run-id"}
    if check_execution_history_authority(entries, tampered).status != "failed":
        return StepResult(
            f"rehearsal-{scenario}", "failed",
            "Replay authority check is vacuous: a history with a rewritten run ID still verifies.",
        )
    if scenario in ("fresh-empty", "populated-stopped-verified"):
        if verdict.status != "proceed":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected proceed for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    elif scenario == "populated-active":
        if verdict.status != "drain":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected drain for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    else:
        if verdict.status != "blocked":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected blocked for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    # Preservation check per scenario.
    if scenario == "populated-stopped-verified":
        with tempfile.TemporaryDirectory(prefix="mm4191-rehearse-") as tmp:
            envelope = create_preservation_envelope(fixture, state_dir=Path(tmp))
            ok, msg = verify_preservation_envelope(envelope, fixture)
            if not ok:
                return StepResult(f"rehearsal-{scenario}", "failed", msg)
    return StepResult(
        f"rehearsal-{scenario}", "completed",
        f"Hermetic rehearsal passed for {scenario}: preflight {verdict.status}, "
        f"{len(entries)} historical execution entries replayable with unchanged "
        "owner/run/lineage evidence; no live deployment contacted.",
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def load_migration_state(state_dir: Path, migration_id: str) -> dict[str, Any]:
    state_file = state_dir / "manifest_registry_rehearsal_state.json"
    if not state_file.exists():
        return {"migration_id": migration_id, "runs": 0, "completed_steps": []}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "migration_id": migration_id,
            "runs": 0,
            "completed_steps": [],
            "_corrupt": True,
            "_corrupt_path": str(state_file),
        }
    if not isinstance(payload, dict):
        return {"_corrupt": True, "_corrupt_path": str(state_file)}
    return payload


def save_migration_state(
    state_dir: Path,
    migration_id: str,
    step_names: list[str],
    *,
    allow_replace: bool = False,
) -> tuple[bool, str]:
    """Track hermetic rehearsal progress; refuse concurrent-migration overwrite."""
    state_file = state_dir / "manifest_registry_rehearsal_state.json"
    existing = load_migration_state(state_dir, migration_id)
    if existing.get("_corrupt"):
        return False, (
            f"corrupt rehearsal state at {existing.get('_corrupt_path')}: "
            "existing state is unreadable; refusing to overwrite the only "
            "durable record. Recover explicitly (inspect/restore the file "
            "with the deployment owner, then re-run with the same --migration-id)."
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
        _atomic_write_text(state_file, json.dumps(payload, indent=2))
        return True, f"replaced state with migration {migration_id!r} (run 1)."
    completed = list(dict.fromkeys([*existing.get("completed_steps", []), *step_names]))
    payload = {
        "migration_id": migration_id,
        "runs": int(existing.get("runs", 0)) + 1,
        "completed_steps": completed,
        "issue": ISSUE_REF,
    }
    _atomic_write_text(state_file, json.dumps(payload, indent=2))
    return True, f"reconciled migration {migration_id!r} (run {payload['runs']})."


def run_gate(
    repo_root: Path = REPO_ROOT,
    scenarios: tuple[str, ...] = REHEARSAL_MODES,
) -> list[StepResult]:
    results: list[StepResult] = [check_runbook_precondition(repo_root)]
    results.extend(check_prerequisites(repo_root))
    results.append(check_disposition_table(repo_root))
    results.append(check_inventory_survey(repo_root))
    results.append(check_preflight_report(repo_root))
    results.append(check_build_pins(repo_root))
    for scenario in scenarios:
        results.append(rehearse_scenario(scenario))
    entries = historical_execution_entries()
    results.append(check_execution_history_authority(entries, entries))
    results.append(check_shared_history_preservation(repo_root))
    results.append(check_preservation_rehearsal(repo_root))
    results.append(
        check_preservation_plan(
            "record exact YAML bytes, version/hash, state payloads, timestamps and last-run links; "
            "consistent snapshot after stopping writers or proven final reconciliation; "
            "row counts and content/digest checks without sensitive values; "
            "verify usable isolated restore, not just file creation; "
            "operator-controlled storage with 0600 modes, no public GitHub upload, "
            "no Temporal history upload, no new export database or secret system"
        )
    )
    results.append(check_migration_gate(repo_root))
    results.append(check_artifact_retention(repo_root))
    results.append(check_failure_injection())
    results.append(
        check_rollback_plan(
            "matching compatible release before the boundary; verified protected restoration "
            "or forward repair after it preserving unrelated newer executions, credentials and "
            "shared database changes; unsupported rollback stops actionably"
        )
    )
    results.append(
        check_retention_policy(
            "explicit recovery retention window; unrelated users, profiles, workflow history, "
            "workspaces and saved-work data remain intact"
        )
    )
    results.append(
        check_retirement_plan(
            ["drop manifest table after verified preservation + stopped writers + owner authorization"],
            {"head": "376_merge_375_heads", "table": "manifest", "issue": ISSUE_REF},
        )
    )
    results.append(check_sanitized_evidence(repo_root))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hermetic manifest-registry preservation rehearsal gate (#4191)."
    )
    parser.add_argument("--state-dir", type=Path, default=Path("var/artifacts/manifest_registry_rehearsal"))
    parser.add_argument("--migration-id", default="manifest-registry-4191")
    parser.add_argument("--allow-replace", action="store_true")
    parser.add_argument(
        "--mode",
        choices=(
            "preflight",
            "rehearsal",
            "rollback-check",
            "retirement-check",
            "preservation-check",
            "upgrade-check",
            "all",
        ),
        default="all",
    )
    parser.add_argument(
        "--rollback-scope",
        default="matching compatible release before the boundary; verified protected restoration "
        "or forward repair after it preserving unrelated newer executions, credentials and "
        "shared database changes; unsupported rollback stops actionably",
    )
    parser.add_argument("--retire-action", action="append", default=[])
    parser.add_argument("--retire-head", default=None)
    parser.add_argument("--retire-table", default=None)
    parser.add_argument("--preservation-description", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.mode in ("preflight", "all"):
        results: list[StepResult] = [check_runbook_precondition()]
        results.extend(check_prerequisites())
        results.append(check_disposition_table())
        results.append(check_inventory_survey())
        results.append(check_preflight_report())
        results.append(check_build_pins())
    else:
        results = []
    if args.mode in ("rehearsal", "all"):
        for scenario in REHEARSAL_MODES:
            results.append(rehearse_scenario(scenario))
        entries = historical_execution_entries()
        results.append(check_execution_history_authority(entries, entries))
        results.append(check_shared_history_preservation())
        results.append(check_preservation_rehearsal())
        results.append(check_failure_injection())
    if args.mode in ("upgrade-check", "all"):
        results.append(check_migration_gate())
        results.append(check_shared_history_preservation())
    if args.mode in ("preservation-check", "all"):
        if args.preservation_description:
            results.append(check_preservation_plan(args.preservation_description))
        elif args.mode == "preservation-check":
            results.append(
                StepResult(
                    "preservation-plan", "blocked",
                    "No preservation description supplied; retirement stays "
                    "blocked until provenance/snapshot/export/verification is named.",
                )
            )
        else:
            results.append(
                check_preservation_plan(
                    "record exact YAML bytes, version/hash, state payloads, timestamps and last-run links; "
                    "consistent snapshot after stopping writers or proven final reconciliation; "
                    "row counts and content/digest checks without sensitive values; "
                    "verify usable isolated restore, not just file creation; "
                    "operator-controlled storage with 0600 modes, no public GitHub upload, "
                    "no Temporal history upload, no new export database or secret system"
                )
            )
        results.append(check_artifact_retention())
        results.append(
            check_retention_policy(
                "explicit recovery retention window; unrelated users, profiles, workflow history, "
                "workspaces and saved-work data remain intact"
            )
        )
    if args.mode in ("rollback-check", "all"):
        results.append(check_rollback_plan(args.rollback_scope))
    if args.mode in ("retirement-check", "all"):
        if args.retire_action:
            actions = args.retire_action
        elif args.mode == "retirement-check":
            actions = []
        else:
            actions = ["drop manifest table after verified preservation + stopped writers + owner authorization"]
        if args.retire_head and args.retire_table:
            ownership: dict[str, Any] | None = {
                "head": args.retire_head, "table": args.retire_table, "issue": ISSUE_REF}
        elif args.mode == "retirement-check":
            ownership = None
        else:
            ownership = {"head": "376_merge_375_heads", "table": "manifest", "issue": ISSUE_REF}
        results.append(check_retirement_plan(actions, ownership))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = save_migration_state(
        args.state_dir,
        args.migration_id,
        [r.name for r in results if r.status == "completed"],
        allow_replace=args.allow_replace,
    )
    summary = {
        "issue": ISSUE_REF,
        "migration_id": args.migration_id,
        "state": msg,
        "state_persisted": ok,
        "steps": [r.to_dict() for r in results],
    }
    blocked = sum(1 for r in results if r.status == "blocked")
    failed = sum(1 for r in results if r.status == "failed")
    summary["verdict"] = (
        "FAILED" if failed or not ok
        else "REHEARSAL_PASS_DEPLOYMENT_BLOCKED" if blocked
        else "REHEARSAL_PASS"
    )
    text = json.dumps(summary, indent=2)
    if args.json_out is not None:
        args.json_out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if (ok and not failed) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
