"""Operator-visible runtime-provider migration status and telemetry.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 10 and 11).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.runtime_provider_migration_status import (
    RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION,
    build_runtime_provider_migration_status,
)
from moonmind.omnigent.runtime_provider_rollout import (
    RUNTIME_PROVIDER_ROLLBACK_ENV,
    RolloutState,
    RuntimeProviderRollbackControl,
    default_runtime_provider_rollout_policy,
)
from tests.unit.omnigent.support_evidence_fixtures import (
    concurrency_record,
    support_plan,
    support_row,
)

_CODEX_GATE = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
_CLAUDE_GATE = "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED"


def _policy(env: dict[str, str] | None = None):
    return default_runtime_provider_rollout_policy(env=env or {})


@pytest.fixture(autouse=True)
def _reset_metrics():
    control_plane_metrics.reset()
    yield
    control_plane_metrics.reset()


def test_status_reports_every_registered_combination_with_exact_dimensions():
    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true", _CLAUDE_GATE: "true"})
    )
    assert status.schema_version == RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION
    rows = {row.target_id: row for row in status.combinations}
    assert set(rows) == {
        "codex.generic-omnigent",
        "codex.legacy-profile-bound-omnigent",
        "claude.generic-omnigent",
        "claude.direct",
        "codex.direct",
        "opencode.generic-omnigent",
    }
    codex = rows["codex.generic-omnigent"]
    assert codex.rollout_state is RolloutState.new_work_default
    assert codex.default_status == "default_for_new_work"
    assert codex.compatibility_path_status == "not_a_compatibility_path"
    assert codex.harness_id == "codex-native"
    assert codex.host_class_ref == "omnigent-codex@1"
    assert codex.runtime_pack_ref == "codex-native-pack@1"
    assert codex.credential_materializer_ref == "codex-oauth-home@1"
    assert codex.execution_realizer_ref == "generic-omnigent-host@1"
    assert codex.agent_profile_compatibility_class == (
        "moonmind.omnigent-agent-profile.v2"
    )
    assert "linux/amd64" in codex.architectures
    assert codex.rollout_generation >= 1


def test_status_labels_compatibility_paths_distinctly():
    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true"})
    )
    rows = {row.target_id: row for row in status.combinations}
    assert rows["codex.legacy-profile-bound-omnigent"].default_status == (
        "retired_for_new_work"
    )
    assert rows["codex.legacy-profile-bound-omnigent"].compatibility_path_status == (
        "retired_for_new_work"
    )
    assert rows["codex.direct"].default_status == "compatibility_path"
    assert rows["codex.direct"].compatibility_path_status == "active_compatibility"
    assert rows["claude.generic-omnigent"].default_status == "unavailable"


def test_status_reports_rollback_availability_and_activation():
    status = build_runtime_provider_migration_status(
        policy=_policy(
            {
                _CODEX_GATE: "true",
                RUNTIME_PROVIDER_ROLLBACK_ENV: (
                    "stop_new_generic_codex_admission"
                ),
            }
        )
    )
    assert status.active_rollback_controls == (
        str(RuntimeProviderRollbackControl.stop_new_generic_codex_admission),
    )
    rows = {row.target_id: row for row in status.combinations}
    codex = rows["codex.generic-omnigent"]
    assert codex.rollback_available is True
    assert "stop_new_generic_codex_admission" in codex.applicable_rollback_controls
    assert "stop_new_generic_codex_admission" in codex.active_rollback_controls
    claude = rows["claude.generic-omnigent"]
    assert claude.active_rollback_controls == ()


def test_status_reports_native_chat_availability_control():
    allowed = build_runtime_provider_migration_status(policy=_policy())
    assert allowed.native_interactive_chat_allowed is True
    disabled = build_runtime_provider_migration_status(
        policy=_policy(
            {RUNTIME_PROVIDER_ROLLBACK_ENV: "disable_native_interactive_chat"}
        )
    )
    assert disabled.native_interactive_chat_allowed is False


def test_status_excludes_credentials_host_paths_and_image_authority():
    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true"})
    )
    serialized = json.dumps(status.model_dump(by_alias=True, mode="json"))
    for forbidden in (
        "token",
        "secret",
        "password",
        "apiKey",
        "providerSessionId",
        "/home/",
        "/var/run/docker",
        "@sha256:",
        "imageRef",
    ):
        assert forbidden not in serialized, forbidden


def test_status_projects_bounded_recent_outcomes_per_harness_class():
    control_plane_metrics.increment(
        control_plane_metrics.MIGRATION_LAUNCH_READINESS,
        harness_class="codex",
        readiness="ready",
    )
    control_plane_metrics.increment(
        control_plane_metrics.MIGRATION_SUPPORT_EVIDENCE_DENIAL,
        harness_class="codex",
        denial_reason="support_evidence_stale",
    )
    control_plane_metrics.increment(
        control_plane_metrics.MIGRATION_FOLLOWUP_AVAILABILITY,
        harness_class="codex",
        followup_kind="workflow_chat",
        availability="available",
    )
    control_plane_metrics.increment(
        control_plane_metrics.MIGRATION_CLEANUP_OUTCOME,
        harness_class="codex",
        cleanup_outcome="completed_clean",
    )
    control_plane_metrics.increment(
        control_plane_metrics.MIGRATION_LAUNCH_READINESS,
        harness_class="claude",
        readiness="not_ready",
    )
    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true", _CLAUDE_GATE: "true"})
    )
    rows = {row.target_id: row for row in status.combinations}
    codex = rows["codex.generic-omnigent"].recent_outcomes
    assert codex.launch_readiness == {"ready": 1}
    assert codex.support_evidence_denials == {"support_evidence_stale": 1}
    assert codex.followup_availability == {"workflow_chat:available": 1}
    assert codex.cleanup_outcomes == {"completed_clean": 1}
    claude = rows["claude.generic-omnigent"].recent_outcomes
    assert claude.launch_readiness == {"not_ready": 1}


# --- Telemetry contract -----------------------------------------------------

_REQUIRED_MIGRATION_METRICS = (
    "omnigent_migration_selected_path",
    "omnigent_migration_rollout_state",
    "omnigent_migration_launch_readiness",
    "omnigent_migration_support_evidence_denial",
    "omnigent_migration_provider_profile_wait_seconds",
    "omnigent_migration_host_latency_seconds",
    "omnigent_migration_first_turn_latency_seconds",
    "omnigent_migration_followup_availability",
    "omnigent_migration_cleanup_outcome",
    "omnigent_migration_fallback_denied",
    "omnigent_migration_rollback_activation",
)


#: Every migration family and the recorder its production owners must use.
#: A family with no production emitter leaves the operator migration view
#: permanently empty for that signal, so the recorder must be referenced by
#: production code, not only by tests.
_MIGRATION_RECORDERS = {
    "omnigent_migration_selected_path": "record_runtime_target_selection",
    "omnigent_migration_rollout_state": "record_runtime_target_selection",
    "omnigent_migration_fallback_denied": "record_runtime_target_selection",
    "omnigent_migration_rollback_activation": "record_rollback_activation",
    "omnigent_migration_launch_readiness": "record_migration_launch_readiness",
    "omnigent_migration_support_evidence_denial": "record_support_evidence_denial",
    "omnigent_migration_provider_profile_wait_seconds": (
        "record_provider_profile_wait"
    ),
    "omnigent_migration_host_latency_seconds": "record_host_latency",
    "omnigent_migration_first_turn_latency_seconds": "record_first_turn_latency",
    "omnigent_migration_followup_availability": "record_followup_availability",
    "omnigent_migration_cleanup_outcome": "record_cleanup_outcome",
}


def test_every_required_migration_metric_is_registered():
    inventory = control_plane_metrics.label_inventory()
    for name in _REQUIRED_MIGRATION_METRICS:
        assert name in inventory, name


def test_every_required_migration_metric_has_a_production_emitter():
    """A declared family with no production emitter is an empty operator view."""

    import pathlib

    assert set(_MIGRATION_RECORDERS) == set(_REQUIRED_MIGRATION_METRICS)
    repo_root = pathlib.Path(control_plane_metrics.__file__).parents[3]
    sources = [
        path
        for package in ("moonmind", "api_service")
        for path in (repo_root / package).rglob("*.py")
        # The registry declares the recorders; a caller must invoke one.
        if path != pathlib.Path(control_plane_metrics.__file__)
    ]
    emitters: dict[str, list[str]] = {}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for recorder in set(_MIGRATION_RECORDERS.values()):
            if f"{recorder}," in text or f"{recorder}(" in text:
                emitters.setdefault(recorder, []).append(
                    str(path.relative_to(repo_root))
                )
    missing = sorted(
        name
        for name, recorder in _MIGRATION_RECORDERS.items()
        if not emitters.get(recorder)
    )
    assert missing == [], missing


def test_migration_metric_labels_stay_low_cardinality_and_identity_free():
    inventory = control_plane_metrics.label_inventory()
    for name in _REQUIRED_MIGRATION_METRICS:
        for label in inventory[name]:
            assert label not in control_plane_metrics.FORBIDDEN_LABEL_KEYS
            allowed = control_plane_metrics.BOUNDED_LABEL_VALUES[label]
            # Bounded means a small closed vocabulary, not free text.
            assert 0 < len(allowed) <= 20, (name, label, len(allowed))


def test_out_of_vocabulary_label_collapses_instead_of_leaking_identity():
    control_plane_metrics.record_runtime_target_selection(
        harness_id="brand-new-native",
        realizer_class="generic_omnigent",
        selection_source="rollout_default",
        rollout_state="new_work_default",
        available=True,
    )
    counters = control_plane_metrics.snapshot()["counters"]
    assert any("'harness_class', 'unregistered'" in key for key in counters)
    assert not any("brand-new-native" in key for key in counters)


def test_denied_selection_records_the_fallback_denial():
    control_plane_metrics.record_runtime_target_selection(
        harness_id="codex-native",
        realizer_class="generic_omnigent",
        selection_source="authored",
        rollout_state="disabled",
        available=False,
        denial_reason="rollout_disabled",
    )
    series = {
        (name, labels.get("denial_reason")): value
        for name, labels, value in control_plane_metrics.counter_series()
    }
    assert (
        series[
            (control_plane_metrics.MIGRATION_FALLBACK_DENIED, "rollout_disabled")
        ]
        == 1
    )


def test_rollback_activation_metric_uses_the_closed_control_vocabulary():
    for control in RuntimeProviderRollbackControl:
        control_plane_metrics.record_rollback_activation(str(control))
    counters = control_plane_metrics.snapshot()["counters"]
    recorded = {
        key
        for key in counters
        if control_plane_metrics.MIGRATION_ROLLBACK_ACTIVATION in key
    }
    assert len(recorded) == len(list(RuntimeProviderRollbackControl))
    assert not any("other" in key for key in recorded)


# --- Protected support projection -------------------------------------------

_SUPPORT_INDEX_ENV = "MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE"


def _publish_index(tmp_path, entries, monkeypatch) -> None:
    path = tmp_path / "execution-support-evidence.json"
    path.write_text(json.dumps({"entries": list(entries)}), encoding="utf-8")
    monkeypatch.setenv(_SUPPORT_INDEX_ENV, str(path))


def _generic_row(target: str = "codex.generic-omnigent"):
    def _select(status):
        return {row.target_id: row for row in status.combinations}[target]

    return _select


@pytest.mark.parametrize(
    "status", ["failed", "skipped", "blocked", "unavailable", "partial"]
)
def test_a_newer_non_pass_row_never_displaces_the_last_pass(
    status: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The projection reports what *backs* a rule, not merely what is newest.

    MoonLadderStudios/MoonMind#3885 made non-pass rows recordable. Being the
    newest entry for a combination, such a row would otherwise win
    ``_newest_matching`` and replace the last real pass as the evidence the
    operator view says backs the rule.
    """

    plan = support_plan()
    passed_at = datetime.now(UTC) - timedelta(hours=6)
    monkeypatch.setenv(_SUPPORT_INDEX_ENV, "")
    _publish_index(
        tmp_path,
        [
            support_row(plan, generated_at=passed_at),
            support_row(plan, generated_at=datetime.now(UTC), status=status),
        ],
        monkeypatch,
    )

    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )

    assert row.protected_evidence is not None
    assert row.protected_evidence.generated_at == passed_at
    assert row.last_successful_canary_at == passed_at


