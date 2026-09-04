"""Shared runtime-target selection and admission boundary.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 2, 3, 4, 5, 9).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from moonmind.omnigent.runtime_provider_rollout import (
    RUNTIME_PROVIDER_ROLLBACK_ENV,
    RolloutReason,
    RolloutState,
    RuntimeProviderPathClass,
    default_runtime_provider_rollout_policy,
)
from moonmind.workflows.executions import runtime_target_selection as boundary
from moonmind.workflows.executions.runtime_target_selection import (
    AuthoringSurface,
    SelectionSource,
    resolve_default_runtime_id,
    resolve_runtime_target_catalog,
    resolve_runtime_target_selection,
    runtime_target_catalog_payload,
)

_CODEX_GATE = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
_CLAUDE_GATE = "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED"

#: Every surface the issue names must resolve through this one boundary.
_REQUIRED_SURFACES = (
    AuthoringSurface.workflow_create,
    AuthoringSurface.preset_expansion,
    AuthoringSurface.schedule,
    AuthoringSurface.schedule_occurrence,
    AuthoringSurface.edit,
    AuthoringSurface.rerun,
    AuthoringSurface.retry_as_fresh_execution,
    AuthoringSurface.checkpoint_branch,
    AuthoringSurface.remediation,
    AuthoringSurface.linked_continuation,
    AuthoringSurface.api_submission,
    AuthoringSurface.mcp_submission,
)


def _policy(env: dict[str, str] | None = None):
    return default_runtime_provider_rollout_policy(env=env or {})


def _settings(default_runtime: str = "omnigent") -> SimpleNamespace:
    return SimpleNamespace(default_runtime=default_runtime)


# --- One shared boundary ----------------------------------------------------


@pytest.mark.parametrize("surface", _REQUIRED_SURFACES)
def test_every_authoring_surface_receives_the_same_promoted_default(surface):
    policy = _policy({_CODEX_GATE: "true"})
    selection = resolve_runtime_target_selection(
        surface=surface, workflow_settings=_settings(), policy=policy
    )
    assert selection.runtime_id == "omnigent"
    assert selection.target_id == "codex.generic-omnigent"
    assert selection.rollout_state is RolloutState.new_work_default
    assert selection.source is SelectionSource.rollout_default
    assert selection.policy_version == policy.policy_version
    assert selection.rollout_generation == 1
    assert selection.available is True


def test_boundary_never_reads_environment_variables_for_a_default():
    source = inspect.getsource(boundary)
    assert "os.environ" not in source
    assert "getenv" not in source


def test_boundary_emits_bounded_migration_telemetry_once_per_selection():
    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    control_plane_metrics.reset()
    try:
        resolve_runtime_target_selection(
            surface=AuthoringSurface.workflow_create,
            policy=_policy({_CODEX_GATE: "true"}),
        )
        series = {
            (name, tuple(sorted(labels.items()))): value
            for name, labels, value in control_plane_metrics.counter_series()
        }
        selected = [
            (labels, value)
            for (name, labels), value in series.items()
            if name == control_plane_metrics.MIGRATION_SELECTED_PATH
        ]
        assert len(selected) == 1
        labels, value = selected[0]
        assert value == 1
        assert dict(labels) == {
            "harness_class": "codex",
            "realizer_class": "generic_omnigent",
            "selection_source": "rollout_default",
        }
    finally:
        control_plane_metrics.reset()


def test_dashboard_config_projection_is_not_counted_as_a_selection():
    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    control_plane_metrics.reset()
    try:
        resolve_runtime_target_selection(
            surface=AuthoringSurface.dashboard_config, policy=_policy()
        )
        assert control_plane_metrics.counter_series() == ()
    finally:
        control_plane_metrics.reset()


def test_denied_selection_records_the_fallback_denial_metric():
    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    control_plane_metrics.reset()
    try:
        resolve_runtime_target_selection(
            surface=AuthoringSurface.api_submission,
            requested_target_id="claude.generic-omnigent",
            policy=_policy(),
        )
        denials = [
            (labels, value)
            for name, labels, value in control_plane_metrics.counter_series()
            if name == control_plane_metrics.MIGRATION_FALLBACK_DENIED
        ]
        assert denials == [
            ({"harness_class": "claude", "denial_reason": "rollout_disabled"}, 1)
        ]
    finally:
        control_plane_metrics.reset()


def test_active_rollback_control_is_recorded_at_the_selection_boundary():
    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    control_plane_metrics.reset()
    try:
        resolve_runtime_target_selection(
            surface=AuthoringSurface.workflow_create,
            workflow_settings=_settings(),
            policy=_policy(
                {
                    _CODEX_GATE: "true",
                    RUNTIME_PROVIDER_ROLLBACK_ENV: (
                        "restore_legacy_or_direct_default"
                    ),
                }
            ),
        )
        activations = [
            (labels, value)
            for name, labels, value in control_plane_metrics.counter_series()
            if name == control_plane_metrics.MIGRATION_ROLLBACK_ACTIVATION
        ]
        assert activations == [
            ({"rollback_control": "restore_legacy_or_direct_default"}, 1)
        ]
    finally:
        control_plane_metrics.reset()


def test_unknown_surface_fails_fast():
    with pytest.raises(ValueError):
        resolve_runtime_target_selection(
            surface="totally_new_surface", policy=_policy()
        )


# --- Promotion ---------------------------------------------------------------


def test_default_follows_the_rollout_policy_not_a_literal():
    # Before Codex generic promotion the legacy profile-bound row is the
    # promoted Omnigent target, so the canonical runtime id is still omnigent.
    unpromoted = _policy({})
    assert resolve_default_runtime_id(policy=unpromoted) == "omnigent"
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create, policy=unpromoted
    )
    assert selection.target_id == "codex.legacy-profile-bound-omnigent"
    assert selection.path_class is (
        RuntimeProviderPathClass.legacy_profile_bound_omnigent
    )

    promoted = _policy({_CODEX_GATE: "true"})
    promoted_selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create, policy=promoted
    )
    assert promoted_selection.target_id == "codex.generic-omnigent"
    assert promoted_selection.path_class is (
        RuntimeProviderPathClass.generic_omnigent
    )


def test_authored_product_intention_resolves_to_the_qualified_target():
    policy = _policy({_CODEX_GATE: "true"})
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        requested_runtime="omnigent",
        policy=policy,
    )
    assert selection.source is SelectionSource.authored
    # The submitted canonical identity stays `omnigent`.
    assert selection.runtime_id == "omnigent"
    assert selection.target_id == "codex.generic-omnigent"


def test_claude_and_opencode_promotion_is_independent():
    policy = _policy({_CLAUDE_GATE: "true"})
    catalog = resolve_runtime_target_catalog(policy=policy)
    states = {view.target_id: view.rollout_state for view in catalog}
    assert states["claude.generic-omnigent"] is RolloutState.new_work_default
    assert states["opencode.generic-omnigent"] is RolloutState.new_work_default
    assert states["codex.generic-omnigent"] is RolloutState.disabled


# --- Compatibility labeling --------------------------------------------------


def test_direct_and_legacy_paths_are_labeled_compatibility_choices():
    catalog = resolve_runtime_target_catalog(policy=_policy({_CODEX_GATE: "true"}))
    by_target = {view.target_id: view for view in catalog}
    assert by_target["codex.direct"].compatibility_path is True
    assert by_target["codex.direct"].runtime_id == "codex_cli"
    assert by_target["codex.direct"].default_eligible is False
    assert by_target["claude.direct"].compatibility_path is True
    legacy = by_target["codex.legacy-profile-bound-omnigent"]
    assert legacy.compatibility_path is True
    assert legacy.default_eligible is False
    # A promoted generic row is the only non-compatibility default.
    assert by_target["codex.generic-omnigent"].compatibility_path is False
    assert by_target["codex.generic-omnigent"].default_eligible is True


def test_catalog_labels_are_never_new_top_level_runtime_ids():
    catalog = resolve_runtime_target_catalog(policy=_policy({_CODEX_GATE: "true"}))
    assert {view.runtime_id for view in catalog} == {
        "omnigent",
        "codex_cli",
        "claude_code",
    }
    for view in catalog:
        assert view.target_id != view.runtime_id


# --- Immutable recorded authority --------------------------------------------


@pytest.mark.parametrize(
    "surface",
    (
        AuthoringSurface.edit,
        AuthoringSurface.rerun,
        AuthoringSurface.schedule_occurrence,
        AuthoringSurface.linked_continuation,
    ),
)
def test_continuation_surfaces_preserve_recorded_authority(surface):
    policy = _policy({_CODEX_GATE: "true"})
    selection = resolve_runtime_target_selection(
        surface=surface,
        recorded_target_id="codex.legacy-profile-bound-omnigent",
        recorded_runtime_id="omnigent",
        policy=policy,
    )
    assert selection.source is SelectionSource.recorded
    assert selection.target_id == "codex.legacy-profile-bound-omnigent"
    assert selection.rollout_state is RolloutState.retired_for_new_work
    # Retired for new work: still visible, but new submission needs a
    # replacement rather than a silent upgrade.
    assert selection.available is False
    assert selection.replacement_required is True


def test_rerun_may_explicitly_upgrade_to_the_qualified_target():
    policy = _policy({_CODEX_GATE: "true"})
    upgraded = resolve_runtime_target_selection(
        surface=AuthoringSurface.rerun,
        recorded_target_id="codex.legacy-profile-bound-omnigent",
        recorded_runtime_id="omnigent",
        upgrade_to_qualified_target=True,
        policy=policy,
    )
    assert upgraded.source is SelectionSource.rollout_default
    assert upgraded.target_id == "codex.generic-omnigent"


def test_unavailable_historical_selection_stays_visible_and_needs_replacement():
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.edit,
        recorded_target_id="codex.retired-experiment",
        recorded_runtime_id="omnigent",
        policy=_policy(),
    )
    assert selection.source is SelectionSource.recorded
    assert selection.runtime_id == "omnigent"
    assert selection.available is False
    assert selection.replacement_required is True
    assert selection.reason_code is RolloutReason.combination_not_registered


def test_retry_as_fresh_execution_authors_a_new_target():
    policy = _policy({_CODEX_GATE: "true"})
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.retry_as_fresh_execution,
        recorded_target_id="codex.legacy-profile-bound-omnigent",
        recorded_runtime_id="omnigent",
        policy=policy,
    )
    # A fresh execution is new work, so it takes the promoted target.
    assert selection.source is SelectionSource.rollout_default
    assert selection.target_id == "codex.generic-omnigent"


# --- Fail-closed selection ---------------------------------------------------


def test_explicit_target_selection_of_a_disabled_row_fails_closed():
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.api_submission,
        requested_target_id="claude.generic-omnigent",
        policy=_policy(),
    )
    assert selection.available is False
    assert selection.replacement_required is True
    assert selection.reason_code is RolloutReason.rollout_disabled


def test_rollback_removes_the_default_without_substituting_a_runtime():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "stop_all_new_omnigent_work",
        }
    )
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    # The configured runtime id is reported with an exact unavailable reason;
    # it is never replaced with direct Codex or any other runtime.
    assert selection.runtime_id == "omnigent"
    assert selection.available is False
    assert selection.replacement_required is True
    assert selection.source is SelectionSource.configured_default


def test_rollback_can_restore_an_explicitly_supported_direct_default():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "restore_legacy_or_direct_default",
        }
    )
    catalog = resolve_runtime_target_catalog(policy=policy)
    by_target = {view.target_id: view for view in catalog}
    # The generic row is demoted to explicit-only; every explicitly supported
    # legacy or direct row becomes a restored default for future executions.
    assert by_target["codex.generic-omnigent"].rollout_state is (
        RolloutState.explicit_only
    )
    assert by_target["codex.legacy-profile-bound-omnigent"].rollout_state is (
        RolloutState.new_work_default
    )
    assert by_target["codex.direct"].rollout_state is (
        RolloutState.new_work_default
    )
    # The configured runtime keeps its identity: the restored default is the
    # legacy Omnigent row, not a substituted direct runtime.
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings("omnigent"),
        policy=policy,
    )
    assert selection.runtime_id == "omnigent"
    assert selection.target_id == "codex.legacy-profile-bound-omnigent"
    assert selection.available is True
    # A deployment configured for direct Codex receives the restored direct row.
    direct_selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings("codex_cli"),
        policy=policy,
    )
    assert direct_selection.runtime_id == "codex_cli"
    assert direct_selection.target_id == "codex.direct"


def test_rollback_only_changes_new_admission_not_recorded_authority():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "stop_new_generic_codex_admission",
        }
    )
    # New work loses the promoted generic Codex target.
    new_work = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        requested_target_id="codex.generic-omnigent",
        policy=policy,
    )
    assert new_work.available is False
    assert new_work.reason_code is RolloutReason.rollout_disabled
    # An execution recorded against the legacy row keeps its recorded target;
    # rollback never rewrites it.
    recorded = resolve_runtime_target_selection(
        surface=AuthoringSurface.rerun,
        recorded_target_id="codex.legacy-profile-bound-omnigent",
        recorded_runtime_id="omnigent",
        policy=policy,
    )
    assert recorded.source is SelectionSource.recorded
    assert recorded.target_id == "codex.legacy-profile-bound-omnigent"


def test_unregistered_runtime_id_remains_authorable():
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.mcp_submission,
        requested_runtime="jules",
        policy=_policy(),
    )
    assert selection.runtime_id == "jules"
    assert selection.available is True
    assert selection.target_id is None


def test_short_runtime_aliases_normalize_to_canonical_ids():
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.api_submission,
        requested_runtime="codex",
        policy=_policy(),
    )
    assert selection.runtime_id == "codex_cli"
    assert selection.target_id == "codex.direct"
    assert selection.path_class is RuntimeProviderPathClass.direct_compatibility


# --- Dashboard projection ----------------------------------------------------


def test_catalog_payload_is_serializable_and_secret_free():
    payload = runtime_target_catalog_payload(policy=_policy({_CODEX_GATE: "true"}))
    assert payload["defaultRuntimeId"] == "omnigent"
    assert payload["policyVersion"].startswith("moonmind.omnigent-runtime-provider")
    target_ids = {item["targetId"] for item in payload["targets"]}
    assert "codex.generic-omnigent" in target_ids
    import json

    serialized = json.dumps(payload)
    for forbidden in ("token", "secret", "@sha256:", "/home/"):
        assert forbidden not in serialized

def test_explicit_deployment_default_configuration_is_not_silently_replaced():
    """An operator-configured runtime is an authored intention, not a guess."""

    policy = _policy({_CODEX_GATE: "true"})
    # `codex_cli` is a labeled compatibility path, so the rollout policy does
    # not recommend it — but an explicit deployment configuration still gets it.
    assert (
        resolve_default_runtime_id(
            workflow_settings=_settings("codex_cli"), policy=policy
        )
        == "codex_cli"
    )
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings("codex_cli"),
        policy=policy,
    )
    assert selection.runtime_id == "codex_cli"
    assert selection.target_id == "codex.direct"
    assert selection.source is SelectionSource.configured_default
    assert selection.available is True
    assert selection.path_class is RuntimeProviderPathClass.direct_compatibility


def test_configured_runtime_with_no_selectable_target_reports_the_promoted_one():
    """A configured runtime the policy fully disabled cannot stay the default."""

    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: (
                "stop_new_generic_codex_admission,"
                "restore_legacy_or_direct_default"
            ),
        }
    )
    disabled_only = policy.model_copy(
        update={
            "rules": tuple(
                rule
                for rule in policy.rules
                if rule.target_id
                in {"codex.generic-omnigent", "codex.direct"}
            )
        }
    )
    # Every `omnigent` row is gone or disabled, so the promoted direct row is
    # reported instead of an unavailable configured runtime.
    assert (
        resolve_default_runtime_id(
            workflow_settings=_settings("omnigent"), policy=disabled_only
        )
        == "codex_cli"
    )


def test_configured_runtime_that_is_unregistered_stays_authorable():
    selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings("jules"),
        policy=_policy(),
    )
    assert selection.runtime_id == "jules"
    assert selection.available is True
    assert selection.target_id is None
    assert selection.reason_code is RolloutReason.combination_not_registered
