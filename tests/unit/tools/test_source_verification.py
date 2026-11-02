from copy import deepcopy

import pytest

from tools.ci.source_verification import (
    SOURCE_OWNERS,
    artifact_attempt,
    source_receipt,
    validate_source_receipt,
)

IDENTITY = dict(revision="a" * 40, run_id="123", attempt="2")


def test_retained_job_artifacts_require_an_explicit_admitted_attempt():
    assert (
        artifact_attempt(
            "source-verification-123-1",
            prefix="source-verification",
            run_id="123",
            current_attempt="2",
        )
        == "1"
    )
    for name in (
        "source-verification-123-3",
        "source-verification-124-1",
        "source-verification-123-0",
        "source-verification-123-1\n",
    ):
        with pytest.raises(ValueError):
            artifact_attempt(
                name, prefix="source-verification", run_id="123", current_attempt="2"
            )


def test_complete_current_source_is_reusable_only_for_the_same_run_and_attempt():
    receipt = source_receipt(
        **IDENTITY, selection=dict.fromkeys(SOURCE_OWNERS, "true"), result="success"
    )
    validate_source_receipt(receipt, **IDENTITY)
    for key, value in (
        ("revision", "b" * 40),
        ("runId", "124"),
        ("attempt", "1"),
        ("status", "skipped"),
        ("owners", list(SOURCE_OWNERS)[:-1]),
    ):
        changed = deepcopy(receipt)
        changed[key] = value
        with pytest.raises(ValueError):
            validate_source_receipt(changed, **IDENTITY)


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_incomplete_source_never_yields_a_receipt(result):
    with pytest.raises(ValueError):
        source_receipt(
            **IDENTITY, selection=dict.fromkeys(SOURCE_OWNERS, "true"), result=result
        )


def test_an_omitted_owner_is_not_covered_by_an_aggregate_success():
    for missing in SOURCE_OWNERS:
        selection = dict.fromkeys(SOURCE_OWNERS, "true")
        selection[missing] = "false"
        with pytest.raises(ValueError):
            source_receipt(**IDENTITY, selection=selection, result="success")
