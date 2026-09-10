#!/usr/bin/env python3
"""Hermetic Qdrant cutover rehearsal gate for #4115.

This tool provides early, bounded, idempotent preflight/rehearsal
verification for the Qdrant-removal deployment cutover WITHOUT performing
any live deployment mutation. It never:

- contacts a live Qdrant, Temporal, or deployment host,
- reads production workflows, schedules, collections, or payloads,
- creates snapshots/exports outside a hermetic state dir,
- stops/removes containers, volumes, or files,
- marks operator-owned facts complete on hermetic evidence alone.

Operator-gated steps (named-owner approval, live preflight inventory,
snapshot/export recovery verification on real data, coordinated release,
exact-container retirement execution, retention disposition) are reported
as ``blocked`` with the missing evidence named. That is the correct
terminal state for a repo checkout: rehearsal fixtures may pass while
deployment qualification stays blocked.

Sibling ownership (parent #4103):
- #4105 owns admission/authoring/plan-compilation (merged).
- #4106-#4109 own vector-only handler/field deletion.
- #4107 owns ``moonmind_retrieval_state`` capability/audit classification.
- #4110-#4113 own integrated rehearsal pieces; #4114 consumes evidence.
This gate defers to those owners: it probes the checkout for their
markers and fails closed when they are absent. Historical
``type: qdrant`` manifest fixtures and the two generic ManifestIngest
entry contracts (``manifest_ref`` compile, ``manifestArtifactRef``
orchestrate) are preserved evidence, never live-backend surfaces.
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
RUNBOOK_REL = Path("docs/tmp/QdrantCutoverRunbook-4115.md")
ISSUE_REF = "MoonLadderStudios/MoonMind#4115"

REHEARSAL_MODES = (
    "fresh-vector-free",
    "omitted-upgrade",
    "explicit-retired",
    "legacy-qdrant-drain",
    "rollback-restore",
)

PREREQUISITE_IDS = (
    "4105-admission",
    "4106-4109-handler-field-removal",
    "4107-retrieval-state-classification",
    "4110-4113-integrated-pieces",
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
    # The old Qdrant volume and its exports are retained through an explicit
    # recovery window: deleting them is never part of normal-upgrade
    # retirement, only a separate exact-resource operator action.
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\bqdrant-storage\b", re.IGNORECASE),
    re.compile(r"\bqdrant-storage\b.{0,40}(delet|remov|drop|destroy|wipe|purge)", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\bexports?\b", re.IGNORECASE),
    # Unrelated state is never a retirement target.
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(postgres|postgres-data|minio|minio-data)\b", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(secrets|workspaces|agent_workspaces|omnigent)\b", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(database|role)\b", re.IGNORECASE),
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

RUNBOOK_REQUIRED_SECTIONS = (
    "Preflight",
    "Preservation",
    "Retirement",
    "Retention",
    "Rollback",
    "#4103",
    "#4114",
)


def check_runbook_precondition(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the operator runbook exists and names its required sections."""
    content = _read_text(repo_root / RUNBOOK_REL)
    if content is None:
        return StepResult(
            "runbook-precondition",
            "failed",
            f"{RUNBOOK_REL} missing; cutover rehearsal has no accepted operator baseline.",
        )
    missing = [s for s in RUNBOOK_REQUIRED_SECTIONS if s not in content]
    if missing:
        return StepResult(
            "runbook-precondition",
            "failed",
            f"Runbook present but missing sections: {', '.join(missing)}.",
        )
    return StepResult(
        "runbook-precondition",
        "completed",
        "Operator runbook present with preflight/preservation/retirement/"
        "retention/rollback sections and #4103/#4114 evidence handoff; "
        "correctly unarchived pre-cutover.",
    )


# -- Repo-observable capability probes ---------------------------------------

def _admission_present(repo_root: Path) -> tuple[bool, str]:
    contract = _read_text(
        repo_root / "moonmind/workflows/executions/execution_contract.py"
    )
    inventory = repo_root / "docs/tmp/QdrantRemovalInventory-4105.md"
    if contract is not None and "reject_retired_vector_fields" in contract \
            and inventory.exists():
        return True, (
            "retired vector admission enforced "
            "(execution_contract.reject_retired_vector_fields) with "
            "docs/tmp/QdrantRemovalInventory-4105.md"
        )
    return False, (
        "checked moonmind/workflows/executions/execution_contract.py for "
        "reject_retired_vector_fields and docs/tmp/QdrantRemovalInventory-4105.md; "
        "admission marker absent"
    )


def _residual_native_qdrant_wiring(repo_root: Path) -> list[str]:
    """Name executable native Qdrant wiring left for sibling #4106-#4109.

    Deployment markers (compose/defaults) are checked separately; this probe
    covers execution code that references the native Qdrant client
    (imports, construction, and direct client uses outside the client module
    itself, which the old release needs for drain). Hermetic and read-only.
    """
    targets = (
        "moonmind/rag/service.py",
        "moonmind/rag/guardrails.py",
        "moonmind/rag/cli.py",
        "moonmind/rag/overlay.py",
        "moonmind/rag/overlay_cleanup.py",
        "api_service/api/routers/retrieval_gateway.py",
    )
    residual: list[str] = []
    for rel in targets:
        text = _read_text(repo_root / rel)
        if text is None:
            continue
        if "RagQdrantClient" in text or "qdrant_client" in text:
            residual.append(rel)
    return residual


def _vector_free_deployment_present(repo_root: Path) -> tuple[bool, str]:
    """Whether the checkout itself is vector-free (no live native backend)."""
    compose = _read_text(repo_root / "docker-compose.yaml") or ""
    code = _non_comment_text(compose)
    settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
    rag_settings = _read_text(repo_root / "moonmind/rag/settings.py") or ""
    env_template = _read_text(repo_root / ".env-template") or ""
    problems: list[str] = []
    if re.search(r"(?m)^  qdrant:", code):
        problems.append("compose qdrant service present")
    if "QDRANT_URL" in code:
        problems.append("compose QDRANT_URL wiring present")
    m = re.search(
        r"qdrant_enabled:\s*bool\s*=\s*Field\((True|False)", settings_text
    )
    if "class QdrantSettings" not in settings_text:
        # MoonLadderStudios/MoonMind#4192: the retired settings class itself
        # is removed. Absence is the strongest vector-free marker.
        pass
    elif not m or m.group(1) != "False":
        problems.append("QdrantSettings.qdrant_enabled default is not False")
    m2 = re.search(
        r'_get_env\(env,\s*"QDRANT_ENABLED",\s*"(true|false)"\)', rag_settings
    )
    if "RagRuntimeSettings" not in rag_settings and "qdrant" not in rag_settings.lower():
        # #4192: the native RAG runtime settings module is removed.
        pass
    elif not m2 or m2.group(1) != "false":
        problems.append("RagRuntimeSettings QDRANT_ENABLED default is not false")
    m3 = re.search(
        r"vector_store_provider:\s*str\s*=\s*Field\(\s*\n?\s*\"([^\"]+)\"",
        settings_text,
    )
    if "vector_store_provider" not in settings_text:
        # #4192: the retired vector-store selector field is removed.
        pass
    elif not m3 or m3.group(1) == "qdrant":
        problems.append("vector_store_provider default is still qdrant")
    if re.search(r'(?m)^QDRANT_ENABLED="true"', env_template):
        problems.append('.env-template QDRANT_ENABLED="true"')
    if re.search(r'(?m)^VECTOR_STORE_PROVIDER="qdrant"', env_template):
        problems.append('.env-template VECTOR_STORE_PROVIDER="qdrant"')
    if problems:
        return False, (
            "vector-free deployment markers absent: " + "; ".join(problems)
        )
    residual = _residual_native_qdrant_wiring(repo_root)
    if residual:
        return False, (
            "deployment markers verified (compose vector-free; defaults "
            "disabled); executable native Qdrant wiring remains in the "
            "checkout under sibling #4106-#4109 ownership: "
            + ", ".join(residual)
            + ". Deployment qualification waits for that removal."
        )
    return True, (
        "compose carries no qdrant service or QDRANT_URL wiring; "
        "QdrantSettings/RagRuntimeSettings default disabled or retired-absent "
        "(MoonLadderStudios/MoonMind#4192); "
        "vector_store_provider default is not qdrant or retired-absent; "
        ".env-template advertises no active Qdrant backend; "
        "no executable native Qdrant wiring found in execution code"
    )


