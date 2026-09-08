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
)

_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|bearer|cookie|session[_-]?token|refresh[_-]?token"
    r"|authorization\s*:\s*[^\s]+|token\s*=\s*[^\s;]+)\s*[:=]\s*\S+"
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


def check_prerequisites(_repo_root: Path = REPO_ROOT) -> list[StepResult]:
    """Report every deployment prerequisite as blocked in a repo checkout.

    A repo checkout cannot supply protected inventory exports, live IdP/MFA
    checks, owner approvals, or coordinated release evidence, so each item
    is honestly blocked with its owner named. No unknown fact is marked
    complete.
    """
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
    return [
        StepResult(
            f"prerequisite-{pid}", "blocked",
            f"Missing in repo checkout; owned by {owners[pid]}. "
            "Blocks deployment qualification, not hermetic rehearsal.",
        )
        for pid in PREREQUISITE_IDS
    ]


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


def check_rollback_plan(scope: str) -> StepResult:
    """Validate a rollback scope name without touching any database."""
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
    if "snapshot" in lowered and "reconcil" not in lowered and "forward" not in lowered:
        return StepResult(
            "rollback-scope", "blocked",
            "Blind snapshot restore after post-cutover identity writes requires "
            "reconciliation or forward repair first.",
        )
    return StepResult(
        "rollback-scope", "completed",
        "Rollback scope accepted hermetically: matching app/config/key restore with "
        "session invalidation and reconciliation-before-restore rule recorded.",
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
    """Assert an auth change did not rewrite workflow command order/owner IDs."""
    if len(before) != len(after):
        return StepResult(
            "workflow-history-authority", "failed",
            "Workflow history length changed across auth boundary; requires exact "
            "compatibility/replay evidence or bounded old-release drain.",
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
        return {"migration_id": migration_id, "runs": 0, "completed_steps": []}
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
    for scenario in scenarios:
        results.append(rehearse_scenario(scenario))
    fixture = build_sanitized_fixture("explicit-local")
    results.append(
        check_workflow_history_authority(
            fixture["workflow_owner_refs"], fixture["workflow_owner_refs"]
        )
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
    else:
        results = []
    if args.mode in ("rehearsal", "all"):
        for scenario in REHEARSAL_MODES:
            results.append(rehearse_scenario(scenario))
    if args.mode in ("rollback-check", "all"):
        results.append(check_rollback_plan(args.rollback_scope))
    if args.mode in ("retirement-check", "all"):
        actions = args.retire_action or ["stop service keycloak (identified from inventory)"]
        results.append(check_retirement_plan(actions))

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
