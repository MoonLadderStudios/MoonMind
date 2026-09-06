"""Gate-owned required-shard regressions for issue #4024.

Proves the frozen 42-claim map, the bounded risk matrix, run-record
honesty, aggregate no-masking behavior, and selector/collection of this
gate's own regressions. Sibling-dependent conformance (credential
authority, capture/restore, publication, clean install) stays explicitly
unimplemented in the baseline; these tests pin that the gate reports red
until those slices land.
"""

import pytest

from moonmind.repositories.access_gate.baseline import (
    ASSESSED_HEAD,
    CLAIM_IDS_42,
    CONSUMER_ROWS,
    FROZEN_DESIGN_REVISION,
    FROZEN_PLAN_BASELINE,
    ClaimStatus,
    coverage_delta,
    frozen_claims,
)
from moonmind.repositories.access_gate.risk_matrix import (
    advertised_combinations,
    negative_owner_for,
    positive_owner_for,
)
from moonmind.repositories.access_gate.run_record import (
    ExecutionClass,
    GateAggregate,
    RunRecord,
    aggregate_gate_result,
)
from tools.select_test_suites import select_suites


def _outputs(paths):
    return select_suites(paths, event_name="pull_request").as_outputs()


def test_frozen_revisions_are_pinned():
    assert FROZEN_DESIGN_REVISION == "1dffb29c28de06775ee6028305222c73d911628b"
    assert FROZEN_PLAN_BASELINE == "63bce9852ffa33e33cb0b416bc24a654b1b6f92b"
    assert ASSESSED_HEAD == "7643ae6ab"


def test_claim_count_is_42_with_unique_ids():
    assert len(CLAIM_IDS_42) == 42
    assert len(set(CLAIM_IDS_42)) == 42
    claims = frozen_claims()
    assert [claim.id for claim in claims] == list(CLAIM_IDS_42)


def test_gate_covered_claims_have_exact_entrypoints():
    by_id = {claim.id: claim for claim in frozen_claims()}
    for claim_id in ("TEST-001", "TEST-002", "TEST-003", "TEST-004"):
        claim = by_id[claim_id]
        assert claim.status == ClaimStatus.GATE_COVERED
        assert claim.test_entrypoint == "tests/unit/repositories/test_access_gate.py"
        assert claim.owner and claim.support_combination and claim.evidence_artifact


def test_sibling_dependent_claims_stay_unimplemented_not_green():
    by_id = {claim.id: claim for claim in frozen_claims()}
    # Required App acquisition, source variants, saved-work durability, and
    # publication-only recovery cannot be relabeled out of scope.
    for claim_id in ("CONTRACT-006", "CONTRACT-002", "CONTRACT-012", "CONTRACT-013"):
        claim = by_id[claim_id]
        assert claim.status == ClaimStatus.UNIMPLEMENTED_REQUIRED
        assert claim.test_entrypoint.startswith("pending:")
    unimplemented = [
        claim for claim in frozen_claims()
        if claim.status == ClaimStatus.UNIMPLEMENTED_REQUIRED
    ]
    assert unimplemented, "gate must stay red until sibling slices land"


def test_non_goal_is_unsupported_by_design_not_unimplemented():
    by_id = {claim.id: claim for claim in frozen_claims()}
    assert by_id["NON-GOAL-001"].status == ClaimStatus.UNSUPPORTED_BY_DESIGN


def test_every_claim_has_owner_combination_and_artifact():
    for claim in frozen_claims():
        assert claim.owner, claim.id
        assert claim.support_combination, claim.id
        assert claim.evidence_artifact, claim.id
        assert claim.status in (
            ClaimStatus.GATE_COVERED,
            ClaimStatus.UNIMPLEMENTED_REQUIRED,
            ClaimStatus.UNSUPPORTED_BY_DESIGN,
        ), claim.id


def test_consumer_inventory_is_complete_with_no_singleton_discovery():
    assert len(CONSUMER_ROWS) == 9
    for row in CONSUMER_ROWS:
        assert row.boundary and row.owner
        assert row.test_entrypoint.startswith("pending:")
        assert row.status == ClaimStatus.UNIMPLEMENTED_REQUIRED


def test_design_change_requires_coverage_delta_review():
    assert coverage_delta(FROZEN_DESIGN_REVISION).requires_review is False
    delta = coverage_delta("deadbeef" * 5)
    assert delta.requires_review is True
    assert delta.frozen_revision == FROZEN_DESIGN_REVISION