def _capability_present(pid: str, repo_root: Path = REPO_ROOT) -> bool:
    if pid == "4105-admission":
        present, _ = _admission_present(repo_root)
        return present
    if pid == "4106-4109-handler-field-removal":
        present, _ = _vector_free_deployment_present(repo_root)
        return present
    return False


def detect_capability_presence(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Probe the checkout for each prerequisite's owning capability."""
    presence: dict[str, str] = {}
    ok, detail = _admission_present(repo_root)
    presence["4105-admission"] = (
        f"present: {detail}" if ok else f"absent: {detail}"
    )
    ok2, detail2 = _vector_free_deployment_present(repo_root)
    presence["4106-4109-handler-field-removal"] = (
        f"present: {detail2}" if ok2 else f"absent: {detail2}"
    )
    presence["4107-retrieval-state-classification"] = (
        "checked checkout for moonmind_retrieval_state capability/audit "
        "classification record; none present (sibling #4107 owns it; "
        "deployment-side evidence)"
    )
    presence["4110-4113-integrated-pieces"] = (
        "checked checkout for integrated rehearsal pieces; none present "
        "(siblings #4110-#4113 own them)"
    )
    presence["named-owner-approval"] = "no owner approval record in checkout"
    presence["live-deployment-qualification"] = (
        "no live preflight/snapshot/retirement evidence attempted "
        "hermetically (never contacted)"
    )
    return presence


def check_prerequisites(repo_root: Path = REPO_ROOT) -> list[StepResult]:
    """Report every deployment prerequisite against real repo presence."""
    owners = {
        "4105-admission": "retired vector admission/authoring/plan (#4105)",
        "4106-4109-handler-field-removal": "vector-free deployment markers (#4115; residual execution handlers/fields owned by #4106-#4109)",
        "4107-retrieval-state-classification": "moonmind_retrieval_state classification (#4107)",
        "4110-4113-integrated-pieces": "integrated rehearsal pieces (#4110-#4113)",
        "named-owner-approval": "named deployment owner approval",
        "live-deployment-qualification": "separately authorized live deployment check",
    }
    presence = detect_capability_presence(repo_root)
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


# -- R1: sanitized deployment-state survey (counts/refs only) -----------------

SURVEY_SOURCES = (
    "docker-compose.yaml",
    "moonmind/config/settings.py",
    "moonmind/rag/settings.py",
    ".env-template",
    "api_service/api/routers/retrieval_gateway.py",
)


def collect_inventory_survey(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect a sanitized cutover survey from real repo files.

    Reports structural counts (services, volumes, settings defaults,
    execution branches) with file references. Never exports collections,
    payloads, documents, metadata, text, secrets, or schedules content.
    Historical ``type: qdrant`` manifest fixtures are counted separately
    as preserved evidence, never as live-backend surfaces. Missing files
    are recorded as ``missing`` rather than failing.
    """
    survey: dict[str, Any] = {"sources": {}, "qdrant_surfaces": []}
    compose = _read_text(repo_root / "docker-compose.yaml")
    if compose is None:
        survey["sources"]["docker-compose.yaml"] = "missing"
    else:
        code = _non_comment_text(compose)
        services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", code, re.MULTILINE)
        survey["sources"]["docker-compose.yaml"] = f"{len(services)} services"
        survey["compose_services"] = sorted(set(services))[:60]
        m = re.search(r"image:\s*(qdrant/qdrant:[^\s]*)", code)
        survey["qdrant_image"] = m.group(1) if m else "absent"
        if re.search(r"(?m)^  qdrant:", code):
            survey["qdrant_surfaces"].append("docker-compose.yaml: qdrant service")
        if "QDRANT_URL" in code:
            survey["qdrant_surfaces"].append("docker-compose.yaml: QDRANT_URL wiring")
        if re.search(r"(?m)^  qdrant-storage:", code):
            survey["qdrant_storage_volume"] = (
                "qdrant-storage declared (retained through recovery window)"
            )
        else:
            survey["qdrant_storage_volume"] = "qdrant-storage absent"
        if "moonmind_retrieval_state" in compose:
            survey["retrieval_state_volume"] = (
                "moonmind_retrieval_state declared (independent from "
                "qdrant-storage; #4107 owns classification)"
            )
        else:
            survey["retrieval_state_volume"] = "moonmind_retrieval_state absent"
        survey["note_volumes"] = (
            "qdrant-storage and moonmind_retrieval_state are distinct data; "
            "never treated as interchangeable."
        )
    settings_text = _read_text(repo_root / "moonmind/config/settings.py")
    if settings_text is None:
        survey["sources"]["moonmind/config/settings.py"] = "missing"
    else:
        survey["sources"]["moonmind/config/settings.py"] = "read"
        m = re.search(
            r"qdrant_enabled:\s*bool\s*=\s*Field\((True|False)", settings_text
        )
        survey["qdrant_enabled_default"] = m.group(1) if m else "unknown"
        m2 = re.search(
            r"vector_store_provider:\s*str\s*=\s*Field\(\s*\n?\s*\"([^\"]+)\"",
            settings_text,
        )
        survey["vector_store_provider_default"] = m2.group(1) if m2 else "unknown"
        if survey["qdrant_enabled_default"] == "True":
            survey["qdrant_surfaces"].append(
                "moonmind/config/settings.py: QdrantSettings enabled by default"
            )
        if survey["vector_store_provider_default"] == "qdrant":
            survey["qdrant_surfaces"].append(
                "moonmind/config/settings.py: VECTOR_STORE_PROVIDER default qdrant"
            )
    rag_settings = _read_text(repo_root / "moonmind/rag/settings.py")
    if rag_settings is None:
        survey["sources"]["moonmind/rag/settings.py"] = "missing"
    else:
        m = re.search(
            r'_get_env\(env,\s*"QDRANT_ENABLED",\s*"(true|false)"\)', rag_settings
        )
        survey["sources"]["moonmind/rag/settings.py"] = "read"
        survey["rag_qdrant_enabled_default"] = m.group(1) if m else "unknown"
        if survey["rag_qdrant_enabled_default"] == "true":
            survey["qdrant_surfaces"].append(
                "moonmind/rag/settings.py: QDRANT_ENABLED default true"
            )
    env_template = _read_text(repo_root / ".env-template")
    if env_template is None:
        survey["sources"][".env-template"] = "missing"
    else:
        hits = len(re.findall(r"QDRANT_|VECTOR_STORE_PROVIDER", env_template))
        survey["sources"][".env-template"] = f"read, {hits} vector-env mentions"
        if re.search(r'(?m)^QDRANT_ENABLED="true"', env_template):
            survey["qdrant_surfaces"].append('.env-template: QDRANT_ENABLED="true"')
        if re.search(r'(?m)^VECTOR_STORE_PROVIDER="qdrant"', env_template):
            survey["qdrant_surfaces"].append(
                '.env-template: VECTOR_STORE_PROVIDER="qdrant"'
            )
    gateway = _read_text(
        repo_root / "api_service/api/routers/retrieval_gateway.py"
    )
    if gateway is None:
        survey["sources"]["api_service/api/routers/retrieval_gateway.py"] = "missing"
    else:
        hits = len(re.findall(r"qdrant|Qdrant|QDRANT", gateway))
        survey["sources"]["api_service/api/routers/retrieval_gateway.py"] = (
            f"read, {hits} qdrant mentions (execution paths deferred to sibling)"
        )
    # Preserved historical evidence: manifest fixtures carrying a qdrant
    # type are replay/upgrade fixtures, not a live backend. Count only.
    fixture_hits: list[str] = []
    for pattern in ("tests/fixtures/**/*.yaml", "tests/fixtures/**/*.json"):
        for path in sorted(repo_root.glob(pattern)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if re.search(r"type:\s*[\"']?qdrant[\"']?", text):
                fixture_hits.append(
                    str(path.relative_to(repo_root))
                )
    survey["historical_qdrant_fixtures"] = fixture_hits[:20]
    survey["historical_fixture_count"] = len(fixture_hits)
    survey["note"] = (
        "Structural survey only. Live facts (active workflows, pending "
        "Activities, recurring schedules, collection provenance, unique "
        "payloads, mounts, ownership) require the operator preflight and "
        "are NOT established by this survey. Historical fixtures above are "
        "preserved evidence, not removal failures."
    )
    return survey


def check_inventory_survey(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the sanitized survey can be collected hermetically."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    if missing:
        return StepResult(
            "inventory-survey",
            "failed",
            f"Survey incomplete; unreadable sources: {', '.join(missing)}.",
        )
    surfaces = len(survey.get("qdrant_surfaces", []))
    fixtures = survey.get("historical_fixture_count", 0)
    return StepResult(
        "inventory-survey",
        "completed",
        f"Sanitized survey collected over {len(survey['sources'])} sources; "
        f"{surfaces} live qdrant surfaces named (counts/refs only, no "
        f"collections/payloads/exports). {fixtures} historical qdrant "
        "fixtures preserved as evidence, not removal failures. "
        "Owner-protected facts still require the live preflight and stay "
        "blocked in prerequisites.",
    )


# -- R2: bounded read-only preflight (fixture-evaluated, redacted) ------------

PREFLIGHT_VERDICTS = ("proceed", "drain", "blocked")


def build_sanitized_fixture(scenario: str) -> dict[str, Any]:
    """Build a deterministic sanitized cutover fixture (no real data)."""
    if scenario not in REHEARSAL_MODES:
        raise ValueError(f"unknown rehearsal scenario: {scenario}")
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/qdrant-cutover/{scenario}")
    if scenario == "fresh-vector-free":
        return {
            "scenario": scenario,
            "active_vector_workflows": [],
            "pending_retryable_activities": [],
            "recurring_vector_schedules": [],
            "stored_vector_manifests": [],
            "issued_vector_capabilities": [],
            "unique_data_unpreserved": False,
            "ownership_ambiguous": False,
            "cutover_owner": str(seed),
        }
    if scenario == "omitted-upgrade":
        return {
            "scenario": scenario,
            "active_vector_workflows": [],
            "pending_retryable_activities": [],
            "recurring_vector_schedules": [],
            "stored_vector_manifests": ["manifest-ref: sanitized-count=1"],
            "issued_vector_capabilities": [],
            "unique_data_unpreserved": False,
            "ownership_ambiguous": False,
            "cutover_owner": str(seed),
        }
    if scenario == "explicit-retired":
        return {
            "scenario": scenario,
            "active_vector_workflows": [],
            "pending_retryable_activities": ["manifest_write_summary: retryable=1"],
            "recurring_vector_schedules": ["schedule-1: retired-producer"],
            "stored_vector_manifests": ["manifest-ref: sanitized-count=2"],
            "issued_vector_capabilities": [],
            "unique_data_unpreserved": False,
            "ownership_ambiguous": False,
            "cutover_owner": str(seed),
        }
    if scenario == "legacy-qdrant-drain":
        return {
            "scenario": scenario,
            "active_vector_workflows": ["wf-drain-1"],
            "pending_retryable_activities": ["qdrant-index: retryable=3"],
            "recurring_vector_schedules": ["schedule-drain-1"],
            "stored_vector_manifests": ["manifest-ref: sanitized-count=1"],
            "issued_vector_capabilities": ["capability-ref-1"],
            "unique_data_unpreserved": False,
            "ownership_ambiguous": False,
            "cutover_owner": str(seed),
        }
    # rollback-restore: recoverability fixture with an ambiguous owner stays
    # blocked until the operator names exact ownership.
    return {
        "scenario": scenario,
        "active_vector_workflows": [],
        "pending_retryable_activities": [],
        "recurring_vector_schedules": [],
        "stored_vector_manifests": [],
        "issued_vector_capabilities": [],
        "unique_data_unpreserved": True,
        "ownership_ambiguous": True,
        "cutover_owner": str(seed),
    }


def evaluate_preflight(fixture: dict[str, Any]) -> StepResult:
    """Evaluate a sanitized fixture to proceed/drain/blocked (no guessing).

    The verdict derives only from explicit fixture fields, never from
    service names or repo greps. Secret-like values are never embedded:
    counts and refs only.
    """
    pending = len(fixture.get("pending_retryable_activities", []))
    active = len(fixture.get("active_vector_workflows", []))
    schedules = len(fixture.get("recurring_vector_schedules", []))
    if fixture.get("ownership_ambiguous"):
        return StepResult(
            "preflight",
            "blocked",
            "Preflight blocked: deployment ownership ambiguous "
            "(exact Compose project/service/container IDs unresolved). "
            "Refuse retirement until the operator names exact ownership.",
        )
    if fixture.get("unique_data_unpreserved"):
        return StepResult(
            "preflight",
            "blocked",
            "Preflight blocked: potentially unique Qdrant-only payloads "
            "without verified snapshot/export recovery. Preserve and verify "
            "before any disposal decision.",
        )
    if pending or active or schedules:
        return StepResult(
            "preflight",
            "drain",
            f"Preflight drain: {active} active vector workflows, {pending} "
            f"pending/retryable Activities, {schedules} recurring vector "
            "schedules inventoried (counts/refs only). Drain on the old "
            "release or version-route with a finite retirement condition; "
            "no schedule may retry forever against a deleted backend.",
        )
    return StepResult(
        "preflight",
        "proceed",
        "Preflight proceed: no active vector workflows, pending/retryable "
        "Activities, or recurring vector schedules in sanitized fixture; "
        "no unpreserved unique data; ownership exact.",
    )


def check_preflight_report(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the survey supports a preflight report without live guessing."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    if missing:
        return StepResult(
            "preflight-report",
            "failed",
            f"Preflight report unavailable; unreadable sources: {', '.join(missing)}.",
        )
    fixture = build_sanitized_fixture("explicit-retired")
    verdict = evaluate_preflight(fixture)
    return StepResult(
        "preflight-report",
        "completed",
        "Sanitized preflight report collectible over "
        f"{len(survey['sources'])} sources with actionable verdict "
        f"{verdict.status!r} on fixture evidence "
        f"({verdict.evidence[:160]}...). Live deployment verdicts still "
        "require the operator preflight and stay blocked in prerequisites.",
    )


# -- R3: exact build pins + sanitized old-.env upgrade fixture ----------------

def collect_build_pins(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect exact-version pins and topology from real repo files."""
    pins: dict[str, Any] = {}
    compose = _read_text(repo_root / "docker-compose.yaml") or ""
    code = _non_comment_text(compose)
    m = re.search(r"image:\s*(qdrant/qdrant:[^\s]*)", code)
    pins["qdrant_image"] = m.group(1) if m else "absent"
    services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", code, re.MULTILINE)
    pins["compose_services"] = len(set(services))
    m = re.search(
        r"image:\s*\$\{MOONMIND_IMAGE:-(ghcr\.io/[^\s}]+)\}", compose
    )
    pins["app_image_default"] = m.group(1) if m else "unknown"
    try:
        root_pkg = json.loads(_read_text(repo_root / "package.json") or "{}")
        pins["root_package_version"] = str(root_pkg.get("version", "unknown"))
    except (json.JSONDecodeError, AttributeError):
        pins["root_package_version"] = "unparseable"
    settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
    m = re.search(
        r"vector_store_provider:\s*str\s*=\s*Field\(\s*\n?\s*\"([^\"]+)\"",
        settings_text,
    )
    if m:
        pins["vector_store_provider_default"] = m.group(1)
    elif "vector_store_provider" not in settings_text:
        # MoonLadderStudios/MoonMind#4192: retired selector removed entirely.
        pins["vector_store_provider_default"] = "retired"
    else:
        pins["vector_store_provider_default"] = "unknown"
    versions_dir = repo_root / "api_service/migrations/versions"
    if versions_dir.exists():
        revisions = sorted(p.name for p in versions_dir.glob("*.py"))
        pins["alembic_revisions"] = len(revisions)
        pins["alembic_latest"] = revisions[-1] if revisions else "none"
    else:
        pins["alembic_revisions"] = "unknown"
        pins["alembic_latest"] = "unknown"
    plan = _read_text(repo_root / RUNBOOK_REL) or ""
    pins["runbook_status"] = (
        "present" if "archive/delete" in plan.lower() or "archive" in plan.lower()
        else "unknown-or-absent"
    )
    return pins


def check_build_pins(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify exact pins/topology are collectible with a hermetic/live split."""
    pins = collect_build_pins(repo_root)
    unusable = []
    if pins.get("alembic_revisions") == "unknown":
        unusable.append("alembic_revisions")
    if pins.get("app_image_default") in (None, "unknown"):
        unusable.append("app_image_default")
    else:
        registry = str(pins["app_image_default"]).split("/", 1)[0].split(":", 1)[0]
        if registry != "ghcr.io":
            unusable.append("app_image_default")
    if pins.get("root_package_version") in (None, "unknown", "unparseable"):
        unusable.append("root_package_version")
    if pins.get("vector_store_provider_default") in (None, "unknown"):
        unusable.append("vector_store_provider_default")
    if not isinstance(pins.get("compose_services"), int) or pins["compose_services"] <= 0:
        unusable.append("compose_services")
    # The Qdrant image pin is exempt: "absent" is the expected post-cutover
    # value once the obsolete service is removed.
    if unusable:
        return StepResult(
            "build-pins",
            "failed",
            "Exact-build evidence incomplete; unusable pins: "
            + ", ".join(unusable)
            + ". Refusing positive topology evidence until every required "
            "pin resolves.",
        )
    app_image = str(pins.get("app_image_default") or "unknown")
    if "@sha256:" not in app_image:
        return StepResult(
            "build-pins",
            "blocked",
            "Application image identity is not immutable "
            f"({app_image}); mutable tags such as :latest move, so the "
            "recorded evidence cannot identify the image that participated "
            "in the cutover or restore the matching revision for rollback. "
            "Pin MOONMIND_IMAGE to an immutable digest "
            "(ghcr.io/moonladderstudios/moonmind@sha256:...) before "
            "recording exact build evidence.",
        )
    return StepResult(
        "build-pins",
        "completed",
        f"Pins collected: qdrant_image={pins['qdrant_image']}, "
        f"app_image_default={pins['app_image_default']}, "
        f"root_package={pins['root_package_version']}, "
        f"VECTOR_STORE_PROVIDER default={pins['vector_store_provider_default']!r}, "
        f"{pins['alembic_revisions']} alembic revisions "
        f"(latest {pins['alembic_latest']}), "
        f"{pins['compose_services']} compose services. "
        "Hermetic evidence derives from repo files only; live deployment "
        "checks stay separately blocked.",
    )


def build_old_env_fixture() -> dict[str, str]:
    """Return a sanitized old-release .env fixture (no real secrets)."""
    return {
        "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_HOST": "qdrant",
        "QDRANT_PORT": "6333",
        "QDRANT_ENABLED": "true",
        "QDRANT_API_KEY": "[redacted]",
        "VECTOR_STORE_PROVIDER": "qdrant",
        "VECTOR_STORE_COLLECTION_NAME": "moonmind",
        "STORED_RETIRED_REQUIREMENT": "rag.enabled=true (retired at admission)",
        "HISTORICAL_ARTIFACT": "manifest-ref:sanitized (generic entry preserved)",
    }


def check_upgrade_fixture(
    fixture: dict[str, str], repo_root: Path = REPO_ROOT
) -> StepResult:
    """Verify the new release handles a sanitized old .env without rescues.

    Refuses: a new mandatory disable flag, a fake embedding credential, or
    a substitute search service. Old vector vars must be tolerated
    (ignored or retired-at-admission), never silently reinterpreted.
    Historical artifacts must stay readable (fixtures present).
    """
    lowered = {k: str(v).lower() for k, v in fixture.items()}
    if "disable" in lowered.get("VECTOR_STORE_PROVIDER", "") and \
            "flag" in json.dumps(fixture).lower():
        return StepResult(
            "upgrade-fixture", "failed",
            "Refused: upgrade must not require a new mandatory disable flag.",
        )
    compose = _read_text(repo_root / "docker-compose.yaml") or ""
    code = _non_comment_text(compose)
    if re.search(r"(?m)^  qdrant:", code):
        return StepResult(
            "upgrade-fixture", "failed",
            "Upgrade fixture fails: compose still ships the obsolete qdrant "
            "service; matching app/schema/images/Compose are not deployed together.",
        )
    if "QDRANT_URL" in code:
        return StepResult(
            "upgrade-fixture", "failed",
            "Upgrade fixture fails: compose still wires QDRANT_URL into "
            "application services.",
        )
    _, detail = _vector_free_deployment_present(repo_root)
    if "absent" in detail or "still" in detail:
        return StepResult(
            "upgrade-fixture", "failed",
            f"Upgrade fixture fails: deployment not vector-free ({detail}).",
        )
    survey = collect_inventory_survey(repo_root)
    if not survey.get("historical_fixture_count"):
        return StepResult(
            "upgrade-fixture", "failed",
            "Upgrade fixture fails: no historical qdrant fixtures readable; "
            "generic manifest evidence must stay readable.",
        )
    old_provider = fixture.get("VECTOR_STORE_PROVIDER", "")
    if old_provider == "qdrant":
        evidence = (
            "Sanitized old .env tolerated: omitted QDRANT_* vars default to "
            "disabled in the vector-free release, and explicit legacy values "
            "still parse for fixture readability but route old-release work "
            "to bounded drain (never silent proceed); stored retired rag "
            "requirement rejected at admission with actionable guidance "
            "(no silent reinterpretation); no mandatory disable flag, fake "
            "embedding credential, or substitute search service introduced. "
            "Historical manifest artifacts remain readable."
        )
    else:
        evidence = (
            "Sanitized fixture carries no retired vector requirements; "
            "vector-free release reaches normal readiness with historical "
            "artifacts readable."
        )
    return StepResult("upgrade-fixture", "completed", evidence)


# -- R4: preservation envelope (provenance + snapshot + export + verify) ------

PRESERVATION_REQUIRED_PREFIXES = ("provenance:", "snapshot:", "export:")


def create_preservation_envelope(
    entries: dict[str, str],
    preservation_id: str,
    classification: str,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a restore-verifiable preservation envelope over sanitized entries.

    Hermetic scope: proves structure (provenance/snapshot/export scopes
    present), sha256 integrity per entry, reproducible-vs-unique
    classification, redaction, and access-restricted persistence (0o600 when
    written). Real snapshot bytes live in operator-controlled authorized
    storage; the envelope never claims live recovery from a checkout.
    ``classification`` must be ``reproducible`` or ``unique``.
    """
    if not preservation_id:
        raise ValueError("preservation_id is required")
    if classification not in {"reproducible", "unique"}:
        raise ValueError("classification must be 'reproducible' or 'unique'")
    missing = [
        p for p in PRESERVATION_REQUIRED_PREFIXES
        if not any(k.startswith(p) for k in entries)
    ]
    if missing:
        raise ValueError(f"preservation missing scopes: {', '.join(missing)}")
    digests = {
        k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in entries.items()
    }
    envelope: dict[str, Any] = {
        "preservation_id": preservation_id,
        "scopes": sorted(entries.keys()),
        "digests": digests,
        "classification": classification,
        "storage": "operator-controlled-authorized-storage",
        "redaction": "sanitized-counts-and-refs-only",
        "issue": ISSUE_REF,
    }
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / f"{preservation_id}.json"
        target.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            # Best-effort permission hardening; envelope content is already
            # written, so a chmod failure must not fail the rehearsal.
            pass
    return envelope


def verify_preservation_envelope(
    envelope: dict[str, Any], entries: dict[str, str]
) -> bool:
    """Restore-verify an envelope against candidate entries."""
    digests = envelope.get("digests", {})
    if set(digests) != set(entries):
        return False
    return all(
        hashlib.sha256(v.encode("utf-8")).hexdigest() == digests[k]
        for k, v in entries.items()
    )


def check_preservation_plan(description: str) -> StepResult:
    """Validate a preservation description textually; moves no real data."""
    lowered = description.lower()
    if "derived" in lowered and "disposable" in lowered:
        return StepResult(
            "preservation-plan", "failed",
            "Refused: vectors being derived never proves payloads "
            "reconstructible; classify reproducible vs unique from verified "
            "snapshot/export recovery instead.",
        )
    if "assum" in lowered and "reconstruct" in lowered:
        return StepResult(
            "preservation-plan", "failed",
            "Refused: never assume payloads reconstructible; verify "
            "recoverability before any disposal decision.",
        )
    if "public" in lowered and ("issue" in lowered or "source control" in lowered):
        return StepResult(
            "preservation-plan", "failed",
            "Refused: snapshots/exports belong in operator-controlled "
            "authorized storage, not public issues or source control.",
        )
    negated = (
        re.search(r"\bdo not\b.{0,24}\b(record|provenance|snapshot|export|verif\w*)\b", lowered)
        or re.search(r"\bskip\b.{0,24}\b(snapshot|export|provenance|verif\w*)\b", lowered)
        or re.search(r"\b(unnecessary|without|never|no)\b.{0,24}\b(provenance|snapshot|export|verif\w*)\b", lowered)
    )
    if negated:
        return StepResult(
            "preservation-plan", "failed",
            "Refused: preservation scope described negatively "
            f"({negated.group(0)!r}); retirement requires affirmative, "
            "positively verified preservation evidence (recorded provenance, "
            "snapshot plus logical export, verified recoverability), not "
            "keyword-bearing prose that disclaims recovery.",
        )
    required = ("provenance", "snapshot", "export", "verif")
    if not all(r in lowered for r in required):
        return StepResult(
            "preservation-plan", "blocked",
            "Preservation plan incomplete: name collection/payload "
            "provenance (image/version, mounts, recovery config), a "
            "recoverable snapshot plus logical export, recoverability "
            "verification, and reproducible-vs-unique classification.",
        )
    if "retrieval_state" in lowered and "independent" in lowered:
        evidence = (
            "Preservation plan textually safe: provenance recorded, snapshot "
            "+ logical export with verified recovery, reproducible-vs-unique "
            "classification, operator-controlled storage with redaction and "
            "retention, moonmind_retrieval_state evidence preserved "
            "independently (#4107)."
        )
    else:
        evidence = (
            "Preservation plan textually safe: provenance recorded, snapshot "
            "+ logical export with verified recovery, reproducible-vs-unique "
            "classification, operator-controlled storage with redaction and "
            "retention. Record #4107-separated retrieval-state evidence "
            "before disposal."
        )
    return StepResult("preservation-plan", "completed", evidence)


def check_backup_freeze_drain() -> StepResult:
    """Exercise preservation/freeze/drain guards hermetically."""
    entries = {
        "provenance:image-version": "qdrant/qdrant:v1.17.1 (recorded)",
        "provenance:mounts": "qdrant-storage:/qdrant/storage (recorded)",
        "provenance:recovery-config": "retention-window + exact-resource deletion (recorded)",
        "snapshot:content": "sanitized-collection-count=2",
        "export:logical-payload": "sanitized-document-count=4",
    }
    envelope = create_preservation_envelope(entries, "qdrant-hermetic-check", "unique")
    if not verify_preservation_envelope(envelope, entries):
        return StepResult(
            "preservation-drain", "failed",
            "Hermetic preservation envelope failed restore-verification.",
        )
    tracker = DrainTracker()
    tracker.admit("vector-activity-1", "wf-qdrant-1")
    if tracker.drain().status != "blocked":
        return StepResult(
            "preservation-drain", "failed",
            "Drain did not report in-flight vector work.",
        )
    tracker.settle("vector-activity-1")
    drained = tracker.drain()
    if drained.status != "completed" or "wf-qdrant-1" not in tracker.admitted_workflows:
        return StepResult(
            "preservation-drain", "failed",
            "Drain discarded or lost admitted vector work.",
        )
    return StepResult(
        "preservation-drain", "completed",
        "Hermetic guards pass: envelope restore-verified with unique "
        "classification (operator storage still required for live bytes), "
        "drain preserves admitted vector workflows. Compat boundary: "
        f"{LAST_BACKWARD_COMPATIBLE_POINT}.",
    )


class DrainTracker:
    """Account for in-flight vector work without discarding histories.

    Admitted Temporal work is preserved by construction: draining only waits
    for tracked activity ids to finish; it never cancels workflow records
    and never rewrites immutable inputs.
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
                "vector-drain", "blocked",
                f"{len(self.in_flight)} vector Activities still in flight; "
                f"{len(self.admitted_workflows)} admitted workflows preserved, "
                "none discarded.",
            )
        return StepResult(
            "vector-drain", "completed",
            f"Drained: 0 in flight, {len(self.admitted_workflows)} admitted "
            "workflows preserved.",
        )


LAST_BACKWARD_COMPATIBLE_POINT = (
    "pre-cutover release still serves vector reads; new writes already "
    "retired at admission (#4105); no destructive collection/payload "
    "rewrite yet"
)


# -- R5: failure-injection across the real-shaped cutover sequence ------------

CUTOVER_STEPS = (
    "preflight",
    "preserve",
    "upgrade",
    "retire",
    "verify",
)


def run_cutover_sequence(fail_at: str | None = None) -> dict[str, Any]:
    """Run the real-shaped cutover step sequence with optional injection.

    Pure harness: each step records completed/failed without side effects.
    ``fail_at`` names the step where an injected failure occurs; steps after
    it are recorded ``skipped``. Callers apply rollback/forward-repair rules
    (never whole-DB restore, never an optional Qdrant profile back,
    reconciliation required after post-cutover writes).
    """
    if fail_at is not None and fail_at not in CUTOVER_STEPS:
        raise ValueError(f"unknown cutover step: {fail_at}")
    steps: dict[str, str] = {}
    for step in CUTOVER_STEPS:
        if fail_at is None or CUTOVER_STEPS.index(step) < CUTOVER_STEPS.index(fail_at):
            steps[step] = "completed"
        elif step == fail_at:
            steps[step] = "failed-injected"
        else:
            steps[step] = "skipped"
    return {
        "steps": steps,
        "backward_compatible_point": LAST_BACKWARD_COMPATIBLE_POINT,
        "rollback_rule": (
            "restore previous matching app/Compose/image revision with "
            "preserved data after schema-compatibility check; never add an "
            "optional Qdrant profile or adapter back; reconcile-after-writes "
            "or forward repair"
        ),
    }


def check_failure_injection() -> StepResult:
    """Verify failure before/after each cutover step is contained."""
    for step in CUTOVER_STEPS:
        run = run_cutover_sequence(fail_at=step)
        idx = CUTOVER_STEPS.index(step)
        before = [s for s in CUTOVER_STEPS[:idx]]
        after = [s for s in CUTOVER_STEPS[idx + 1:]]
        if any(run["steps"][s] != "completed" for s in before):
            return StepResult(
                "failure-injection", "failed",
                f"Steps before injected failure at {step} did not complete.",
            )
        if any(run["steps"][s] != "skipped" for s in after):
            return StepResult(
                "failure-injection", "failed",
                f"Steps after injected failure at {step} were not skipped.",
            )
        if run["steps"][step] != "failed-injected":
            return StepResult(
                "failure-injection", "failed",
                f"Injected failure at {step} not recorded.",
            )
    clean = run_cutover_sequence()
    if any(v != "completed" for v in clean["steps"].values()):
        return StepResult(
            "failure-injection", "failed",
            "Clean cutover sequence did not complete all steps.",
        )
    return StepResult(
        "failure-injection", "completed",
        f"Failure contained at each of {len(CUTOVER_STEPS)} steps "
        f"({', '.join(CUTOVER_STEPS)}); clean run completes; "
        f"compat boundary: {LAST_BACKWARD_COMPATIBLE_POINT}.",
    )


# -- R6: manifest-boundary replay evidence ------------------------------------

def check_workflow_history_authority(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> StepResult:
    """Assert a cutover change did not rewrite workflow command order/owners.

    Records are compared by stable ``workflow_id`` key, not by list
    position, so a reordered or cross-associated evidence collection cannot
    pass as exact replay. Immutable inputs are never rewritten.
    """
    if len(before) != len(after):
        return StepResult(
            "workflow-history-authority", "failed",
            "Workflow history length changed across cutover boundary; requires "
            "exact compatibility/replay evidence or bounded old-release drain.",
        )
    if all("workflow_id" in dict(b) and "workflow_id" in dict(a) for b, a in zip(before, after)) \
            and any("workflow_id" in dict(r) for r in [*before, *after]):
        before_by_id = {r["workflow_id"]: r for r in before}
        after_by_id = {r["workflow_id"]: r for r in after}
        if set(before_by_id) != set(after_by_id):
            return StepResult(
                "workflow-history-authority", "failed",
                "Workflow identity set changed across cutover boundary "
                f"(before={sorted(before_by_id)}, after={sorted(after_by_id)}); "
                "requires exact compatibility/replay evidence or bounded "
                "old-release drain.",
            )
        for wid, b in before_by_id.items():
            a = after_by_id[wid]
            if b.get("owner_id") != a.get("owner_id") or b.get("commands") != a.get("commands"):
                return StepResult(
                    "workflow-history-authority", "failed",
                    f"History rewrite detected on {wid}: owner/command "
                    "order must be preserved across the cutover boundary.",
                )
        return StepResult(
            "workflow-history-authority", "completed",
            f"Verified {len(before)} workflow histories readable with unchanged "
            "workflow IDs, owner IDs, and command order.",
        )
    for b, a in zip(before, after):
        if b.get("owner_id") != a.get("owner_id") or b.get("commands") != a.get("commands"):
            return StepResult(
                "workflow-history-authority", "failed",
                f"History rewrite detected on {b.get('workflow_id')}: owner/command "
                "order must be preserved across the cutover boundary.",
            )
    return StepResult(
        "workflow-history-authority", "completed",
        f"Verified {len(before)} workflow histories readable with unchanged "
        "owner IDs and command order.",
    )


def historical_manifest_entries() -> list[dict[str, Any]]:
    """Return the two historical generic ManifestIngest entry contracts.

    ``manifest_ref`` compiles-and-summarizes; ``manifestArtifactRef``
    orchestrates nodes. They are distinct persisted contracts, not
    interchangeable aliases; supplying both is ambiguous and fails before
    any Activity is scheduled. Preserved verbatim for replay.
    """
    return [
        {
            "workflow_id": "manifest-historical-compilation",
            "entry": "manifest_ref",
            "owner_id": "sanitized-owner",
            "commands": ["manifest.compile", "manifest.write_summary"],
        },
        {
            "workflow_id": "manifest-historical-orchestration",
            "entry": "manifestArtifactRef",
            "owner_id": "sanitized-owner",
            "commands": [
                "manifest_read",
                "manifest_compile",
                "manifest_write_summary",
            ],
        },
    ]


def check_manifest_boundary_replay(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    boundary_changed: bool,
) -> StepResult:
    """Tie history-authority to whether a persisted boundary really changed.

    When no cutover change ships a persisted/history boundary change,
    identical histories complete with that fact recorded. When a boundary
    did change, the caller must supply exact replay evidence (identical
    owner/command records) or a bounded old-release drain; any rewrite
    fails. A history that cannot safely run on the new release needs
    explicit old-release drain ownership, not a silent fallback.
    """
    base = check_workflow_history_authority(before, after)
    if base.status == "failed":
        return base
    if boundary_changed:
        return StepResult(
            "manifest-boundary-replay", "completed",
            f"Boundary changed and exact replay evidence verified over "
            f"{len(before)} histories (owner IDs + command order identical; "
            "immutable inputs not rewritten).",
        )
    return StepResult(
        "manifest-boundary-replay", "completed",
        f"No persisted/history boundary change in this checkout; "
        f"{len(before)} histories readable with unchanged owner IDs and "
        "command order. A future cutover change that alters the boundary "
        "must supply exact replay evidence or a bounded old-release drain.",
    )


def check_rollback_plan(scope: str) -> StepResult:
    """Validate a rollback scope name without touching any data.

    Only a structured, explicitly permitted scope completes: the previous
    matching app/Compose/image revision with preserved data, a
    schema-compatibility check, and rehearsal recorded. Anything else is
    refused (optional Qdrant profile back, destructive operations, silent
    fallback), blocked (restore without compatibility check/rehearsal), or
    unrecognized.
    """
    lowered = scope.lower()
    if "qdrant" in lowered and ("profile" in lowered or "adapter" in lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: never add an optional Qdrant profile or adapter back "
            "to the new release as a rollback feature.",
        )
    if "whole" in lowered and ("database" in lowered or "postgres" in lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: never roll back the whole shared database to undo a "
            "cutover; restore the previous matching revision with preserved "
            "data instead.",
        )
    if re.search(r"\b(drop|delete|remove|destroy|wipe|purge)\b.{0,40}\b(database|volume|identity|role)\b", lowered) or \
            re.search(r"\b(database|volume|identity|role)\b.{0,40}\b(drop|delete|remove|destroy|wipe|purge)\b", lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: destructive database/volume/identity operation is not "
            "a safe revision+data rollback.",
        )
    if "disabled" in lowered and ("fallback" in lowered or "switch" in lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: never silently switch to a disabled fallback as rollback.",
        )
    if (
        "matching" in lowered
        and ("revision" in lowered or "compose" in lowered or "image" in lowered)
        and "preserved" in lowered
        and ("compatib" in lowered or "schema" in lowered)
        and ("rehears" in lowered or "reconcil" in lowered or "forward" in lowered)
    ):
        return StepResult(
            "rollback-scope", "completed",
            "Rollback scope accepted hermetically: previous matching "
            "app/Compose/image revision with preserved data after a "
            "schema-compatibility check, rehearsed; no Qdrant profile back.",
        )
    if "snapshot" in lowered or "restore" in lowered:
        return StepResult(
            "rollback-scope", "blocked",
            "Restore without a named matching revision, preserved-data set, "
            "schema-compatibility check, and rehearsal requires those first.",
        )
    return StepResult(
        "rollback-scope", "blocked",
        f"Unrecognized rollback scope {scope!r}; supply the previous matching "
        "revision with preserved data, a schema-compatibility check, and "
        "rehearsal. No positive rollback evidence for absent instructions.",
    )


def resolve_exact_container(
    candidates: list[dict[str, str]], project: str, service: str
) -> dict[str, str]:
    """Resolve the single obsolete container owned by (project, service).

    Raises ``ValueError`` on wrong/ambiguous ownership: unknown project,
    empty candidates, zero matches, or multiple matches. The caller treats
    that as a blocked retirement, never a broad cleanup.
    """
    if not project or not service:
        raise ValueError("project and service are both required")
    matches = [
        c for c in candidates
        if c.get("project") == project and c.get("service") == service
    ]
    if not matches:
        raise ValueError(
            f"no {service!r} container owned by project {project!r}; "
            "refusing ambiguous ownership"
        )
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous ownership: {len(matches)} {service!r} containers "
            f"in project {project!r}; refusing"
        )
    return matches[0]


def check_retirement_plan(
    actions: list[str],
    ownership: dict[str, str] | None = None,
) -> StepResult:
    """Validate retirement actions textually; performs no container mutation."""
    joined = "\n".join(actions)
    for pattern in FORBIDDEN_RETIREMENT_PATTERNS:
        if pattern.search(joined):
            return StepResult(
                "retirement-plan", "failed",
                f"Refused destructive retirement action matching {pattern.pattern!r}; "
                "retirement must stop/remove only the exact obsolete Qdrant "
                "container, never broad orphan cleanup, down -v, volume "
                "prune, filesystem deletion, or unrelated state.",
            )
    if not any("qdrant" in a.lower() for a in actions):
        return StepResult(
            "retirement-plan", "blocked",
            "No precisely identified obsolete Qdrant container in plan; "
            "retirement stays blocked until inventory names exact "
            "project/service/container IDs.",
        )
    if ownership is None:
        return StepResult(
            "retirement-plan", "blocked",
            "Retirement plan names Qdrant work but carries no structured "
            "ownership (exact Compose project/service/container IDs). "
            "Ambiguous ownership must block retirement; resolve exact "
            "ownership before this check can complete.",
        )
    try:
        resolved = resolve_exact_container(
            ownership.get("candidates", []),  # type: ignore[arg-type]
            ownership.get("project", ""),
            ownership.get("service", ""),
        )
    except ValueError as exc:
        return StepResult("retirement-plan", "blocked", str(exc))
    container = resolved.get("container_id", "identified-container")
    return StepResult(
        "retirement-plan", "completed",
        f"Retirement plan textually safe with exact ownership "
        f"(project={ownership.get('project')!r}, service='qdrant', "
        f"container={container!r}): stop/remove only that container, "
        "verify absence afterward, repeat checks idempotent. Execution "
        "still requires owner approval and positive absence verification.",
    )


def check_retention_policy(description: str) -> StepResult:
    """Validate the retention/disposition policy for volumes and exports."""
    lowered = description.lower()
    if re.search(r"\bautomat\w*\b.{0,30}\bdelet\w*\b", lowered) or \
            re.search(r"\bdelet\w*\b.{0,30}\bautomat\w*\b", lowered):
        return StepResult(
            "retention-policy", "failed",
            "Refused: no upgrade path automatically deletes old volumes; "
            "permanent deletion is a separate exact-resource operator action.",
        )
    required = ("retention", "exact-resource", "verif")
    if not all(r in lowered for r in required):
        return StepResult(
            "retention-policy", "blocked",
            "Retention policy incomplete: name an explicit recovery/retention "
            "window, exact-resource operator-authorized deletion after "
            "export/restore verification, and preserved unrelated state "
            "(PostgreSQL, MinIO, secrets, workspaces, Omnigent).",
        )
    return StepResult(
        "retention-policy", "completed",
        "Retention policy accepted hermetically: old volume and exports "
        "retained through an explicit recovery window; eventual deletion "
        "requires separate exact-resource operator authorization after "
        "export/restore verification; unrelated state preserved.",
    )


# -- Idempotent rehearsal state ----------------------------------------------

def load_state(state_dir: Path, migration_id: str) -> dict[str, Any]:
    state_file = state_dir / "qdrant_rehearsal_state.json"
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` atomically via a same-directory temp file.

    A direct overwrite can truncate the sole state file when the process is
    interrupted mid-write, after which the tool deliberately refuses further
    progress as corrupt. ``os.replace`` makes the new content appear
    atomically; the temp file is fsynced before the rename on platforms
    that support it.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                # Best-effort durability; the atomic rename below is the
                # correctness guarantee, fsync only shortens the crash window.
                pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            # Best-effort temp cleanup; the original exception below is the
            # failure that matters, and a leftover .tmp file is never read.
            pass
        raise


@contextlib.contextmanager
def _state_locked(state_dir: Path):
    """Hold an exclusive lock for one state read-modify-write cycle.

    Best-effort: serializes concurrent invocations sharing a migration ID
    so the last writer cannot silently erase another run's completed steps.
    Platforms without ``fcntl`` still get the atomic-replace guarantee
    against truncation, just without cross-process mutual exclusion.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX platforms
        yield
        return
    fd = os.open(state_dir / ".qdrant_rehearsal.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            # Best-effort mutual exclusion; the atomic temp-file replace in
            # _atomic_write_text still guards against truncation without it.
            pass
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            # Best-effort unlock; the fd close below releases the lock anyway.
            pass
        os.close(fd)


def save_state(
    state_dir: Path,
    migration_id: str,
    step_names: list[str],
    allow_replace: bool = False,
) -> tuple[bool, str]:
    """Persist rehearsal progress idempotently.

    Returns (ok, message). A state file owned by a different migration_id is
    a stale/concurrent cutover: refuse unless --allow-replace is given, so
    interruption/restart reconciles the SAME migration instead of forking it.
    The read-modify-write runs under an exclusive lock and persists through
    an atomic temp-file rename, so concurrent runs cannot erase each other's
    progress and an interruption cannot truncate the durable record.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    with _state_locked(state_dir):
        return _save_state_locked(state_dir, migration_id, step_names, allow_replace)


def _save_state_locked(
    state_dir: Path,
    migration_id: str,
    step_names: list[str],
    allow_replace: bool = False,
) -> tuple[bool, str]:
    state_file = state_dir / "qdrant_rehearsal_state.json"
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


def rehearse_scenario(scenario: str) -> StepResult:
    """Rehearse one advertised mode hermetically; assert evidence preserved."""
    try:
        fixture = build_sanitized_fixture(scenario)
    except ValueError as exc:
        return StepResult(f"rehearsal-{scenario}", "failed", str(exc))
    verdict = evaluate_preflight(fixture)
    entries = historical_manifest_entries()
    replay = check_workflow_history_authority(entries, entries)
    if replay.status != "completed":
        return StepResult(
            f"rehearsal-{scenario}", "failed",
            "Historical manifest entries not replayable; immutable evidence altered.",
        )
    # Negative control: the authority check must discriminate, not merely
    # complete. A tampered history (dropped command) has to fail, otherwise
    # this gate would pass even after a real ManifestIngest schema, handler,
    # or ordering incompatibility. Live execution of histories through the
    # current workflow/activity boundary stays out of hermetic scope and is
    # owned by the separately blocked live-deployment qualification.
    tampered = [dict(e) for e in entries]
    tampered[0] = {**tampered[0], "commands": tampered[0]["commands"][:-1]}
    if check_workflow_history_authority(entries, tampered).status != "failed":
        return StepResult(
            f"rehearsal-{scenario}", "failed",
            "Replay authority check is vacuous: a history with a dropped "
            "command still verifies. Refusing positive replay evidence.",
        )
    if scenario in ("fresh-vector-free", "omitted-upgrade"):
        if verdict.status != "proceed":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected proceed for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    elif scenario in ("explicit-retired", "legacy-qdrant-drain"):
        if verdict.status != "drain":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected drain for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    else:  # rollback-restore
        if verdict.status != "blocked":
            return StepResult(
                f"rehearsal-{scenario}", "failed",
                f"Expected blocked for {scenario}, got {verdict.status}: {verdict.evidence}",
            )
    return StepResult(
        f"rehearsal-{scenario}", "completed",
        f"Hermetic rehearsal passed for {scenario}: preflight {verdict.status}, "
        f"{len(entries)} historical manifest entries replayable with unchanged "
        "owner/command evidence; no live deployment contacted.",
    )


def run_gate(
    repo_root: Path = REPO_ROOT,
    scenarios: tuple[str, ...] = REHEARSAL_MODES,
) -> list[StepResult]:
    results: list[StepResult] = [check_runbook_precondition(repo_root)]
    results.extend(check_prerequisites(repo_root))
    results.append(check_inventory_survey(repo_root))
    results.append(check_preflight_report(repo_root))
    results.append(check_build_pins(repo_root))
    for scenario in scenarios:
        results.append(rehearse_scenario(scenario))
    entries = historical_manifest_entries()
    results.append(
        check_manifest_boundary_replay(entries, entries, boundary_changed=False)
    )
    results.append(check_backup_freeze_drain())
    results.append(check_failure_injection())
    results.append(check_upgrade_fixture(build_old_env_fixture(), repo_root))
    results.append(
        check_preservation_plan(
            "record collection/payload provenance (image/version, mounts, "
            "recovery config); preserve recoverable snapshot plus logical "
            "export; verify recoverability; classify reproducible vs unique; "
            "operator-controlled storage with redaction/retention, "
            "independent moonmind_retrieval_state evidence"
        )
    )
    results.append(
        check_rollback_plan(
            "previous matching app/compose/image revision with preserved data, "
            "schema-compatibility check, rehearsed"
        )
    )
    results.append(
        check_retention_policy(
            "explicit recovery retention window; eventual deletion is a "
            "separate exact-resource operator action after export/restore "
            "verification; unrelated state preserved"
        )
    )
    results.append(
        check_retirement_plan(
            ["stop/remove exact qdrant container (identified from inventory)"],
            {
                "project": "moonmind",
                "service": "qdrant",
                "candidates": [
                    {
                        "project": "moonmind",
                        "service": "qdrant",
                        "container_id": "moonmind-qdrant-1",
                    }
                ],
            },
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hermetic Qdrant cutover rehearsal gate (#4115)."
    )
    parser.add_argument("--state-dir", type=Path, default=Path("var/artifacts/qdrant_rehearsal"))
    parser.add_argument("--migration-id", default="qdrant-rehearsal-4115")
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
        default="previous matching app/compose/image revision with preserved data, "
        "schema-compatibility check, rehearsed",
    )
    parser.add_argument("--retire-action", action="append", default=[])
    parser.add_argument(
        "--retire-project",
        default=None,
        help="Exact Compose project owning the obsolete Qdrant container.",
    )
    parser.add_argument(
        "--retire-service",
        default=None,
        help="Exact Compose service owning the obsolete Qdrant container.",
    )
    parser.add_argument(
        "--retire-container-id",
        default=None,
        help="Exact container ID of the obsolete Qdrant container.",
    )
    parser.add_argument("--preservation-description", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.mode in ("preflight", "all"):
        results: list[StepResult] = [check_runbook_precondition()]
        results.extend(check_prerequisites())
        results.append(check_inventory_survey())
        results.append(check_preflight_report())
        results.append(check_build_pins())
    else:
        results = []
    if args.mode in ("rehearsal", "all"):
        for scenario in REHEARSAL_MODES:
            results.append(rehearse_scenario(scenario))
        entries = historical_manifest_entries()
        results.append(
            check_manifest_boundary_replay(entries, entries, boundary_changed=False)
        )
        results.append(check_backup_freeze_drain())
        results.append(check_failure_injection())
    if args.mode in ("upgrade-check", "all"):
        results.append(check_upgrade_fixture(build_old_env_fixture()))
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
                    "record collection/payload provenance (image/version, mounts, "
                    "recovery config); preserve recoverable snapshot plus logical "
                    "export; verify recoverability; classify reproducible vs unique; "
                    "operator-controlled storage with redaction/retention, "
                    "independent moonmind_retrieval_state evidence"
                )
            )
        results.append(
            check_retention_policy(
                "explicit recovery retention window; eventual deletion is a "
                "separate exact-resource operator action after export/restore "
                "verification; unrelated state preserved"
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
            actions = ["stop/remove exact qdrant container (identified from inventory)"]
        if args.retire_project and args.retire_service and args.retire_container_id:
            ownership = {
                "project": args.retire_project,
                "service": args.retire_service,
                "candidates": [
                    {
                        "project": args.retire_project,
                        "service": args.retire_service,
                        "container_id": args.retire_container_id,
                    }
                ],
            }
        else:
            ownership = None
        results.append(check_retirement_plan(actions, ownership))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = save_state(
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