def test_an_index_of_only_non_pass_rows_backs_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable index with no pass is "nothing backs this", not "no source"."""

    plan = support_plan()
    _publish_index(
        tmp_path,
        [
            support_row(plan, status="failed"),
            support_row(plan, status="blocked"),
        ],
        monkeypatch,
    )

    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true"})
    )
    row = _generic_row()(status)

    assert row.protected_evidence is None
    assert row.last_successful_canary_at is None
    # The document itself was readable, so the operator is told the source is
    # available and simply carries no passing row.
    assert status.evidence_sources_available["protectedLive"] is True


# --- Advertised concurrency --------------------------------------------------


def test_advertised_concurrency_never_exceeds_the_validated_level(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment configured for 16 advertises only what it validated."""

    plan = support_plan()
    entry = support_row(plan)
    entry["concurrency"] = concurrency_record(plan, level=4)
    _publish_index(tmp_path, [entry], monkeypatch)
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY", "16")

    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )

    assert row.advertised_concurrency.validated_level == 4
    assert row.advertised_concurrency.advertised_level == 4
    # The configured value is reported unchanged; nothing rewrote it.
    assert row.advertised_concurrency.operator_ceiling == 16
    assert row.advertised_concurrency.limited_by == "qualified_level"


def test_advertised_concurrency_never_exceeds_a_lower_operator_ceiling(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = support_plan()
    entry = support_row(plan)
    entry["concurrency"] = concurrency_record(plan, level=4)
    _publish_index(tmp_path, [entry], monkeypatch)
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY", "2")

    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )

    assert row.advertised_concurrency.validated_level == 4
    assert row.advertised_concurrency.advertised_level == 2
    assert row.advertised_concurrency.limited_by == "operator_ceiling"


