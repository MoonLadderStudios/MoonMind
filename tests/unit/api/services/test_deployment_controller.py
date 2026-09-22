"""Controller-backed deployment operations for MoonLadderStudios/MoonMind#4502.

Settings Operations, the host update command, and the typed update Skill/tool
all submit or observe the same controller operation. An update is not a
``MoonMind.UserWorkflow``: submission works with Temporal stopped and never
creates a workflow or application artifact as a prerequisite.

The controller owns the durable operation identity. Duplicate submission and
lost acknowledgment reattach to the same operation instead of launching a
second updater. An explicit retry starts a fresh bounded attempt while
preserving the first failure. Changed targets are explicit new intent.
"""

from __future__ import annotations

import pytest

from api_service.services.deployment_controller import (
    DeploymentControllerClient,
    DeploymentControllerError,
)


def _request(**overrides):
    payload = {
        "stack": "moonmind",
        "repository": "ghcr.io/moonladderstudios/moonmind",
        "reference": "stable",
        "mode": "changed_services",
        "reason": "routine update",
        "operation_kind": "update",
    }
    payload.update(overrides)
    return payload


def test_submit_creates_durable_controller_operation(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state")

    first = client.submit(_request(), operator="admin@example.com")
    second = client.submit(_request(), operator="admin@example.com")

    assert first.operation_id.startswith("depupd_")
    # Lost acknowledgment (client timeout / browser refresh) reattaches to the
    # same operation instead of launching a second updater.
    assert second.operation_id == first.operation_id
    assert second.status in {"ACCEPTED", "QUEUED"}
    assert "workflow" not in first.operation_id.lower()
    assert client.mutation_owner_count(first.operation_id) == 1


def test_changed_target_is_explicit_new_intent(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state")

    first = client.submit(_request(reference="stable"), operator="admin")
    second = client.submit(_request(reference="latest"), operator="admin")

    assert second.operation_id != first.operation_id


def test_explicit_retry_starts_fresh_attempt_and_preserves_first_failure(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state")

    original = client.submit(_request(), operator="admin")
    client.record_result(
        original.operation_id,
        status="FAILED",
        error="postcheck: operator route unreachable",
    )

    retried = client.retry(original.operation_id, operator="admin")

    assert retried.operation_id != original.operation_id
    assert retried.retry_of == original.operation_id
    assert retried.attempt == 2
    observed = client.observe(retried.operation_id)
    assert observed.first_error == "postcheck: operator route unreachable"
    # The original failure is preserved on the original record as well.
    assert client.observe(original.operation_id).error == (
        "postcheck: operator route unreachable"
    )


def test_retry_exhaustion_does_not_reuse_temporal_budget(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state", max_attempts=2)

    original = client.submit(_request(), operator="admin")
    client.record_result(original.operation_id, status="FAILED", error="boom")
    second = client.retry(original.operation_id, operator="admin")
    client.record_result(second.operation_id, status="FAILED", error="boom again")

    with pytest.raises(DeploymentControllerError) as excinfo:
        client.retry(second.operation_id, operator="admin")
    assert excinfo.value.code == "controller_retry_exhausted"


def test_access_denied_without_credential_exposure(tmp_path):
    client = DeploymentControllerClient(
        state_dir=tmp_path / "state",
        controller_secret="s3cr3t-controller-value",
    )

    with pytest.raises(DeploymentControllerError) as excinfo:
        client.submit(_request(), operator="admin", credential="wrong-secret")

    assert excinfo.value.code == "controller_access_denied"
    assert "s3cr3t-controller-value" not in str(excinfo.value)


def test_controller_unavailable_is_distinct(tmp_path):
    client = DeploymentControllerClient(
        state_dir=tmp_path / "state",
        available=False,
    )

    with pytest.raises(DeploymentControllerError) as excinfo:
        client.submit(_request(), operator="admin")

    assert excinfo.value.code == "controller_unavailable"


def test_optional_history_import_cannot_change_confirmed_outcome(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state")
    submitted = client.submit(_request(), operator="admin")
    client.record_result(
        submitted.operation_id, status="SUCCEEDED", resolved_digest="sha256:abc"
    )

    observed = client.record_history_import(
        submitted.operation_id, imported=False, error="artifact store offline"
    )

    assert observed.status == "SUCCEEDED"
    assert observed.error is None
    assert observed.history_import is not None
    assert observed.history_import["imported"] is False


def test_redacted_logs_never_carry_secrets(tmp_path):
    client = DeploymentControllerClient(state_dir=tmp_path / "state")
    submitted = client.submit(_request(), operator="admin")

    client.append_log(
        submitted.operation_id,
        "pull failed with token=abcdef123456 for https://user:pw@ghcr.io/v2/x",
    )
    observed = client.observe(submitted.operation_id)

    assert "abcdef123456" not in observed.logs_text
    assert "pw@" not in observed.logs_text
    assert "pull failed" in observed.logs_text
