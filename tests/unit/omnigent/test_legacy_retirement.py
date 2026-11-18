"""Legacy retirement admission table + guard tests.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835,
MoonLadderStudios/MoonMind#3932, MoonLadderStudios/MoonMind#5342,
MoonLadderStudios/MoonMind#5341.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from moonmind.omnigent.legacy_retirement import (
    OBSOLETE_CONFIGURATION,
    RETIREMENT_INVENTORY,
    LegacyAdmissionRejected,
    LegacyPathRecord,
    ObsoleteConfiguration,
    ObsoleteConfigurationError,
    RetirementClass,
    RetirementGuardError,
    assert_inventory_is_complete,
    assert_new_admission_allowed,
    assert_obsolete_configuration,
    assert_surface_admits_new_work,
    evaluate_new_admission,
    get_retirement_record,
)
from moonmind.omnigent.retirement_surfaces import (
    discover_legacy_surfaces,
    parse_surface_ref,
)


def _record(**overrides: object) -> LegacyPathRecord:
    payload: dict[str, object] = {
        "pathId": "omnigent.legacy.fixture",
        "owner": "omnigent-control-plane",
        "description": "Fixture row.",
        "retirementClass": RetirementClass.NEW_ADMISSION_DISABLED,
        "surfaces": (
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore",
        ),
        "consumer": "Fixture consumer.",
        "removalCondition": "Fixture removal condition.",
    }
    payload.update(overrides)
    return LegacyPathRecord(**payload)  # type: ignore[arg-type]


# ------------------------------------------------------------------ records


def test_record_rejects_admission_source_on_a_closed_class() -> None:
    with pytest.raises(ValueError, match="does not admit new work"):
        _record(newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution")


def test_record_requires_admission_source_while_it_still_admits() -> None:
    with pytest.raises(ValueError, match="must be named"):
        _record(retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH)


@pytest.mark.parametrize("field", ["consumer", "removalCondition"])
def test_record_requires_its_consumer_and_removal_condition(field: str) -> None:
    with pytest.raises(ValueError, match="must name"):
        _record(**{field: "  "})


def test_every_retained_row_names_its_consumer_and_removal_condition() -> None:
    missing = sorted(
        path.path_id
        for path in RETIREMENT_INVENTORY
        if not path.consumer.strip() or not path.removal_condition.strip()
    )
    assert missing == []


@pytest.mark.parametrize("field", ["sharedOperation", "missingOutcome"])
def test_a_path_that_still_admits_must_map_to_a_shared_operation(
    field: str,
) -> None:
    mapped = {"sharedOperation": "Shared op.", "missingOutcome": "Gap."}
    mapped[field] = " "
    with pytest.raises(ValueError, match="still admits new work must name"):
        _record(
            retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
            newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
            **mapped,
        )


@pytest.mark.parametrize("field", ["sharedOperation", "missingOutcome"])
def test_a_closed_path_names_no_shared_lifecycle_gap(field: str) -> None:
    with pytest.raises(ValueError, match="only such a path may"):
        _record(**{field: "Something."})


@pytest.mark.parametrize(
    "surface",
    [
        "python:moonmind.workflows.temporal.runtime.launcher:ManagedRuntimeLauncher",
        "python:moonmind.workflows.temporal.workflows.agent_session:MoonMindAgentSessionWorkflow",
        "python:moonmind.workflows.temporal.runtime.codex_session_runtime:CodexManagedSessionRuntime",
    ],
)
def test_direct_launcher_and_agent_session_owners_are_inventoried(
    surface: str,
) -> None:
    from moonmind.omnigent.legacy_retirement import retirement_record_for_surface

    record = retirement_record_for_surface(surface)
    assert record is not None, surface
    assert record.admits_new_work
    assert record.consumer.strip()
    assert record.removal_condition.strip()


def test_a_class_either_admits_new_work_or_refuses_it() -> None:
    # There is no rollback-only class: restoring a prior default for new work is
    # the supervisor rollback control's job, not a per-row generation allowlist.
    assert {member.value for member in RetirementClass} == {
        "active_product_path",
        "new_admission_disabled",
    }
    with pytest.raises(ValueError):
        _record(rollbackGenerations=frozenset({"gen-7"}))
    with pytest.raises(TypeError):
        evaluate_new_admission(  # type: ignore[call-arg]
            _record(), rollback_generation="gen-7"
        )


def test_record_rejects_a_surface_ref_without_a_scheme() -> None:
    with pytest.raises(ValueError):
        _record(surfaces=("moonmind.omnigent.bridge_store:X",))


# ------------------------------------------------------------ stale rows


@pytest.mark.parametrize(
    "surface",
    (
        "python:moonmind.omnigent.bridge_store:__DeletedStore__",
        "compose-service:omnigent-host-gone",
        "script:start-deleted-host.sh",
    ),
)
def test_a_row_whose_surface_is_gone_must_be_deleted_with_the_code(
    surface: str,
) -> None:
    # Deleting a legacy component deletes its admission row in the same change;
    # there is no "removed" class or removal evidence to keep it around.
    stale = (*RETIREMENT_INVENTORY, _record(surfaces=(surface,)))
    with pytest.raises(RetirementGuardError, match="no longer exist"):
        assert_inventory_is_complete(stale)


def test_two_rows_cannot_share_a_path_id() -> None:
    duplicated = (
        *RETIREMENT_INVENTORY,
        _record(pathId=RETIREMENT_INVENTORY[0].path_id),
    )
    with pytest.raises(RetirementGuardError, match="duplicate retirement row"):
        assert_inventory_is_complete(duplicated)


def test_env_surface_evidence_excludes_the_inventory_itself(monkeypatch) -> None:
    """An ``env:`` row must not prove its own surface exists.

    ``legacy_retirement.py`` writes every inventoried variable into its own row,
    so including the inventory in the environment corpus let the guard pass
    after the real Compose and runtime handling for a retained variable had been
    deleted.
    """

    from moonmind.omnigent import retirement_surfaces

    variable = "OMNIGENT_ONLY_IN_THE_INVENTORY_IMAGE_REF"
    monkeypatch.setattr(
        retirement_surfaces, "COMPOSE_FILE", pathlib.Path("/nonexistent-compose.yaml")
    )
    retirement_surfaces.reset_surface_caches()
    try:
        assert retirement_surfaces.surface_exists(f"env:{variable}") is False
    finally:
        retirement_surfaces.reset_surface_caches()


def test_env_surface_evidence_ignores_comments_and_docstrings(monkeypatch) -> None:
    """Non-executable text is not a consumer of an environment identity."""

    from moonmind.omnigent import retirement_surfaces

    source = "\n".join(
        (
            '"""OMNIGENT_DOCSTRING_ONLY_REF is described here."""',
            "# OMNIGENT_COMMENT_ONLY_REF is mentioned here.",
            "import os",
            'value = os.environ["OMNIGENT_REALLY_CONSUMED_REF"]',
            "",
        )
    )
    executable = retirement_surfaces._executable_python_text(source)
    assert "OMNIGENT_DOCSTRING_ONLY_REF" not in executable
    assert "OMNIGENT_COMMENT_ONLY_REF" not in executable
    # An operand string is exactly how a variable is honored, so it is kept.
    assert "OMNIGENT_REALLY_CONSUMED_REF" in executable


# ------------------------------------------------------------- completeness


def test_inventory_covers_every_discovered_legacy_surface() -> None:
    assert_inventory_is_complete()


def test_discovery_finds_every_inventoried_surface_it_derives() -> None:
    # Completeness alone passes when discovery silently finds nothing, so the
    # detector must still see every row surface of a kind it derives from the
    # repository. Deleting a legacy component deletes its row, not this test.
    derived_schemes = {
        "realizer",
        "runtime-strategy",
        "script",
        "compose-service",
        "compose-profile",
        "env",
    }
    discovered = discover_legacy_surfaces()
    missed = sorted(
        ref
        for path in RETIREMENT_INVENTORY
        for ref in path.surfaces
        if parse_surface_ref(ref)[0] in derived_schemes and ref not in discovered
    )
    assert missed == []


def test_unclassified_new_legacy_dependency_fails_ci() -> None:
    # Simulate registering a new legacy realizer with no retirement row.
    with pytest.raises(RetirementGuardError, match="no retirement row"):
        assert_inventory_is_complete(
            discovered=discover_legacy_surfaces() | {"realizer:codex-legacy@2"}
        )


def test_two_rows_cannot_claim_the_same_surface() -> None:
    duplicated = (
        *RETIREMENT_INVENTORY,
        _record(
            pathId="omnigent.legacy.duplicate",
            surfaces=RETIREMENT_INVENTORY[0].surfaces,
        ),
    )
    with pytest.raises(RetirementGuardError, match="more than one row"):
        assert_inventory_is_complete(duplicated)


# ------------------------------------------------------------- new admission


def test_assert_new_admission_raises_with_a_stable_reason_code() -> None:
    closed = (_record(pathId="omnigent.legacy.closed"),)
    with pytest.raises(LegacyAdmissionRejected) as excinfo:
        assert_new_admission_allowed("omnigent.legacy.closed", inventory=closed)
    assert excinfo.value.reason_code == "new_admission_disabled:new_admission_disabled"
    assert excinfo.value.path_id == "omnigent.legacy.closed"


def test_canonical_surfaces_are_never_subject_to_retirement_admission() -> None:
    assert assert_surface_admits_new_work("realizer:generic-omnigent-host@1") is None


def test_code_owned_retirement_state_matches_product_selection_state() -> None:
    """A realizer's registry deprecation must match its retirement class."""

    from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS

    for ref, entry in KNOWN_REALIZERS.items():
        record = None
        for path in RETIREMENT_INVENTORY:
            if f"realizer:{ref}" in path.surfaces:
                record = path
                break
        if record is None:
            # The canonical generic realizer carries no retirement row.
            assert entry["deprecated"] is False, ref
            continue
        assert entry["deprecated"] is not record.admits_new_work, (
            f"realizer {ref} is registered deprecated={entry['deprecated']} but "
            f"its retirement class is {record.retirement_class.value}"
        )


