"""The published protected execution-support index.

Source issue: MoonLadderStudios/MoonMind#3885.

These cover the derivation the protected live-conformance publish job runs
(`.github/workflows/omnigent-live-conformance.yml`). Asserting the workflow
calls this module is a separate contract test; what the module produces is
asserted here, because an index shape checked only as YAML text is not evidence
that the index is correct.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.execution_support_evidence import (
    validate_protected_execution_support_evidence,
)
from moonmind.omnigent.execution_support_publication import (
    EXECUTION_SUPPORT_INDEX_VERSION,
    build_protected_support_index,
    load_concurrency_records,
    published_statuses,
    write_protected_support_index,
)
from moonmind.omnigent.session_supervisor_rollback import (
    SUPERVISOR_ROLLBACK_POLICY_VERSION,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_COMPATIBILITY_VERSION,
    OMNIGENT_SESSION_FEATURE_GENERATION,
)
from tests.unit.omnigent.support_evidence_fixtures import (
    concurrency_record,
    support_identity,
    support_plan,
)

_SOURCE_COMMIT = "abcdef1234567890"


def _combination(plan, *, status: str = "passed", host_mode: str = "on_demand"):
    identity = plan.supportIdentity.model_dump(mode="json", by_alias=True)
    return {
        "status": status,
        "hostMode": host_mode,
        "hostImageRef": plan.hostImageRef,
        "bindingIdentity": {
            **identity,
            "supportCombinationKey": plan.supportCombinationKey,
            "policySnapshotDigest": plan.policySnapshotDigest,
            "effectiveLaunchSnapshotDigest": plan.effectiveLaunchSnapshotDigest,
            "providerProfileClass": "own-auth",
        },
    }


def _manifest(combinations: dict, *, generated_at: datetime | None = None) -> dict:
    now = generated_at or datetime.now(UTC)
    return {
        "sourceCommit": _SOURCE_COMMIT,
        "generatedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=7)).isoformat(),
        "combinations": combinations,
    }


def _build(manifest, **overrides):
    kwargs = {
        "protected_run_ref": "https://example.invalid/actions/runs/9",
        "evidence_manifest_ref": "workflow_chat/workflow-chat-acceptance.json",
        "evidence_manifest_digest": "sha256:" + "b" * 64,
        "policy_gate_ref_prefix": "github-actions:omnigent-live-conformance/9/1",
        "feature_generation": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replay_compatibility_version": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollback_policy_version": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }
    kwargs.update(overrides)
    return build_protected_support_index(manifest, **kwargs)


# --- Non-pass rows ----------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["failed", "skipped", "blocked", "unavailable", "partial"]
)
def test_a_combination_that_did_not_pass_is_recorded_not_omitted(status: str) -> None:
    """An operator must be able to tell "failed" from "never attempted"."""

    passing = support_plan()
    failing = support_plan(support_identity(model_digest="sha256:" + "c" * 64))
    index = _build(
        _manifest(
            {
                "codex-primary": _combination(passing),
                "opencode-secondary": _combination(failing, status=status),
            }
        )
    )

    statuses = published_statuses(index)
    assert statuses[passing.supportCombinationKey] == "passed"
    assert statuses[failing.supportCombinationKey] == status
    assert index["schemaVersion"] == EXECUTION_SUPPORT_INDEX_VERSION


@pytest.mark.parametrize(
    "status", ["failed", "skipped", "blocked", "unavailable", "partial"]
)
def test_a_recorded_non_pass_row_can_never_be_admitted(status: str) -> None:
    """Widening what is recorded must never widen what is admitted."""

    failing = support_plan(support_identity(model_digest="sha256:" + "c" * 64))
    index = _build(
        _manifest(
            {
                "codex-primary": _combination(support_plan()),
                "opencode-secondary": _combination(failing, status=status),
            }
        )
    )
    entry = next(
        item
        for item in index["entries"]
        if item["supportCombinationKey"] == failing.supportCombinationKey
    )

    assert entry["policyQualified"] is False
    with pytest.raises(ValueError, match="did not pass"):
        validate_protected_execution_support_evidence(entry)


def test_an_unrecognized_outcome_is_recorded_as_failed_not_dropped() -> None:
    failing = support_plan(support_identity(model_digest="sha256:" + "c" * 64))
    index = _build(
        _manifest(
            {
                "codex-primary": _combination(support_plan()),
                "opencode-secondary": _combination(failing, status="exploded"),
            }
        )
    )

    assert published_statuses(index)[failing.supportCombinationKey] == "failed"


def test_a_combination_that_claims_no_capability_is_not_a_row() -> None:
    """`unsupported` has no exact identity, so there is nothing to file.

    The protected acceptance manifest reports a combination that never claimed
    native chat as ``unsupported`` and refuses to give it a ``bindingIdentity``
    at all. Publishing it would have to invent a ``supportCombinationKey``
    nothing ran under, and the old publisher's blanket "skip anything that did
    not pass" hid that distinction behind the same branch that also dropped
    genuine failures.
    """

    passing = support_plan()
    index = _build(
        _manifest(
            {
                "codex-primary": _combination(passing),
                "claude-unclaimed": {
                    "status": "unsupported",
                    "unsupportedReason": "native chat is not claimed",
                },
            }
        )
    )

    assert list(published_statuses(index)) == [passing.supportCombinationKey]


def test_an_index_with_no_passing_combination_is_refused() -> None:
    """The index is admission authority; publishing one with no pass is a bug."""

    with pytest.raises(ValueError, match="no passing combinations"):
        _build(_manifest({"codex-primary": _combination(support_plan(), status="failed")}))


def test_an_empty_or_unsourced_manifest_is_refused() -> None:
    with pytest.raises(ValueError, match="no combinations"):
        _build(_manifest({}))
    manifest = _manifest({"codex-primary": _combination(support_plan())})
    manifest["sourceCommit"] = "  "
    with pytest.raises(ValueError, match="no source commit"):
        _build(manifest)


# --- Concurrency dimension --------------------------------------------------


def test_a_passing_row_carries_the_concurrency_record_it_qualified() -> None:
    from moonmind.omnigent.concurrency_qualification import (
        ConcurrencyQualificationRecord,
    )
    from moonmind.omnigent.execution_support_evidence import (
        advertised_concurrency_ceiling,
    )

    plan = support_plan()
    record = ConcurrencyQualificationRecord.model_validate(
        concurrency_record(plan, level=4)
    )
    index = _build(
        _manifest({"codex-primary": _combination(plan)}),
        concurrency_records=[record],
    )

    entry = index["entries"][0]
    assert entry["concurrency"]["identity"]["supportCombinationKey"] == (
        plan.supportCombinationKey
    )
    admitted = validate_protected_execution_support_evidence(entry)
    assert advertised_concurrency_ceiling(admitted) == 4


def test_a_concurrency_record_for_another_combination_is_refused() -> None:
    """Evidence does not generalize across exact combinations."""

    from moonmind.omnigent.concurrency_qualification import (
        ConcurrencyQualificationRecord,
    )

    published = support_plan()
    other = support_plan(support_identity(model_digest="sha256:" + "c" * 64))
    record = ConcurrencyQualificationRecord.model_validate(
        concurrency_record(other, level=4)
    )

    with pytest.raises(ValueError, match="did not publish"):
        _build(
            _manifest({"codex-primary": _combination(published)}),
            concurrency_records=[record],
        )


def test_a_non_pass_row_never_carries_admission_relevant_concurrency() -> None:
    """A combination that failed cannot publish a validated peak."""

    from moonmind.omnigent.concurrency_qualification import (
        ConcurrencyQualificationRecord,
    )

    passing = support_plan()
    failing = support_plan(support_identity(model_digest="sha256:" + "c" * 64))
    record = ConcurrencyQualificationRecord.model_validate(
        concurrency_record(failing, level=4)
    )

    index = _build(
        _manifest(
            {
                "codex-primary": _combination(passing),
                "opencode-secondary": _combination(failing, status="failed"),
            }
        ),
        concurrency_records=[record],
    )

    entry = next(
        item
        for item in index["entries"]
        if item["supportCombinationKey"] == failing.supportCombinationKey
    )
    # The row is still published so the failure is visible, but it carries no
    # validated peak: a failed combination advertises nothing.
    assert entry["status"] == "failed"
    assert entry.get("concurrency") is None


def test_publishing_without_a_concurrency_record_leaves_the_dimension_absent() -> None:
    """Absent means "no level is validated", which is not an implicit one."""

    from moonmind.omnigent.execution_support_evidence import (
        advertised_concurrency_ceiling,
    )

    index = _build(_manifest({"codex-primary": _combination(support_plan())}))

    entry = index["entries"][0]
    assert entry.get("concurrency") is None
    assert advertised_concurrency_ceiling(
        validate_protected_execution_support_evidence(entry)
    ) == 0


# --- Staged record loading --------------------------------------------------


def test_layer_records_for_one_combination_merge_into_one_record(tmp_path) -> None:
    """Two single-layer records validate nothing until they are one record."""

    from moonmind.omnigent.concurrency_qualification import (
        ConcurrencyQualificationLayer,
    )

    plan = support_plan()
    full = concurrency_record(plan, level=4)
    hermetic = dict(full)
    hermetic["rows"] = [
        row
        for row in full["rows"]
        if row["layer"] == ConcurrencyQualificationLayer.hermetic.value
    ]
    exact = dict(full)
    exact["rows"] = [
        row
        for row in full["rows"]
        if row["layer"] == ConcurrencyQualificationLayer.exact_docker.value
    ]
    (tmp_path / "hermetic").mkdir()
    (tmp_path / "exact").mkdir()
    (tmp_path / "hermetic" / "record.json").write_text(
        json.dumps(hermetic), encoding="utf-8"
    )
    (tmp_path / "exact" / "record.json").write_text(
        json.dumps(exact), encoding="utf-8"
    )
    # A layer's evidence directory is staged beside its record and must be
    # ignored rather than parsed as one.
    (tmp_path / "hermetic" / "overlap.json").write_text(
        json.dumps({"schemaVersion": "something-else"}), encoding="utf-8"
    )

    records = load_concurrency_records(tmp_path)

    assert len(records) == 1
    assert records[0].validated_concurrency_level == 4


def test_records_from_different_substrates_are_a_conflict(tmp_path) -> None:
    plan = support_plan()
    first = concurrency_record(plan, level=4)
    second = json.loads(json.dumps(first))
    second["identity"]["workerTopologyRef"] = "two-replicas@1"
    (tmp_path / "a.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(ValueError, match="disagree about the substrate identity"):
        load_concurrency_records(tmp_path)


def test_no_staged_records_is_not_a_failure(tmp_path) -> None:
    assert load_concurrency_records(tmp_path / "absent") == ()
    assert load_concurrency_records(tmp_path) == ()


def test_the_written_index_round_trips_through_admission(tmp_path) -> None:
    plan = support_plan()
    index = _build(_manifest({"codex-primary": _combination(plan)}))
    path = write_protected_support_index(
        index, tmp_path / "nested" / "execution-support-evidence.json"
    )

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == index
    validate_protected_execution_support_evidence(reloaded["entries"][0])