def test_a_combination_without_current_concurrency_evidence_advertises_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unqualified is zero, and an expired row stops advertising its level."""

    plan = support_plan()
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY", "8")

    # No concurrency dimension at all.
    _publish_index(tmp_path, [support_row(plan)], monkeypatch)
    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )
    assert row.advertised_concurrency.advertised_level == 0
    assert row.advertised_concurrency.validated_level == 0
    assert row.advertised_concurrency.limited_by == "unqualified"

    # A qualified but expired row: the concurrency dimension inherits the
    # freshness of the support row that carries it.
    expired = support_row(
        plan,
        generated_at=datetime.now(UTC) - timedelta(days=10),
        ttl=timedelta(days=1),
    )
    expired["concurrency"] = concurrency_record(plan, level=4)
    _publish_index(tmp_path, [expired], monkeypatch)
    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )
    assert row.protected_evidence is not None
    assert row.protected_evidence.expired is True
    assert row.advertised_concurrency.advertised_level == 0
    assert row.advertised_concurrency.limited_by == "unqualified"


def test_no_published_index_advertises_nothing_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default deployment path has no protected index and must still work."""

    monkeypatch.delenv(_SUPPORT_INDEX_ENV, raising=False)

    status = build_runtime_provider_migration_status(
        policy=_policy({_CODEX_GATE: "true"})
    )

    assert status.evidence_sources_available["protectedLive"] is False
    for row in status.combinations:
        assert row.advertised_concurrency.advertised_level == 0
        assert row.advertised_concurrency.limited_by == "unqualified"