def test_every_advertised_lane_has_positive_and_negative_owners():
    combinations = advertised_combinations()
    assert combinations
    for combination in combinations:
        assert combination.positive_journey_owner
        assert combination.negative_recovery_owner
        assert combination.shared_substrate
        assert positive_owner_for(
            combination.runtime,
            combination.source,
            combination.access,
            combination.output_publication,
        ) == combination.positive_journey_owner
        assert negative_owner_for(
            combination.runtime,
            combination.source,
            combination.access,
            combination.output_publication,
        ) == combination.negative_recovery_owner


def test_git_lore_authority_and_credentialless_keyed_lanes_preserved():
    lanes = {(c.source, c.access) for c in advertised_combinations()}
    assert ("repository", "lore-authoritative") in lanes
    assert ("scratch", "credentialless") in lanes
    assert ("scratch", "keyed-provider-profile") in lanes


def test_unadvertised_combination_fails_lookup_not_silent_pass():
    with pytest.raises(KeyError):
        positive_owner_for("unknown-runtime", "scratch", "credentialless", "none")
    with pytest.raises(KeyError):
        negative_owner_for("unknown-runtime", "scratch", "credentialless", "none")


def test_run_record_validates_real_postgres_lane():
    record = RunRecord(
        scenario="concurrent pins race deletion",
        execution_class=ExecutionClass.REAL_POSTGRES_OBJECT_STORE,
        source_revision="abc123",
        db_engine="postgresql",
        db_version="16",
        artifact_backend="s3-compatible",
    )
    record.validate()
    assert record.to_dict()["db_engine"] == "postgresql"


def test_postgres_named_run_on_sqlite_is_rejected():
    record = RunRecord(
        scenario="postgres race",
        execution_class=ExecutionClass.HELPER_IN_MEMORY,
        db_engine="postgresql",
    )
    with pytest.raises(ValueError, match="does not prove PostgreSQL races"):
        record.validate()


def test_helper_results_cannot_carry_real_run_identity():
    record = RunRecord(
        scenario="helper check",
        execution_class=ExecutionClass.HELPER_IN_MEMORY,
        image_digest="sha256:abc",
    )
    with pytest.raises(ValueError, match="helper/in-memory"):
        record.validate()


def test_live_verification_must_name_provider_adapter():
    record = RunRecord(
        scenario="live smoke",
        execution_class=ExecutionClass.LIVE_PROTECTED,
    )
    with pytest.raises(ValueError, match="provider adapter"):
        record.validate()


def test_unknown_execution_class_fails_closed():
    with pytest.raises(ValueError, match="unknown execution_class"):
        RunRecord(scenario="x", execution_class="mystery").validate()


def test_aggregate_never_masks_selected_failure_skip_or_missing_artifact():
    ok = GateAggregate("a", selected=True, passed=True, skipped=False,
                       artifacts_complete=True)
    assert aggregate_gate_result((ok,)) is True
    failed = GateAggregate("a", selected=True, passed=False, skipped=False,
                           artifacts_complete=True)
    assert aggregate_gate_result((failed,)) is False
    skipped = GateAggregate("a", selected=True, passed=True, skipped=True,
                            artifacts_complete=True)
    assert aggregate_gate_result((skipped,)) is False
    missing = GateAggregate("a", selected=True, passed=True, skipped=False,
                            artifacts_complete=False)
    assert aggregate_gate_result((missing,)) is False
    assert aggregate_gate_result((ok, failed)) is False


def test_empty_selection_fails_closed_for_unknown_changes():
    assert aggregate_gate_result(()) is False
    unselected = GateAggregate("a", selected=False, passed=True, skipped=False,
                               artifacts_complete=True)
    assert aggregate_gate_result((unselected,)) is False


def test_gate_module_changes_select_unit_fast():
    outputs = _outputs([
        "moonmind/repositories/access_gate/baseline.py",
        "moonmind/repositories/access_gate/risk_matrix.py",
        "moonmind/repositories/access_gate/run_record.py",
        "tests/unit/repositories/test_access_gate.py",
    ])
    assert outputs["unit_fast"] == "true"


def test_gate_test_file_is_collected_in_unit_fast_shard():
    import pathlib
    assert pathlib.Path("tests/unit/repositories/test_access_gate.py").exists()
