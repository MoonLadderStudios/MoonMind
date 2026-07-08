"""MM-1153: unified docs/Omnigent/OmnigentBridge.md §17 failure classifier.

Source issue traceability: MM-1140 -> MM-1153. These tests pin every row of the
§17 error-classification table to its specified MoonMind failure class through
the one reusable classifier (OB-§17 / DESIGN-REQ-010).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.failure_classification import (
    OMNIGENT_FAILURE_CLASS_TABLE,
    OmnigentFailureReason,
    classify_omnigent_failure,
    classify_omnigent_http_status,
    failure_class_for_terminal_status,
)

# The canonical §17 table (docs/Omnigent/OmnigentBridge.md §17). `None` marks the
# "completed with diagnostics" row.
SECTION_17_TABLE = {
    OmnigentFailureReason.UPSTREAM_UNREACHABLE: "integration_error",
    OmnigentFailureReason.HOST_REGISTER_CONNECT: "integration_error",
    OmnigentFailureReason.AUTH_FAILURE: "integration_error",
    OmnigentFailureReason.INVALID_SESSION_PAYLOAD: "user_error",
    OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH: "user_error",
    OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION: "integration_error",
    OmnigentFailureReason.STREAM_DISCONNECT_ACTIVE: "integration_error",
    OmnigentFailureReason.RUNTIME_HARNESS_FAILURE: "execution_error",
    OmnigentFailureReason.SESSION_HOST_TIMEOUT: "system_error",
    OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED: None,
    OmnigentFailureReason.REQUIRED_ARTIFACT_PERSISTENCE_FAILED: "system_error",
}


def test_table_covers_every_section_17_reason() -> None:
    """The classifier table must contain exactly the §17 rows."""

    assert set(OMNIGENT_FAILURE_CLASS_TABLE) == set(OmnigentFailureReason)
    assert set(SECTION_17_TABLE) == set(OmnigentFailureReason)


@pytest.mark.parametrize(("reason", "expected"), list(SECTION_17_TABLE.items()))
def test_every_section_17_row_maps_via_one_classifier(
    reason: OmnigentFailureReason,
    expected: str | None,
) -> None:
    assert classify_omnigent_failure(reason) == expected


def test_timed_out_is_system_error_and_not_collapsed_into_failed() -> None:
    assert failure_class_for_terminal_status("timed_out") == "system_error"
    assert failure_class_for_terminal_status("failed") == "execution_error"
    assert failure_class_for_terminal_status("timed_out") != (
        failure_class_for_terminal_status("failed")
    )


def test_terminal_status_mapping_preserves_existing_behavior() -> None:
    assert failure_class_for_terminal_status("completed") is None
    assert failure_class_for_terminal_status("canceled") == "system_error"
    assert failure_class_for_terminal_status("failed") == "execution_error"
    # Unexpected/degraded status values default to integration_error.
    assert failure_class_for_terminal_status("mystery") == "integration_error"
    assert failure_class_for_terminal_status("") == "integration_error"


def test_optional_resource_harvest_completed_with_diagnostics_by_default() -> None:
    assert (
        classify_omnigent_failure(
            OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED
        )
        is None
    )


def test_optional_resource_harvest_escalates_when_full_evidence_required() -> None:
    assert (
        classify_omnigent_failure(
            OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED,
            require_full_evidence=True,
        )
        == "system_error"
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, "user_error"),
        (404, "user_error"),
        (409, "user_error"),
        (422, "user_error"),
        (401, "integration_error"),
        (403, "integration_error"),
        (429, "integration_error"),
        (500, "integration_error"),
        (503, "integration_error"),
    ],
)
def test_http_status_classifier_matches_section_17_rows(
    status_code: int,
    expected: str,
) -> None:
    assert classify_omnigent_http_status(status_code) == expected


def test_digest_mismatch_and_invalid_payload_share_the_user_error_class() -> None:
    """§17 distinguishes user_error from integration_error / execution_error."""

    invalid_payload = classify_omnigent_failure(
        OmnigentFailureReason.INVALID_SESSION_PAYLOAD
    )
    digest_mismatch = classify_omnigent_failure(
        OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH
    )
    assert invalid_payload == digest_mismatch == "user_error"
    assert invalid_payload != classify_omnigent_failure(
        OmnigentFailureReason.RUNTIME_HARNESS_FAILURE
    )
    assert invalid_payload != classify_omnigent_failure(
        OmnigentFailureReason.UPSTREAM_UNREACHABLE
    )
