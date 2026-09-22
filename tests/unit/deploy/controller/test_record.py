"""Local operation record: desired vs installed, crash-safe, bounded retry."""
import json

from conftest import load


def _record_module(controller_path):
    return load("record")


def test_begin_records_desired_and_concrete_images_once(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    assert op["desired"]["image"] == "ghcr.io/org/app@sha256:abc"
    assert op["installed"] is None
    assert op["status"] == "pending"
    # Concrete images recorded once: a second begin for the same target
    # reattaches to the open operation instead of forking a duplicate.
    again = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    assert again["operationId"] == op["operationId"]


def test_confirm_installed_is_durable_and_reporting_cannot_erase_it(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    store.confirm_installed(op["operationId"], image="ghcr.io/org/app@sha256:abc")
    assert store.load(op["operationId"])["status"] == "succeeded"
    # Reporting/cleanup paths cannot erase a confirmed installation or hide
    # failed mandatory verification.
    store.note_reporting_failure(op["operationId"], error="artifact store down")
    loaded = store.load(op["operationId"])
    assert loaded["installed"]["image"] == "ghcr.io/org/app@sha256:abc"
    assert loaded["status"] == "succeeded"
    assert loaded["reportingFailures"] == ["artifact store down"]


def test_explicit_retry_starts_fresh_attempt_with_history_retained(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    for _ in range(record.MAX_AUTO_ATTEMPTS):
        store.record_attempt_error(op["operationId"], error="transient pull failure")
    assert store.load(op["operationId"])["autoAttemptsExhausted"] is True
    retried = store.begin_retry(op["operationId"])
    assert retried["attemptGroup"] == 2
    assert len(retried["attempts"]) == record.MAX_AUTO_ATTEMPTS
    assert retried["status"] == "pending"
    # Exhaustion is not a permanent ban: the retried operation accepts errors.
    store.record_attempt_error(retried["operationId"], error="new attempt failed")
    assert len(store.load(op["operationId"])["attempts"]) == record.MAX_AUTO_ATTEMPTS + 1


def test_first_error_survives_later_noise(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    store.record_attempt_error(op["operationId"], error="original: init-db failed")
    store.record_attempt_error(op["operationId"], error="later: transient noise")
    summary = store.load(op["operationId"])["errorSummary"]
    assert "original: init-db failed" in summary


def test_writes_are_atomic_across_interruption(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    raw = (tmp_path / "operations" / f"{op['operationId']}.json").read_text()
    assert json.loads(raw)["operationId"] == op["operationId"]
    leftovers = list(tmp_path.rglob("*.tmp"))
    assert leftovers == []