def test_runtime_selection_consults_the_retirement_class(monkeypatch) -> None:
    from moonmind.omnigent import legacy_retirement
    from moonmind.omnigent.cutover import CutoverPhase, select_runtime

    record = get_retirement_record("omnigent.legacy.direct_codex_launch")
    closed = record.model_copy(
        update={
            "retirement_class": RetirementClass.NEW_ADMISSION_DISABLED,
            "new_admission_source": "",
        }
    )
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_SURFACE, "runtime-strategy:codex_cli", closed
    )
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_ID,
        "omnigent.legacy.direct_codex_launch",
        closed,
    )

    with pytest.raises(ValueError, match="runtime_new_admission_disabled"):
        select_runtime(
            authored_runtime="codex_cli",
            configured_default="codex_cli",
            phase=CutoverPhase.OPT_IN,
        )
    # Omnigent remains selectable: closing legacy admission never blocks the
    # canonical destination.
    assert (
        select_runtime(
            authored_runtime="omnigent",
            configured_default="codex_cli",
            phase=CutoverPhase.OPT_IN,
        ).runtime_id
        == "omnigent"
    )


# --------------------------------------------------- obsolete configuration


def test_generic_host_image_aliases_are_in_their_deprecation_window() -> None:
    rows = {
        entry.variable: entry
        for entry in OBSOLETE_CONFIGURATION
        if entry.retirement_path_id == "omnigent.legacy.host_image_variable_alias"
    }
    assert set(rows) == {
        "OMNIGENT_HOST_IMAGE_REF",
        "OMNIGENT_HOST_IMAGE",
        "OMNIGENT_HOST_IMAGE_TAG",
    }
    for variable, entry in rows.items():
        # Still honored: a pin warns at startup but never fails it.
        warnings = assert_obsolete_configuration({variable: "registry.test/pin:1"})
        assert len(warnings) == 1, variable
        assert "OMNIGENT_SHARED_HOST_IMAGE_REF" in warnings[0], variable
        assert not entry.removed, variable


