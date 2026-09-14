"""Removed first-run keys fail through the obsolete-configuration lifecycle.

Source issue: MoonLadderStudios/MoonMind#3941 (acceptance REQ-05).

The consumer inventory (``tools/config_consumer_inventory.py``,
``config/consumer_inventory.json``) proves the retired identities below have
no Compose, Dockerfile, shell, Python, or settings-catalog consumer, and
``.env-template`` no longer documents them. A deployment that still supplies
them must get an actionable startup failure or deprecation warning through
the existing ``enforce_obsolete_configuration_at_startup`` mechanism at API
and worker startup — never silent acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path

from moonmind.omnigent.legacy_retirement import (
    RETIREMENT_INVENTORY,
    ComponentFamily,
    ObsoleteConfigurationError,
    RemovalStage,
    RetirementClass,
    assert_obsolete_configuration,
    enforce_obsolete_configuration_at_startup,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

REMOVED_FIRST_RUN_KEYS = ("LocalData",)
DEPRECATED_FIRST_RUN_KEYS = (
    "TEMPORAL_POSTGRES_VERSION",
    "MOONMIND_GEMINI_CAPACITY_RETRY_MAX_ATTEMPTS",
    "MOONMIND_GEMINI_CAPACITY_RETRY_BASE_DELAY_SECONDS",
    "MOONMIND_GEMINI_CAPACITY_RETRY_MAX_DELAY_SECONDS",
)


def _template_assignments() -> set[str]:
    names: set[str] = set()
    for line in (REPO_ROOT / ".env-template").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.add(match.group(1))
    return names


def test_removed_first_run_keys_are_gone_from_the_template():
    assignments = _template_assignments()
    for name in REMOVED_FIRST_RUN_KEYS + DEPRECATED_FIRST_RUN_KEYS:
        assert name not in assignments, (
            f"{name} is retired and must not stay documented as active"
        )


def test_removed_first_run_key_fails_startup():
    try:
        assert_obsolete_configuration({"LocalData": "stale-value"})
    except ObsoleteConfigurationError as exc:
        assert "LocalData" in str(exc)
        assert "omnigent.legacy.first_run_dead_configuration" in str(exc)
    else:
        raise AssertionError("removed LocalData must fail startup")


def test_unset_or_empty_removed_key_passes_startup():
    assert assert_obsolete_configuration({}) == ()
    assert assert_obsolete_configuration({"LocalData": ""}) == ()
    assert assert_obsolete_configuration({"LocalData": "   "}) == ()


def test_deprecated_first_run_keys_warn_with_replacement_guidance():
    for name in DEPRECATED_FIRST_RUN_KEYS:
        warnings = assert_obsolete_configuration({name: "stale-value"})
        assert warnings, f"{name} must warn during its deprecation window"
        assert name in warnings[0]
        assert "omnigent.legacy.first_run_dead_configuration" in warnings[0]
        assert assert_obsolete_configuration({}) == ()


def test_first_run_retirement_row_is_owned_and_staged():
    rows = {
        path.path_id: path
        for path in RETIREMENT_INVENTORY
        if path.path_id == "omnigent.legacy.first_run_dead_configuration"
    }
    assert len(rows) == 1
    row = rows["omnigent.legacy.first_run_dead_configuration"]
    assert row.owner.strip()
    assert row.family is ComponentFamily.ENVIRONMENT_AND_PERSISTED_BOOTSTRAP
    assert row.retirement_class is RetirementClass.ELIGIBLE_FOR_REMOVAL
    assert row.earliest_removal_stage is RemovalStage.STARTUP_AND_COMPOSE
    assert not row.new_admission_source.strip()


class _RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args if args else message)


def test_startup_enforcement_reports_through_the_shared_entrypoint():
    log = _RecordingLog()
    warnings = enforce_obsolete_configuration_at_startup(
        log,
        env={"TEMPORAL_POSTGRES_VERSION": "17"},
    )
    assert warnings
    assert log.warnings and "TEMPORAL_POSTGRES_VERSION" in log.warnings[0]


def test_api_and_worker_startups_share_obsolete_enforcement():
    api_main = (REPO_ROOT / "api_service" / "main.py").read_text(encoding="utf-8")
    worker_runtime = (
        REPO_ROOT / "moonmind" / "workflows" / "temporal" / "worker_runtime.py"
    ).read_text(encoding="utf-8")
    assert "enforce_obsolete_configuration_at_startup" in api_main
    assert "enforce_obsolete_configuration_at_startup" in worker_runtime