def test_an_over_age_row_advertises_nothing_even_though_it_has_not_expired(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advertisement must not outlive the admission window.

    A row generated beyond ``MAX_EXECUTION_SUPPORT_EVIDENCE_AGE`` but carrying a
    longer ``expiresAt`` is refused by admission and reported as *not expired*
    by the evidence view. Deciding freshness here instead of asking admission is
    how a projection ends up advertising a peak that execution will refuse.
    """

    from moonmind.omnigent.execution_support_evidence import (
        MAX_EXECUTION_SUPPORT_EVIDENCE_AGE,
    )

    plan = support_plan()
    over_age = support_row(
        plan,
        generated_at=datetime.now(UTC) - (MAX_EXECUTION_SUPPORT_EVIDENCE_AGE
                                          + timedelta(days=1)),
        ttl=MAX_EXECUTION_SUPPORT_EVIDENCE_AGE + timedelta(days=30),
    )
    over_age["concurrency"] = concurrency_record(plan, level=4)
    _publish_index(tmp_path, [over_age], monkeypatch)
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY", "8")

    row = _generic_row()(
        build_runtime_provider_migration_status(policy=_policy({_CODEX_GATE: "true"}))
    )

    assert row.protected_evidence is not None
    # The document itself has not reached its own expiry...
    assert row.protected_evidence.expired is False
    # ...but admission refuses it, so it advertises nothing.
    assert row.advertised_concurrency.advertised_level == 0
    assert row.advertised_concurrency.validated_level == 0
    assert row.advertised_concurrency.limited_by == "unqualified"