def test_a_shipped_default_is_equivalent_to_omitting_the_variable() -> None:
    removed = (
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_TAG",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_TAG",
            deprecated=True,
            removed=True,
            guidance="Pin OMNIGENT_SHARED_HOST_IMAGE_REF instead.",
            shippedDefault="latest",
        ),
    )
    assert (
        assert_obsolete_configuration(
            {"OMNIGENT_HOST_IMAGE_TAG": " latest "}, configuration=removed
        )
        == ()
    )
    with pytest.raises(ObsoleteConfigurationError, match="OMNIGENT_HOST_IMAGE_TAG"):
        assert_obsolete_configuration(
            {"OMNIGENT_HOST_IMAGE_TAG": "2026.11.1"}, configuration=removed
        )


def test_compose_injected_defaults_never_count_as_obsolete_configuration() -> None:
    """Compose injects these defaults into processes that never chose them.

    Even after every row is removed, a default Compose still supplies must not
    fail startup, or the default deployment could not start.
    """

    compose = (
        pathlib.Path(__file__).resolve().parents[3] / "docker-compose.yaml"
    ).read_text(encoding="utf-8")
    variables = {entry.variable for entry in OBSOLETE_CONFIGURATION}
    injected = {
        (match.group(1), match.group(2))
        for match in re.finditer(r"\$\{([A-Z0-9_]+):-([^}$]*)\}", compose)
        if match.group(1) in variables
    }
    assert ("OMNIGENT_HOST_IMAGE", "ghcr.io/omnigent-ai/omnigent-host") in injected
    removed = tuple(
        entry.model_copy(
            update={"deprecated": True, "removed": True, "guidance": "fixture"}
        )
        for entry in OBSOLETE_CONFIGURATION
    )
    for variable, default in sorted(injected):
        assert (
            assert_obsolete_configuration({variable: default}, configuration=removed)
            == ()
        ), variable


