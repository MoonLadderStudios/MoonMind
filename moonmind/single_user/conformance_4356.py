"""MoonLadderStudios/MoonMind#4356 R1: ten-row single-user conformance mapping.

Maps every docs/SingleUserApplicationDesign.md section 10 outcome row to
concrete existing/new tests and the owning feature issue. Reuses unit,
PostgreSQL integration, Temporal replay/boundary, Compose, and browser
infrastructure without adding a test framework or requiring paid providers,
live IdPs, or production credentials.

Fixture/selector work owned by this issue (#4356) can proceed immediately.
Hermetic subsets of the journey rows are proven in-process against the real
admission/capability boundaries (see tests/unit/single_user/test_*_4356.py);
full journey evidence for the cohort-owned rows consumes the integrated
#4346-4355 candidate (only #4349 is merged at the time of writing); those
rows report ``pending_cohort`` honestly rather than claiming coverage.
Cohort-future integration suites are listed under the row's
``planned_tests`` field -- never under ``new_tests``, whose refs must
resolve to files that exist so a referenced suite cannot silently be
skipped.
"""

from __future__ import annotations

DESIGN_REF = "docs/SingleUserApplicationDesign.md#section-10"
ISSUE_REF = "MoonLadderStudios/MoonMind#4356"
COHORT_REF = "MoonLadderStudios/MoonMind#4346-4355"

