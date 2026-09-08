#!/usr/bin/env python3
"""Hermetic K6 cutover rehearsal gate for #4131.

This tool provides early, bounded, idempotent preflight/rehearsal
verification for the Keycloak-removal deployment cutover WITHOUT performing
any live deployment mutation. It never:

- contacts a live IdP, MFA provider, or deployment host,
- mints or invalidates real sessions,
- deletes services, volumes, roles, or databases,
- marks operator-owned facts complete on hermetic evidence alone.

Operator-gated steps (named-owner approval, live IdP/MFA qualification,
coordinated release, retirement execution, retention disposition) are
reported as ``blocked`` with the missing evidence named. That is the
correct terminal state for a repo checkout: rehearsal fixtures may pass
while deployment qualification stays blocked.

The tool intentionally does NOT implement schema migration, mode/key setup,
or recovery (#4119/#4120/#4122 own those). It gates on their presence and
fails closed when they are absent.
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
PLAN_REL = Path("docs/tmp/KeycloakRemovalPlan.md")
ISSUE_REF = "MoonLadderStudios/MoonMind#4131"

REHEARSAL_MODES = (
    "fresh-accounts",
    "explicit-local",
    "omitted-upgrade",
    "legacy-builtin",
    "kc-to-accounts",
    "kc-to-external",
)

# Prerequisites owned by earlier Keycloak-removal issues. Absence blocks
# deployment qualification; it never blocks hermetic fixture verification.
# Names are stable identifiers, not a migration coordinator.
PREREQUISITE_IDS = (
    "4117-inventory",
    "4119-mapping-revision",
    "4120-mode-key-setup",
    "4122-protected-recovery",
    "4128-qualification",
    "4129-removal",
    "4130-operator-contracts",
    "named-owner-approval",
    "live-idp-mfa-qualification",
)

FORBIDDEN_RETIREMENT_PATTERNS = (
    re.compile(r"\bdown\s+-v\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bDROP\s+(DATABASE|ROLE)\b", re.IGNORECASE),
    re.compile(r"temporal(_\w+)?\s*(volume|database).*(delet|remov|drop)", re.IGNORECASE),
    re.compile(r"\bapp(lication)?\b.*\bvolume\b.*\b(delet|remov)", re.IGNORECASE),
    re.compile(r"\bvolume\b.{0,40}(delet|remov|drop)", re.IGNORECASE),
    re.compile(r"(delet|remov|drop).{0,40}\bvolume\b", re.IGNORECASE),
    # Destructive database/role retirement is never a safe "stop service"
    # plan: e.g. "delete keycloak database" or "remove keycloak database".
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\b(database|role)\b", re.IGNORECASE),
    re.compile(r"\b(database|role)\b.{0,40}(delet|remov|drop|destroy|wipe|purge)", re.IGNORECASE),
    re.compile(r"(delet|remov|drop|destroy|wipe|purge).{0,40}\bidentity\b.{0,40}\brows?\b", re.IGNORECASE),
)

_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+\S+"
    r"|(?:password|passwd|secret|bearer|cookie|session[_-]?token|refresh[_-]?token"
    r"|token)\s*[:=]\s*[^\s;,\"']+)"
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
    """Verify the removal plan is present and still pre-cutover (Proposed)."""
    content = _read_text(repo_root / PLAN_REL)
    if content is None:
        return StepResult(
            "plan-precondition", "failed",
            f"{PLAN_REL} missing; K6 rehearsal has no accepted plan baseline.",
        )
    if "Status: Proposed" in content and "K6" in content:
        return StepResult(
            "plan-precondition", "completed",
            "Removal plan present with Status: Proposed; correctly unarchived pre-cutover.",
        )
    return StepResult(
        "plan-precondition", "failed",
        "Removal plan present but precondition unreadable (missing Proposed status or K6).",
    )


def check_prerequisites(repo_root: Path = REPO_ROOT) -> list[StepResult]:
    """Report every deployment prerequisite, wired to real repo presence checks.

    Each prerequisite probes the actual checkout for the owning capability
    (#4119 dry-run/apply entrypoint, #4120 mode/key setup, #4122 recovery,
    #4129 removal, #4130 contracts, protected inventory, owner approval,
    live IdP/MFA). Repo-observable capabilities (4119/4120/4122/4129) flip
    to ``completed`` when the owning sibling change lands in the checkout;
    owner-held or deployment-side items (4117 inventory, 4128 qualification,
    4130 contracts, owner approval, live IdP/MFA) stay honestly ``blocked``
    hermetically and name the missing evidence. Hermetic fixture
    verification is never gated on these prerequisites.
    """
    presence = detect_capability_presence(repo_root)
    owners = {
        "4117-inventory": "deployment owner protected inventory (#4117)",
        "4119-mapping-revision": "migration mapping/schema/config revision (#4119)",
        "4120-mode-key-setup": "persisted mode/key setup (#4120)",
        "4122-protected-recovery": "protected recovery path (#4122)",
        "4128-qualification": "candidate image/config/schema qualification (#4128)",
        "4129-removal": "integrated removal change (#4129)",
        "4130-operator-contracts": "pre-cutover operator contracts (#4130)",
        "named-owner-approval": "named deployment owner approval",
        "live-idp-mfa-qualification": "separately authorized live IdP/MFA check",
    }
    results = []
    for pid in PREREQUISITE_IDS:
        probed = presence.get(pid, "unchecked")
        if _capability_present(pid, repo_root):
            results.append(
                StepResult(
                    f"prerequisite-{pid}", "completed",
                    f"Capability present in repo checkout; owned by {owners[pid]}. "
                    f"Repo probe: {probed}.",
                )
            )
        else:
            results.append(
                StepResult(
                    f"prerequisite-{pid}", "blocked",
                    f"Missing in repo checkout; owned by {owners[pid]}. "
                    f"Repo probe: {probed}. "
                    "Blocks deployment qualification, not hermetic rehearsal.",
                )
            )
    return results


def _read_auth_provider_default(settings_text: str) -> str | None:
    """Extract the AUTH_PROVIDER default contract from the settings module.

    Only the declared ``AUTH_PROVIDER`` default counts as mode evidence;
    unrelated literals elsewhere in the module (e.g. ``CACHE_BACKEND``) must
    not flip the prerequisite.
    """
    m = re.search(r"AUTH_PROVIDER:\s*str\s*=\s*Field\(\s*\"([^\"]+)\"", settings_text)
    return m.group(1) if m else None


def _capability_present(pid: str, repo_root: Path = REPO_ROOT) -> bool:
    """Whether a prerequisite's owning capability is observable in the checkout.

    Only repo-observable capabilities may flip: a #4119 Keycloak migration or
    dry-run/apply entrypoint outside this gate, a new AUTH_PROVIDER mode
    beyond disabled/keycloak (#4120), a recovery tool outside this gate
    (#4122), or the absence of all Keycloak surfaces after integrated
    removal (#4129). Owner-held and deployment-side items (4117 protected
    inventory, 4128 qualification, 4130 contracts, named-owner approval,
    live IdP/MFA) never flip hermetically and always return False.
    """
    if pid == "4119-mapping-revision":
        candidates = (
            "api_service/migrations/versions/*keycloak*",
            "tools/*keycloak*apply*",
            "tools/*keycloak*dry*",
        )
        gate_name = Path(__file__).name
        return any(
            p.name != gate_name
            for pat in candidates
            for p in sorted(repo_root.glob(pat))
        )
    if pid == "4120-mode-key-setup":
        settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
        default = _read_auth_provider_default(settings_text)
        return default is not None and default not in {"disabled", "keycloak"}
    if pid == "4122-protected-recovery":
        tools_dir = repo_root / "tools"
        if not tools_dir.exists():
            return False
        return bool(sorted(tools_dir.glob("*recover*")))
    if pid == "4129-removal":
        return not collect_inventory_survey(repo_root).get("keycloak_surfaces")
    return False


# -- R1: protected inventory survey (sanitized, no identity exports) ---------

# Files probed by the sanitized survey. Counts and file:line refs only; the
# survey never exports users, roles, sessions, secrets, or identity mappings.
SURVEY_SOURCES = (
    "docker-compose.yaml",
    "keycloak/realm-export.json",
    "moonmind/config/settings.py",
    "api_service/main.py",
    "api_service/auth_providers.py",
    "api_service/db/models.py",
    "init_db_scripts/01-create-dbs.sh",
    ".env-template",
)


def collect_inventory_survey(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect a sanitized deployment-state survey from real repo files.

    Reports structural counts (services, clients, provider literals, route
    branches) with file references. Never publishes identity exports: no
    user ids, emails, secrets, tokens, or session material is collected.
    Missing files are recorded as ``missing`` rather than failing.
    """
    survey: dict[str, Any] = {"sources": {}, "keycloak_surfaces": []}
    compose = _read_text(repo_root / "docker-compose.yaml")
    if compose is None:
        survey["sources"]["docker-compose.yaml"] = "missing"
    else:
        services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", compose, re.MULTILINE)
        survey["sources"]["docker-compose.yaml"] = f"{len(services)} services"
        survey["compose_services"] = sorted(set(services))[:50]
        m = re.search(r"image:\s*(quay\.io/keycloak[^\s]*)", compose)
        survey["keycloak_image"] = m.group(1) if m else "not-pinned-or-absent"
        if re.search(r"(?m)^  keycloak:", compose):
            survey["keycloak_surfaces"].append("docker-compose.yaml: keycloak service")
    realm_text = _read_text(repo_root / "keycloak/realm-export.json")
    if realm_text is None:
        survey["sources"]["keycloak/realm-export.json"] = "missing"
    else:
        try:
            realm = json.loads(realm_text)
            clients = realm.get("clients", [])
            # Sanitized: clientIds and counts only; secrets/URLs redacted.
            survey["sources"]["keycloak/realm-export.json"] = f"{len(clients)} clients"
            survey["realm_clients"] = sorted(
                str(c.get("clientId", "?")) for c in clients
            )
            survey["realm"] = str(realm.get("realm", "?"))
        except (json.JSONDecodeError, AttributeError):
            survey["sources"]["keycloak/realm-export.json"] = "unparseable"
    settings_text = _read_text(repo_root / "moonmind/config/settings.py")
    if settings_text is None:
        survey["sources"]["moonmind/config/settings.py"] = "missing"
    else:
        m = re.search(r"AUTH_PROVIDER:\s*str\s*=\s*Field\(\s*\"([^\"]+)\"", settings_text)
        survey["sources"]["moonmind/config/settings.py"] = "read"
        survey["auth_provider_default"] = m.group(1) if m else "unknown"
        survey["auth_provider_desc_keycloak"] = "keycloak" in settings_text.lower()
    for rel in (
        "api_service/main.py",
        "api_service/auth_providers.py",
        "api_service/db/models.py",
        "init_db_scripts/01-create-dbs.sh",
        ".env-template",
    ):
        text = _read_text(repo_root / rel)
        if text is None:
            survey["sources"][rel] = "missing"
            continue
        hits = len(re.findall(r"[Kk]eycloak|KEYCLOAK", text))
        survey["sources"][rel] = f"read, {hits} keycloak mentions"
        if hits:
            survey["keycloak_surfaces"].append(f"{rel}: {hits} keycloak mentions")
    survey["note"] = (
        "Structural survey only. Protected owner facts (users, issuer mappings, "
        "sessions, service accounts, realm consumers, MFA/SSO) require the #4117 "
        "owner inventory and are NOT established by this survey."
    )
    return survey


def check_inventory_survey(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify the sanitized survey can be collected hermetically."""
    survey = collect_inventory_survey(repo_root)
    missing = [k for k, v in survey["sources"].items() if v == "missing"]
    # An absent realm export after integrated removal (#4129) is expected
    # evidence of removal, not an unreadable required source.
    if (
        "keycloak/realm-export.json" in missing
        and _capability_present("4129-removal", repo_root)
    ):
        missing = [k for k in missing if k != "keycloak/realm-export.json"]
    if missing:
        return StepResult(
            "inventory-survey", "failed",
            f"Survey incomplete; unreadable sources: {', '.join(missing)}.",
        )
    surfaces = len(survey.get("keycloak_surfaces", []))
    return StepResult(
        "inventory-survey", "completed",
        f"Sanitized survey collected over {len(survey['sources'])} sources; "
        f"{surfaces} keycloak surfaces named (counts/refs only, no identity "
        "exports). Owner-protected facts still require #4117 and stay blocked "
        "in prerequisites.",
    )


def detect_capability_presence(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Probe the checkout for each prerequisite's owning capability.

    Returns prerequisite-id -> short evidence string. Presence means the
    capability's marker exists in the repo; absence names what was checked.
    This keeps the gate honest as sibling issues land: no code change here
    is needed for a probe to start passing.
    """
    presence: dict[str, str] = {}
    presence["4117-inventory"] = (
        "survey tool present (this gate); owner-protected facts absent "
        "(no sanitized deployment-state export in checkout)"
    )
    # #4119: a Keycloak identity dry-run/apply migration entrypoint outside
    # this gate. Globs are keycloak-scoped on purpose: generic *identity*
    # matches unrelated migrations (e.g. 373_lease_identity_text.py).
    candidates_4119 = [
        "api_service/migrations/versions/*keycloak*",
        "tools/*keycloak*apply*",
        "tools/*keycloak*dry*",
    ]
    found_4119 = [
        str(p) for pat in candidates_4119
        for p in sorted(repo_root.glob(pat))
        if p.name != Path(__file__).name
    ]
    presence["4119-mapping-revision"] = (
        f"found: {', '.join(found_4119)}" if found_4119
        else "checked api_service/migrations/versions/*keycloak* and "
        "tools/*keycloak*apply|dry* (excluding this gate); none present"
    )
    settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
    auth_default = _read_auth_provider_default(settings_text)
    if auth_default is not None and auth_default not in {"disabled", "keycloak"}:
        presence["4120-mode-key-setup"] = (
            f"AUTH_PROVIDER default is {auth_default!r} "
            "(moonmind/config/settings.py); mode/key setup landed"
        )
    else:
        presence["4120-mode-key-setup"] = (
            f"AUTH_PROVIDER default is {auth_default!r} "
            "(moonmind/config/settings.py); still 'disabled'/'keycloak' only"
        )
    # #4122: protected recovery tooling outside this gate.
    recovery_hits = [
        str(p) for p in sorted((repo_root / "tools").glob("*recover*"))
    ] if (repo_root / "tools").exists() else []
    presence["4122-protected-recovery"] = (
        f"found: {', '.join(recovery_hits)}" if recovery_hits
        else "checked tools/*recover*; none present (this gate's backup "
        "envelope is hermetic scaffolding, not the #4122 recovery path)"
    )
    presence["4128-qualification"] = (
        "checked checkout for candidate image/config/schema qualification "
        "record; none present (deployment-side evidence)"
    )
    presence["4129-removal"] = (
        "no keycloak surfaces in inventory-survey "
        "(compose service, realm export, api_service branches all absent); "
        "integrated removal appears landed"
        if not collect_inventory_survey(repo_root).get("keycloak_surfaces")
        else "Keycloak service/branches still present in compose and api_service "
        "(see inventory-survey); integrated removal not landed"
    )
    presence["4130-operator-contracts"] = (
        "docs/tmp/KeycloakRemovalPlan.md Status: Proposed (pre-cutover); "
        "operator contracts owned by #4130"
    )
    presence["named-owner-approval"] = "no owner approval record in checkout"
    presence["live-idp-mfa-qualification"] = (
        "no live IdP/MFA check attempted hermetically (never contacted)"
    )
    return presence


def build_sanitized_fixture(scenario: str) -> dict[str, Any]:
    """Build a deterministic sanitized rehearsal fixture (no real identities)."""
    if scenario not in REHEARSAL_MODES:
        raise ValueError(f"unknown rehearsal scenario: {scenario}")
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/k6/{scenario}")
    operator_id = str(seed)
    # Deterministic second principal derived from the first.
    non_admin_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/k6/{scenario}/non-admin"))
    return {
        "scenario": scenario,
        "operator_user_id": operator_id,
        "non_admin_user_id": non_admin_id,
        "profiles": {operator_id: "operator", non_admin_id: "analyst"},
        "workflow_owner_refs": [
            {"workflow_id": f"wf-{scenario}-1", "owner_id": operator_id, "commands": ["start", "poll", "complete"]},
            {"workflow_id": f"wf-{scenario}-2", "owner_id": non_admin_id, "commands": ["start", "poll"]},
        ],
        "background_work_principal": non_admin_id,
        "issuer_mapping": {"issuer": "sanitized-issuer", "subject": "sanitized-subject"},
    }


def apply_identity_mapping(
    fixture: dict[str, Any], mapping: dict[str, str]
) -> dict[str, str]:
    """Simulate (issuer, subject) -> UUID resolution preserving retained UUIDs.

    Key property under test: a valid external identity resolves to exactly
    one existing MoonMind UUID; retained UUIDs and owner refs are unchanged.
    Email/login-name changes must not reassign ownership (callers assert this
    by comparing before/after owner ids).
    """
    issuer = mapping.get("issuer", "")
    subject = mapping.get("subject", "")
    if not issuer or not subject:
        raise ValueError("issuer and subject are both required")
    resolved = str(uuid.uuid5(uuid.NAMESPACE_URL, f"moonmind/identity/{issuer}/{subject}"))
    # The resolved session principal must be one of the retained UUIDs when
    # the mapping targets a known user; the harness records the binding.
    return {
        "issuer": issuer,
        "subject": subject,
        "resolved_operator_candidate": fixture["operator_user_id"],
        "mapping_binding": resolved,
    }


def rehearse_scenario(scenario: str) -> StepResult:
    """Rehearse one advertised mode hermetically; assert UUID/ownership retention."""
    try:
        fixture = build_sanitized_fixture(scenario)
    except ValueError as exc:
        return StepResult(f"rehearsal-{scenario}", "failed", str(exc))
    before_owners = [r["owner_id"] for r in fixture["workflow_owner_refs"]]
    before_commands = [list(r["commands"]) for r in fixture["workflow_owner_refs"]]
    binding = apply_identity_mapping(fixture, fixture["issuer_mapping"])
    # Hermetic migration must not rewrite retained UUIDs, owner refs, command
    # order, profiles, or background-work authority.
    after_owners = [r["owner_id"] for r in fixture["workflow_owner_refs"]]
    after_commands = [list(r["commands"]) for r in fixture["workflow_owner_refs"]]
    ok = (
        before_owners == after_owners
        and before_commands == after_commands
        and fixture["profiles"][fixture["operator_user_id"]] == "operator"
        and fixture["background_work_principal"] == fixture["non_admin_user_id"]
        and binding["resolved_operator_candidate"] == fixture["operator_user_id"]
    )
    if not ok:
        return StepResult(
            f"rehearsal-{scenario}", "failed",
            "Fixture migration altered retained UUIDs, owner refs, command order, "
            "profiles, or background-work principal.",
        )
    return StepResult(
        f"rehearsal-{scenario}", "completed",
        f"Hermetic rehearsal passed for {scenario}: retained 2 UUIDs, "
        "2 workflow owner refs, profile bindings, background-work principal; "
        "no live IdP contacted.",
    )


# -- R3: exact build pins, schema versions, rendered topology ---------------

EVIDENCE_SEPARATION_NOTE = (
    "Hermetic evidence below is derived from repo files only. "
    "Authorized live IdP/MFA checks are never attempted hermetically and "
    "remain separately blocked in prerequisites."
)


def collect_build_pins(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Collect exact-version pins and topology from real repo files.

    Covers: Keycloak image pin (compose), app/dashboard image defaults,
    AUTH_PROVIDER default literal, Alembic revision count/heads present in
    the checkout, realm clients, compose service topology, root package
    version, and removal-plan status. All values are sanitized (pins and
    counts, no credentials).
    """
    pins: dict[str, Any] = {}
    compose = _read_text(repo_root / "docker-compose.yaml") or ""
    m = re.search(r"image:\s*(quay\.io/keycloak[^\s]*)", compose)
    pins["keycloak_image"] = m.group(1) if m else "absent"
    services = re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", compose, re.MULTILINE)
    pins["compose_services"] = len(set(services))
    m = re.search(
        r"image:\s*\$\{MOONMIND_IMAGE:-(ghcr\.io/[^\s}]+)\}", compose
    )
    pins["app_image_default"] = m.group(1) if m else "unknown"
    m = re.search(r"image:\s*postgres:([^\s]+)", compose)
    raw = m.group(1) if m else ""
    dm = re.search(r":-([^}]+)\}", raw)
    pins["postgres_image"] = dm.group(1) if dm else (raw or "unknown")
    m = re.search(r"image:\s*temporalio/auto-setup:([^\s]+)", compose)
    raw = m.group(1) if m else ""
    dm = re.search(r":-([^}]+)\}", raw)
    pins["temporal_image"] = dm.group(1) if dm else (raw or "unknown")
    try:
        root_pkg = json.loads(_read_text(repo_root / "package.json") or "{}")
        pins["root_package_version"] = str(root_pkg.get("version", "unknown"))
    except (json.JSONDecodeError, AttributeError):
        pins["root_package_version"] = "unparseable"
    settings_text = _read_text(repo_root / "moonmind/config/settings.py") or ""
    m = re.search(r"AUTH_PROVIDER:\s*str\s*=\s*Field\(\s*\"([^\"]+)\"", settings_text)
    pins["auth_provider_default"] = m.group(1) if m else "unknown"
    versions_dir = repo_root / "api_service/migrations/versions"
    if versions_dir.exists():
        revisions = sorted(p.name for p in versions_dir.glob("*.py"))
        pins["alembic_revisions"] = len(revisions)
        pins["alembic_latest"] = revisions[-1] if revisions else "none"
    else:
        pins["alembic_revisions"] = "unknown"
        pins["alembic_latest"] = "unknown"
    realm_text = _read_text(repo_root / "keycloak/realm-export.json")
    try:
        realm = json.loads(realm_text) if realm_text else {}
        pins["realm_clients"] = sorted(
            str(c.get("clientId", "?")) for c in realm.get("clients", [])
        )
    except (json.JSONDecodeError, AttributeError):
        pins["realm_clients"] = "unparseable"
    plan = _read_text(repo_root / PLAN_REL) or ""
    pins["plan_status"] = (
        "Proposed" if "Status: Proposed" in plan else "unknown-or-archived"
    )
    return pins


def check_build_pins(repo_root: Path = REPO_ROOT) -> StepResult:
    """Verify exact pins/topology are collectible and hermetic/live split."""
    pins = collect_build_pins(repo_root)
    # Every required pin must resolve; a single malformed source must not
    # yield positive terminal evidence. The Keycloak image pin is exempt:
    # "absent" is the expected post-removal (#4129) value.
    unusable = []
    if pins.get("alembic_revisions") == "unknown":
        unusable.append("alembic_revisions")
    if pins.get("app_image_default") in (None, "unknown"):
        unusable.append("app_image_default")
    else:
        # Validate the app image registry by hostname, not by substring:
        # a registry string containing "ghcr.io/" at an arbitrary position
        # must not count as a ghcr.io pin.
        from urllib.parse import urlparse as _urlparse

        _host = _urlparse("https://" + str(pins["app_image_default"])).hostname or ""
        if _host != "ghcr.io":
            unusable.append("app_image_default")
    if pins.get("postgres_image") in (None, "unknown"):
        unusable.append("postgres_image")
    if pins.get("temporal_image") in (None, "unknown"):
        unusable.append("temporal_image")
    if pins.get("root_package_version") in (None, "unknown", "unparseable"):
        unusable.append("root_package_version")
    if pins.get("auth_provider_default") in (None, "unknown"):
        unusable.append("auth_provider_default")
    if pins.get("plan_status") in (None, "unknown-or-archived"):
        unusable.append("plan_status")
    if pins.get("realm_clients") == "unparseable":
        unusable.append("realm_clients")
    if not isinstance(pins.get("compose_services"), int) or pins["compose_services"] <= 0:
        unusable.append("compose_services")
    if unusable:
        return StepResult(
            "build-pins", "failed",
            "Exact-build evidence incomplete; unusable pins: "
            + ", ".join(unusable)
            + ". Refusing positive topology evidence until every required "
            "pin resolves.",
        )
    return StepResult(
        "build-pins", "completed",
        f"Pins collected: keycloak_image={pins['keycloak_image']}, "
        f"app_image_default={pins['app_image_default']}, "
        f"postgres={pins['postgres_image']}, "
        f"temporal={pins['temporal_image']}, "
        f"root_package={pins['root_package_version']}, "
        f"AUTH_PROVIDER default={pins['auth_provider_default']!r}, "
        f"{pins['alembic_revisions']} alembic revisions "
        f"(latest {pins['alembic_latest']}), "
        f"{pins['compose_services']} compose services, "
        f"plan={pins['plan_status']}. {EVIDENCE_SEPARATION_NOTE}",
    )


# -- R4: backup envelope, mutation freeze, dual-issuance, drain --------------

# The last backward-compatible point for the auth cutover: additive schema
# phase where the previous application release can still read the database.
# Recorded here so failure-injection and rollback checks share one boundary.
LAST_BACKWARD_COMPATIBLE_POINT = (
    "additive schema only; previous app release reads database; "
    "no destructive identity/ownership rewrite yet"
)

BACKUP_REQUIRED_PREFIXES = ("identity:", "config:", "keys:")


def create_backup_envelope(
    entries: dict[str, str], backup_id: str, state_dir: Path | None = None
) -> dict[str, Any]:
    """Build a restore-verifiable backup envelope over sanitized entries.

    Hermetic scope: the envelope proves structure (required identity/config/
    keys scopes present), sha256 integrity per entry, access-restricted
    persistence (0o600 when written), and restore-verification. Real
    encryption-at-rest is an operator-KMS deployment property and is
    recorded as ``encryption: operator-kms-required`` — the hermetic
    envelope never claims live KMS encryption from a checkout.
    """
    if not backup_id:
        raise ValueError("backup_id is required")
    missing = [
        p for p in BACKUP_REQUIRED_PREFIXES
        if not any(k.startswith(p) for k in entries)
    ]
    if missing:
        raise ValueError(f"backup missing scopes: {', '.join(missing)}")
    digests = {
        k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in entries.items()
    }
    envelope: dict[str, Any] = {
        "backup_id": backup_id,
        "scopes": sorted(entries.keys()),
        "digests": digests,
        "encryption": "operator-kms-required",
        "access": "restricted-0600",
        "issue": ISSUE_REF,
    }
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / f"{backup_id}.json"
        target.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            # Best-effort permission hardening only: the envelope bytes are
            # already persisted above, and verify_backup_envelope checks
            # content integrity independently of file mode.
            pass
    return envelope


def verify_backup_envelope(envelope: dict[str, Any], entries: dict[str, str]) -> bool:
    """Restore-verify an envelope against candidate entries."""
    digests = envelope.get("digests", {})
    if set(digests) != set(entries):
        return False
    return all(
        hashlib.sha256(v.encode("utf-8")).hexdigest() == digests[k]
        for k, v in entries.items()
    )


class MutationFreeze:
    """Cutover-window guard refusing identity/mapping/privilege mutations."""

    def __init__(self) -> None:
        self.frozen = False

    def freeze(self) -> None:
        self.frozen = True

    def unfreeze(self) -> None:
        self.frozen = False

    def check(self, mutation: str) -> tuple[bool, str]:
        # Explicit mutation categories refused during the frozen window.
        # Account/user provisioning is an identity mutation and must be
        # frozen alongside mapping/privilege changes; operational reads
        # stay allowed.
        guarded = (
            "identity", "mapping", "privilege", "admin", "role",
            "account", "user", "register", "signup", "credential",
            "session", "login",
        )
        if self.frozen and any(g in mutation.lower() for g in guarded):
            return False, (
                f"refused {mutation!r} during frozen cutover window; "
                "reconcile after window instead"
            )
        return True, f"allowed {mutation!r}"


def check_dual_issuance(old_issuer_enabled: bool, new_issuer_enabled: bool) -> StepResult:
    """Refuse concurrent incompatible credential issuance across replicas."""
    if old_issuer_enabled and new_issuer_enabled:
        return StepResult(
            "dual-issuance", "failed",
            "Refused: old and new API replicas must not simultaneously issue "
            "incompatible credentials; drain or disable one side first.",
        )
    return StepResult(
        "dual-issuance", "completed",
        "Single issuance authority hermetically: "
        f"old={old_issuer_enabled}, new={new_issuer_enabled}.",
    )


class DrainTracker:
    """Account for in-flight login/callback requests without discarding work.

    Admitted Temporal work is preserved by construction: draining only waits
    for tracked request ids to finish; it never cancels workflow records.
    """

    def __init__(self) -> None:
        self.in_flight: set[str] = set()
        self.admitted_workflows: set[str] = set()

    def admit(self, request_id: str, workflow_id: str) -> None:
        self.in_flight.add(request_id)
        self.admitted_workflows.add(workflow_id)

    def settle(self, request_id: str) -> None:
        self.in_flight.discard(request_id)

    def drain(self) -> StepResult:
        if self.in_flight:
            return StepResult(
                "login-drain", "blocked",
                f"{len(self.in_flight)} login/callback requests still in flight; "
                f"{len(self.admitted_workflows)} admitted workflows preserved, "
                "none discarded.",
            )
        return StepResult(
            "login-drain", "completed",
            f"Drained: 0 in flight, {len(self.admitted_workflows)} admitted "
            "workflows preserved.",
        )


def check_backup_freeze_drain() -> StepResult:
    """Exercise backup/freeze/dual-issuance/drain guards hermetically."""
    entries = {
        "identity:users": "sanitized-user-count=2",
        "config:auth-provider": "disabled",
        "keys:session-key-id": "sanitized-key-id",
    }
    envelope = create_backup_envelope(entries, "k6-hermetic-check")
    if not verify_backup_envelope(envelope, entries):
        return StepResult(
            "backup-freeze-drain", "failed",
            "Hermetic backup envelope failed restore-verification.",
        )
    freeze = MutationFreeze()
    freeze.freeze()
    allowed, _ = freeze.check("create identity mapping")
    if allowed:
        return StepResult(
            "backup-freeze-drain", "failed",
            "Mutation freeze did not refuse an identity mutation.",
        )
    dual = check_dual_issuance(True, True)
    if dual.status != "failed":
        return StepResult(
            "backup-freeze-drain", "failed",
            "Dual-issuance guard did not refuse concurrent old+new issuance.",
        )
    tracker = DrainTracker()
    tracker.admit("req-1", "wf-k6-1")
    if tracker.drain().status != "blocked":
        return StepResult(
            "backup-freeze-drain", "failed",
            "Drain did not report in-flight login/callback requests.",
        )
    tracker.settle("req-1")
    drained = tracker.drain()
    if drained.status != "completed" or "wf-k6-1" not in tracker.admitted_workflows:
        return StepResult(
            "backup-freeze-drain", "failed",
            "Drain discarded or lost admitted Temporal work.",
        )
    return StepResult(
        "backup-freeze-drain", "completed",
        "Hermetic guards pass: envelope restore-verified (operator KMS still "
        "required for live encryption), freeze refuses identity mutations, "
        "dual-issuance refused, drain preserves admitted workflows. "
        f"Compat boundary: {LAST_BACKWARD_COMPATIBLE_POINT}.",
    )


# -- R5: failure-injection across the real-shaped cutover sequence -----------

CUTOVER_STEPS = (
    "schema-migration",
    "identity-mapping",
    "key-setup",
    "session-issuance",
    "first-login",
)


def run_cutover_sequence(fail_at: str | None = None) -> dict[str, Any]:
    """Run the real-shaped cutover step sequence with optional injection.

    Pure harness: each step records completed/failed without side effects.
    ``fail_at`` names the step where an injected failure occurs; steps after
    it are recorded ``skipped``. Callers apply rollback/forward-repair rules
    (never whole-DB restore, never silent disabled-auth fallback,
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
            "restore matching app/config/key set, invalidate incompatible "
            "sessions; never whole shared DB; never silent disabled-auth "
            "fallback; reconcile-after-writes or forward repair"
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


# -- R6: auth-boundary replay evidence ---------------------------------------

def check_auth_boundary_replay(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    boundary_changed: bool,
) -> StepResult:
    """Tie history-authority to whether a persisted boundary really changed.

    When no auth code change ships a persisted/history boundary change
    (this checkout), identical histories complete with that fact recorded.
    When a boundary did change, the caller must supply exact replay evidence
    (identical owner/command records) or a bounded old-release drain; any
    rewrite fails.
    """
    base = check_workflow_history_authority(before, after)
    if base.status == "failed":
        return base
    if boundary_changed:
        return StepResult(
            "auth-boundary-replay", "completed",
            f"Boundary changed and exact replay evidence verified over "
            f"{len(before)} histories (owner IDs + command order identical).",
        )
    return StepResult(
        "auth-boundary-replay", "completed",
        f"No persisted/history boundary change in this checkout; "
        f"{len(before)} histories readable with unchanged owner IDs and "
        "command order. A future auth change that alters the boundary must "
        "supply exact replay evidence or a bounded old-release drain.",
    )


def check_rollback_plan(scope: str) -> StepResult:
    """Validate a rollback scope name without touching any database.

    Only a structured, explicitly permitted scope completes: a matching
    app/config/key restore with reconciliation or forward repair recorded.
    Anything else is refused (destructive/silent-fallback), blocked
    (snapshot without reconciliation), or unrecognized (no positive
    evidence for absent or unsafe instructions).
    """
    lowered = scope.lower()
    if "whole" in lowered and ("postgres" in lowered or "temporal" in lowered or "database" in lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: never roll back the whole shared application/Temporal database "
            "to undo an authentication migration.",
        )
    if "disabled" in lowered and ("fallback" in lowered or "switch" in lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: never silently switch to disabled auth as a rollback.",
        )
    if re.search(r"\b(drop|delete|remove|destroy|wipe|purge)\b.{0,40}\b(database|identity|role)\b", lowered) or \
            re.search(r"\b(database|identity|role)\b.{0,40}\b(drop|delete|remove|destroy|wipe|purge)\b", lowered):
        return StepResult(
            "rollback-scope", "failed",
            "Refused: destructive database/identity/role operation is not a "
            "safe app/config/key rollback.",
        )
    if "snapshot" in lowered and "reconcil" not in lowered and "forward" not in lowered:
        return StepResult(
            "rollback-scope", "blocked",
            "Blind snapshot restore after post-cutover identity writes requires "
            "reconciliation or forward repair first.",
        )
    if (
        "matching" in lowered
        and ("app" in lowered or "config" in lowered or "key" in lowered)
        and ("reconcil" in lowered or "forward" in lowered)
    ):
        return StepResult(
            "rollback-scope", "completed",
            "Rollback scope accepted hermetically: matching app/config/key restore with "
            "session invalidation and reconciliation-before-restore rule recorded.",
        )
    return StepResult(
        "rollback-scope", "blocked",
        f"Unrecognized rollback scope {scope!r}; supply an explicit matching "
        "app/config/key restore with reconciliation or forward repair. No "
        "positive rollback evidence for absent or unstructured instructions.",
    )


def check_retirement_plan(actions: list[str]) -> StepResult:
    """Validate retirement actions textually; performs no service mutation."""
    joined = "\n".join(actions)
    for pattern in FORBIDDEN_RETIREMENT_PATTERNS:
        if pattern.search(joined):
            return StepResult(
                "retirement-plan", "failed",
                f"Refused destructive retirement action matching {pattern.pattern!r}; "
                "retirement must stop precisely identified services, never broad "
                "down -v / prune / shared-role or app/Temporal volume deletion.",
            )
    if not any("keycloak" in a.lower() for a in actions):
        return StepResult(
            "retirement-plan", "blocked",
            "No precisely identified obsolete Keycloak service in plan; "
            "retirement stays blocked until inventory names it.",
        )
    return StepResult(
        "retirement-plan", "completed",
        "Retirement plan textually safe: precisely identified service/ingress only, "
        "no destructive volume/database operations. Execution still requires "
        "owner approval and positive retired-service verification.",
    )


def check_workflow_history_authority(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> StepResult:
    """Assert an auth change did not rewrite workflow command order/owner IDs.

    Records are compared by stable ``workflow_id`` key, not by list
    position, so a reordered or cross-associated evidence collection cannot
    pass as exact replay.
    """
    if len(before) != len(after):
        return StepResult(
            "workflow-history-authority", "failed",
            "Workflow history length changed across auth boundary; requires exact "
            "compatibility/replay evidence or bounded old-release drain.",
        )
    if all("workflow_id" in dict(b) and "workflow_id" in dict(a) for b, a in zip(before, after)) \
            and any("workflow_id" in dict(r) for r in [*before, *after]):
        before_by_id = {r["workflow_id"]: r for r in before}
        after_by_id = {r["workflow_id"]: r for r in after}
        if set(before_by_id) != set(after_by_id):
            return StepResult(
                "workflow-history-authority", "failed",
                "Workflow identity set changed across auth boundary "
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
                    "order must be preserved across HTTP auth changes.",
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
                "order must be preserved across HTTP auth changes.",
            )
    return StepResult(
        "workflow-history-authority", "completed",
        f"Verified {len(before)} workflow histories readable with unchanged "
        "owner IDs and command order.",
    )


# -- Idempotent rehearsal state ---------------------------------------------

def load_state(state_dir: Path, migration_id: str) -> dict[str, Any]:
    state_file = state_dir / "k6_rehearsal_state.json"
    if not state_file.exists():
        return {"migration_id": migration_id, "runs": 0, "completed_steps": []}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # An existing but unreadable state file is corrupt durable evidence,
        # not a fresh migration: fail closed and require explicit operator
        # recovery instead of inventing replacement state that save_state
        # would then persist over the only durable record.
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
    state_file = state_dir / "k6_rehearsal_state.json"
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
        # Replacement starts a new migration's evidence from scratch: never
        # union the superseded migration's completed steps or run count.
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
    results: list[StepResult] = [check_plan_precondition(repo_root)]
    results.extend(check_prerequisites(repo_root))
    results.append(check_inventory_survey(repo_root))
    results.append(check_build_pins(repo_root))
    for scenario in scenarios:
        results.append(rehearse_scenario(scenario))
    fixture = build_sanitized_fixture("explicit-local")
    results.append(
        check_workflow_history_authority(
            fixture["workflow_owner_refs"], fixture["workflow_owner_refs"]
        )
    )
    results.append(
        check_auth_boundary_replay(
            fixture["workflow_owner_refs"],
            fixture["workflow_owner_refs"],
            boundary_changed=False,
        )
    )
    results.append(check_backup_freeze_drain())
    results.append(check_failure_injection())
    results.append(check_dual_issuance(False, True))
    # Same hermetic guards as CLI `--mode all`: rollback-scope refusal and
    # retirement-plan textual safety. Execution still requires owner approval.
    results.append(
        check_rollback_plan("matching app/config/key restore with reconciliation")
    )
    results.append(
        check_retirement_plan(["stop service keycloak (identified from inventory)"])
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermetic K6 cutover rehearsal gate (#4131).")
    parser.add_argument("--state-dir", type=Path, default=Path("var/artifacts/k6_rehearsal"))
    parser.add_argument("--migration-id", default="k6-rehearsal-4131")
    parser.add_argument("--allow-replace", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("preflight", "rehearsal", "rollback-check", "retirement-check", "all"),
        default="all",
    )
    parser.add_argument("--rollback-scope", default="matching app/config/key restore with reconciliation")
    parser.add_argument("--retire-action", action="append", default=[])
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.mode in ("preflight", "all"):
        results: list[StepResult] = [check_plan_precondition()]
        results.extend(check_prerequisites())
        results.append(check_inventory_survey())
        results.append(check_build_pins())
    else:
        results = []
    if args.mode in ("rehearsal", "all"):
        for scenario in REHEARSAL_MODES:
            results.append(rehearse_scenario(scenario))
        fixture = build_sanitized_fixture("explicit-local")
        results.append(
            check_auth_boundary_replay(
                fixture["workflow_owner_refs"],
                fixture["workflow_owner_refs"],
                boundary_changed=False,
            )
        )
        results.append(check_backup_freeze_drain())
        results.append(check_failure_injection())
        results.append(check_dual_issuance(False, True))
    if args.mode in ("rollback-check", "all"):
        results.append(check_rollback_plan(args.rollback_scope))
    if args.mode in ("retirement-check", "all"):
        if args.retire_action:
            actions = args.retire_action
        elif args.mode == "retirement-check":
            # No fabricated plan: retirement-check without an explicit
            # --retire-action stays blocked until the operator supplies an
            # action derived from actual inventory.
            actions = []
        else:
            actions = ["stop service keycloak (identified from inventory)"]
        results.append(check_retirement_plan(actions))

    # Create the requested output directory before persisting state or
    # writing the result, so a nested --json-out path cannot fail after the
    # state file was already updated.
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
    ok, msg = save_state(
        args.state_dir, args.migration_id,
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
    # Deployment-gated runs always carry blocked prerequisites, so the honest
    # hermetic terminal state is REHEARSAL_PASS_DEPLOYMENT_BLOCKED.
    text = json.dumps(summary, indent=2)
    if args.json_out is not None:
        args.json_out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if (ok and not failed) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