def test_obsolete_configuration_fails_with_an_actionable_message() -> None:
    deprecated = (
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_REF",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            deprecated=True,
            guidance="Repin the shared image digest before the next release.",
        ),
    )
    warnings = assert_obsolete_configuration(
        {"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}, configuration=deprecated
    )
    assert warnings and "OMNIGENT_SHARED_HOST_IMAGE_REF" in warnings[0]
    assert "omnigent.legacy.host_image_variable_alias" in warnings[0]

    removed = (deprecated[0].model_copy(update={"removed": True}),)
    with pytest.raises(ObsoleteConfigurationError, match="no longer honored"):
        assert_obsolete_configuration(
            {"OMNIGENT_HOST_IMAGE_REF": "img@sha256:a"}, configuration=removed
        )
    # An unset obsolete variable never fails startup.
    assert assert_obsolete_configuration({}, configuration=removed) == ()


def test_every_obsolete_configuration_row_records_when_it_is_deleted() -> None:
    """A row's deletion condition lives on its retirement row or on the row."""

    inventory = {path.path_id: path for path in RETIREMENT_INVENTORY}
    for entry in OBSOLETE_CONFIGURATION:
        if entry.retirement_path_id:
            condition = inventory[entry.retirement_path_id].removal_condition
        else:
            condition = entry.removal_condition
        assert condition.strip(), entry.variable


def test_obsolete_configuration_row_has_exactly_one_deletion_owner() -> None:
    with pytest.raises(ValueError, match="removal condition"):
        ObsoleteConfiguration(
            variable="MINIO_BUCKET", replacement="TEMPORAL_ARTIFACT_S3_BUCKET"
        )
    with pytest.raises(ValueError, match="removal condition"):
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_REF",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            removalCondition="Fixture condition.",
        )


RETIRED_ALIASES = {
    "MINIO_ENDPOINT": "TEMPORAL_ARTIFACT_S3_ENDPOINT",
    "MINIO_BUCKET": "TEMPORAL_ARTIFACT_S3_BUCKET",
    "MINIO_ACCESS_KEY": "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID",
    "MINIO_SECRET_KEY": "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY",
    "TEMPORAL_ARTIFACTS_ROOT": "TEMPORAL_ARTIFACT_ROOT",
}