CONFORMANCE_ROWS: tuple[dict, ...] = (
    {
        "id": "fresh_local",
        "scenario": "Fresh local instance",
        "required_outcome": (
            "Dashboard and ordinary operator actions work without account "
            "setup, login, user seeding, or an identity service."
        ),
        "owner": "MoonLadderStudios/MoonMind#4356 (fixture/selector now); "
        "final journey consumes MoonLadderStudios/MoonMind#4346-4355",
        "status": "partial",
        "existing_tests": (
            "tests/integration/test_startup_profile_seeding.py",
            "tests/unit/tools/test_select_test_suites.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_structural_simplification_4356.py",
            "tests/unit/single_user/test_first_run_4356.py",
        ),
    },
    {
        "id": "eligible_upgrade",
        "scenario": "Eligible single-operator populated instance",
        "required_outcome": (
            "Retained resources, effective settings, preset versions, and "
            "credential bindings survive without owner-based visibility, "
            "including proven same-person aliases and collision handling."
        ),
        "owner": "MoonLadderStudios/MoonMind#4346-4355 cohort "
        "(conversion owners); mapping by MoonLadderStudios/MoonMind#4356",
        "status": "pending_cohort",
        "existing_tests": (
            "tests/auth/test_single_user_4349_boundaries.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_structural_simplification_4356.py",
        ),
    },
    {
        "id": "blocked_multi",
        "scenario": "Multiple people or unresolved attribution",
        "required_outcome": (
            "Conversion is blocked before source mutation or access cutover, "
            "without exposing, combining, exporting, or deleting another "
            "person's data; the protected source release and admitted work "
            "remain available."
        ),
        "owner": "MoonLadderStudios/MoonMind#4346-4355 cohort; "
        "mapping by MoonLadderStudios/MoonMind#4356",
        "status": "pending_cohort",
        "existing_tests": (
            "tests/unit/security/test_container_job_capabilities.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_protected_ingress_4356.py",
        ),
        "planned_tests": (
            "tests/integration/single_user/test_blocked_conversion_4356.py",
        ),
    },
    {
        "id": "stale_decision",
        "scenario": "Source changes during conversion",
        "required_outcome": (
            "The candidate is not exposed using a stale eligibility decision; "
            "inconsistent conversion has no partially published result."
        ),
        "owner": "MoonLadderStudios/MoonMind#4346-4355 cohort; "
        "mapping by MoonLadderStudios/MoonMind#4356",
        "status": "pending_cohort",
        "existing_tests": (
            "tests/unit/workflows/temporal/test_run_replayer.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_machine_authority_4356.py",
        ),
        "planned_tests": (
            "tests/integration/single_user/test_stale_eligibility_4356.py",
        ),
    },
    {
        "id": "remote_access",
        "scenario": "Approved remote access",
        "required_outcome": (
            "The configured operator URL works through its protected boundary "
            "without a local user registry."
        ),
        "owner": "MoonLadderStudios/MoonMind#4356 (ingress fixture/selector); "
        "successor scope from MoonLadderStudios/MoonMind#4128",
        "status": "partial",
        "existing_tests": (
            "tests/unit/security/test_container_job_capabilities.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_structural_simplification_4356.py",
            "tests/unit/single_user/test_protected_ingress_4356.py",
        ),
    },
    {
        "id": "bypass_denied",
        "scenario": "Unapproved or bypass access",
        "required_outcome": (
            "Direct-backend, forged-header, hostile-origin, and "
            "invalid-credential requests cannot acquire operator authority."
        ),
        "owner": "MoonLadderStudios/MoonMind#4356 (ingress matrix early work); "
        "successor scope from MoonLadderStudios/MoonMind#4128",
        "status": "partial",
        "existing_tests": (
            "tests/unit/security/test_container_job_capabilities.py",
            "tests/unit/security/test_execution_fanout_capabilities.py",
            "tests/unit/security/test_session_authority_4121.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_selector_4356.py",
            "tests/unit/single_user/test_protected_ingress_4356.py",
        ),
    },
    {
        "id": "machine_scope",
        "scenario": "Scoped machine execution",
        "required_outcome": (
            "Correctly scoped operations work, while unrelated resources and "
            "operator-only actions remain denied."
        ),
        "owner": "MoonLadderStudios/MoonMind#4356 (authority matrix early work)",
        "status": "partial",
        "existing_tests": (
            "tests/unit/security/test_container_job_capabilities.py",
            "tests/unit/security/test_execution_fanout_capabilities.py",
            "tests/unit/security/test_session_authority_4121.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_selector_4356.py",
            "tests/unit/single_user/test_machine_authority_4356.py",
        ),
    },
    {
        "id": "concurrency_restart",
        "scenario": "Concurrent use and restart",
        "required_outcome": (
            "Multiple tabs, workers, schedules, and independent deployments "
            "retain concurrency and recovery behavior."
        ),
        "owner": "MoonLadderStudios/MoonMind#4346-4355 cohort; "
        "mapping by MoonLadderStudios/MoonMind#4356",
        "status": "pending_cohort",
        "existing_tests": (
            "tools/run_omnigent_concurrency_qualification.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_machine_authority_4356.py",
        ),
        "planned_tests": (
            "tests/integration/single_user/test_concurrency_restart_4356.py",
        ),
    },
    {
        "id": "history_replay",
        "scenario": "Retained execution history",
        "required_outcome": (
            "In-flight work and historical payloads remain compatible through "
            "the established replay or cutover path."
        ),
        "owner": "MoonLadderStudios/MoonMind#4346-4355 cohort; "
        "mapping by MoonLadderStudios/MoonMind#4356",
        "status": "pending_cohort",
        "existing_tests": (
            "tests/unit/workflows/temporal/test_run_replayer.py",
        ),
        "planned_tests": (
            "tests/integration/single_user/test_history_replay_4356.py",
        ),
    },
    {
        "id": "structural_simplification",
        "scenario": "Structural simplification",
        "required_outcome": (
            "Ordinary service calls, queries, caches, and startup no longer "
            "depend on human accounts, roles, or a default principal."
        ),
        "owner": "MoonLadderStudios/MoonMind#4356 (introspection regressions now)",
        "status": "partial",
        "existing_tests": (
            "tests/auth/test_single_user_4349_boundaries.py",
        ),
        "new_tests": (
            "tests/unit/single_user/test_structural_simplification_4356.py",
            "tests/unit/single_user/test_conformance_mapping_4356.py",
            "tests/unit/single_user/test_selector_4356.py",
        ),
    },
)


def row_ids() -> tuple[str, ...]:
    """Return the stable section-10 row identifiers in design order."""
    return tuple(row["id"] for row in CONFORMANCE_ROWS)


def owner_for(row_id: str) -> str:
    """Return the owning issue reference for a conformance row."""
    for row in CONFORMANCE_ROWS:
        if row["id"] == row_id:
            return str(row["owner"])
    raise KeyError(row_id)
