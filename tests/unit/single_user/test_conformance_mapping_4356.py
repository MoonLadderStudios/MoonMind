"""MoonLadderStudios/MoonMind#4356 R1: ten-row conformance mapping is complete.

The accepted design (docs/SingleUserApplicationDesign.md section 10) defines
ten observable acceptance rows. This test pins the machine-readable mapping
in moonmind.single_user.conformance_4356 to those rows: exactly ten rows,
stable IDs, each with a scenario, required outcome, feature owner, and at
least one concrete existing or new test reference. It checks structure and
issue references behaviorally (no wording match on the design prose).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_ROW_IDS = (
    "fresh_local",
    "eligible_upgrade",
    "blocked_multi",
    "stale_decision",
    "remote_access",
    "bypass_denied",
    "machine_scope",
    "concurrency_restart",
    "history_replay",
    "structural_simplification",
)


def test_mapping_has_ten_rows_with_stable_ids() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    rows = mapping.CONFORMANCE_ROWS
    assert len(rows) == 10
    assert tuple(row["id"] for row in rows) == EXPECTED_ROW_IDS


def test_each_row_names_owner_and_concrete_tests() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    for row in mapping.CONFORMANCE_ROWS:
        assert row["scenario"], row["id"]
        assert row["required_outcome"], row["id"]
        assert row["owner"], row["id"]
        # Every row must point at concrete test coverage: either an existing
        # suite that already exercises the boundary, or a new test added for
        # this program. An empty pair means the mapping is aspirational.
        existing = list(row.get("existing_tests", ()))
        new_tests = list(row.get("new_tests", ()))
        assert existing or new_tests, row["id"]
        for ref in (*existing, *new_tests):
            assert ref, row["id"]


def test_existing_test_refs_resolve_to_repo_paths() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    for row in mapping.CONFORMANCE_ROWS:
        for ref in row.get("existing_tests", ()):
            # Refs are "path[:node]" so parameterized cases stay addressable.
            path_part = ref.split("::")[0]
            assert (REPO_ROOT / path_part).exists(), (row["id"], ref)


def test_new_test_refs_resolve_to_repo_paths() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    for row in mapping.CONFORMANCE_ROWS:
        for ref in row.get("new_tests", ()):
            # Same "path[:node]" convention as existing_tests: a new_tests
            # ref must point at a file that exists, so a referenced suite
            # cannot silently be skipped. Cohort-future suites belong in
            # the row's planned_tests field, not in new_tests.
            path_part = ref.split("::")[0]
            assert (REPO_ROOT / path_part).exists(), (row["id"], ref)


def test_mapping_uses_no_second_framework_or_live_credentials() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    for row in mapping.CONFORMANCE_ROWS:
        for ref in (
            *row.get("existing_tests", ()),
            *row.get("new_tests", ()),
            *row.get("planned_tests", ()),
        ):
            lowered = ref.lower()
            assert "provider_verification" not in lowered, (row["id"], ref)
            assert "requires_credentials" not in lowered, (row["id"], ref)
        # Owners must reference the single-user program issues, not an
        # external test framework or live environment.
        assert "4356" in row["owner"] or "4346" in row["owner"] or "4128" in row["owner"], row["id"]


def test_mapping_distinguishes_ready_from_cohort_blocked() -> None:
    from moonmind.single_user import conformance_4356 as mapping

    statuses = {row["id"]: row["status"] for row in mapping.CONFORMANCE_ROWS}
    # Fixture/selector work owned by this issue is implementable now.
    assert statuses["fresh_local"] in {"ready", "partial"}
    assert statuses["structural_simplification"] in {"ready", "partial"}
    # Full journey evidence consumes the integrated #4346-4355 cohort, so at
    # least one row must honestly report that it waits on that cohort.
    assert any(
        "4346" in row["owner"] and row["status"] == "pending_cohort"
        for row in mapping.CONFORMANCE_ROWS
    ), statuses