@pytest.mark.parametrize(("alias", "replacement"), sorted(RETIRED_ALIASES.items()))
def test_a_retired_alias_fails_startup_naming_its_replacement(
    alias: str, replacement: str
) -> None:
    """Ignoring a credential alias would silently fall back to defaults (#5341)."""

    expected = f"{alias} is obsolete; set {replacement} instead"
    with pytest.raises(ObsoleteConfigurationError, match=expected):
        assert_obsolete_configuration({alias: "deployment-value"})
    # Settings read environment names case-insensitively, so every spelling
    # the alias was honored under is rejected.
    with pytest.raises(ObsoleteConfigurationError, match=replacement):
        assert_obsolete_configuration({alias.lower(): "deployment-value"})
    assert assert_obsolete_configuration({alias: "  "}) == ()


def test_silo_server_and_test_endpoint_names_are_not_retired_aliases() -> None:
    assert (
        assert_obsolete_configuration(
            {
                "MINIO_ROOT_USER": "operator",
                "MINIO_ROOT_PASSWORD": "operator-secret",
                "MINIO_API_PORT": "9100",
                "MINIO_CONSOLE_PORT": "9101",
                "MOONMIND_TEST_MINIO_ENDPOINT": "http://minio:9000",
            }
        )
        == ()
    )


def test_a_retired_long_term_memory_provider_fails_startup() -> None:
    with pytest.raises(ObsoleteConfigurationError, match="MEMORY_LONG_TERM"):
        assert_obsolete_configuration({"MEMORY_LONG_TERM": "mem0"})
    # The template shipped ``off``, which selects what omitting it selects.
    for shipped in ("off", " OFF ", ""):
        assert assert_obsolete_configuration({"MEMORY_LONG_TERM": shipped}) == ()


def test_obsolete_configuration_cannot_skip_its_deprecation_window() -> None:
    with pytest.raises(ValueError, match="deprecation window"):
        ObsoleteConfiguration(
            variable="OMNIGENT_HOST_IMAGE_REF",
            retirementPathId="omnigent.legacy.host_image_variable_alias",
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            removed=True,
        )


# #5340: retired rollout controls and their previous defaults.
RETIRED_ROLLOUT_CONTROLS = {
    "PUBLICATION_RECOVERY_SHADOW": "false",
    "PUBLICATION_RECOVERY_CANARY_REPOSITORIES": "",
    "PUBLICATION_RECOVERY_CANARY_OWNER_IDS": "",
    "PUBLICATION_RECOVERY_GENERATION": "default",
    "CONTROL_STOP_CONTINUATION_ENABLED": "false",
    "CONTROL_STOP_CONTINUATION_SHADOW": "false",
    "CONTROL_STOP_CONTINUATION_CANARY_OWNER_IDS": "",
    "CONTROL_STOP_CONTINUATION_ALLOWED_PROVIDER_PROFILE_IDS": "",
    "CONTROL_STOP_CONTINUATION_ALLOWED_EXECUTION_PROFILE_REFS": "",
    "CONTROL_STOP_CONTINUATION_ALLOWED_LAUNCH_POLICY_REFS": "",
    "CONTROL_STOP_CONTINUATION_GENERATION": "disabled",
    "LIVE_LOGS_SESSION_TIMELINE_ROLLOUT": "off",
    "LIVE_LOGS_STRUCTURED_HISTORY_ENABLED": "true",
}


@pytest.mark.parametrize("prefix", ("", "FEATURE_FLAGS__"))
@pytest.mark.parametrize(
    ("variable", "previous_default"), sorted(RETIRED_ROLLOUT_CONTROLS.items())
)
def test_a_retired_rollout_control_fails_startup_unless_it_keeps_its_default(
    prefix: str, variable: str, previous_default: str
) -> None:
    """A kept non-default value would otherwise change behavior silently, as
    PUBLICATION_RECOVERY_SHADOW=true would turn into live publication."""

    name = f"{prefix}{variable}"
    with pytest.raises(ObsoleteConfigurationError, match=f"{name} is obsolete"):
        assert_obsolete_configuration({name: "deployment-value"})
    # The previous default selects what omitting the variable now selects.
    assert assert_obsolete_configuration({name: previous_default}) == ()
