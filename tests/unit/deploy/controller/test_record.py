"""Local operation record: desired vs installed, crash-safe, bounded retry."""
import json
from datetime import datetime, timedelta

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
    for field in ("createdAt", "updatedAt"):
        assert datetime.fromisoformat(op[field]).utcoffset() == timedelta(0)
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


def test_exhausted_verification_recovery_keeps_partial_installation(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    operation = store.begin(stack="moonmind", desired_image="img", source_revision="")
    store.confirm_installed(operation["operationId"], image="img", verification_pending=True)
    installed = store.load(operation["operationId"])["installed"]

    for _ in range(record.MAX_AUTO_ATTEMPTS):
        store.record_attempt_error(operation["operationId"], error="verification unavailable")

    final = store.load(operation["operationId"])
    assert final["installed"] == installed
    assert final["status"] == "partially_verified"
    assert final["autoAttemptsExhausted"] is True


def test_retry_is_refused_for_every_non_failed_status(controller_path, tmp_path):
    import pytest

    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    with pytest.raises(RuntimeError):
        store.begin_retry(op["operationId"])  # pending: owned by a writer
    store.supersede(op["operationId"], reason="newer target")
    with pytest.raises(RuntimeError):
        store.begin_retry(op["operationId"])  # superseded: stale intent
    loaded = store.load(op["operationId"])
    assert (loaded["status"], loaded["attemptGroup"]) == ("superseded", 1)

    done = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:def",
        source_revision="def456",
    )
    store.confirm_installed(done["operationId"], image="ghcr.io/org/app@sha256:def")
    with pytest.raises(RuntimeError):
        store.begin_retry(done["operationId"])  # succeeded: already installed
    store.record_verification(done["operationId"], name="service:api", status="failed")
    with pytest.raises(RuntimeError):
        store.begin_retry(done["operationId"])  # partially_verified: installed
    loaded = store.load(done["operationId"])
    assert (loaded["status"], loaded["attemptGroup"]) == ("partially_verified", 1)


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


def test_error_summary_keeps_first_and_latest_causes_ahead_of_compose_progress(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    operation = store.begin(stack="moonmind", desired_image="image", source_revision="")
    progress = " Container moonmind-worker-1 Waiting\n" * 150
    first = f"apply failed: up failed (exit 1)\n{progress}container moonmind-api-1 is unhealthy"
    latest = f"apply failed: up failed (exit 1)\n{progress}container moonmind-api-1 exited (1)"

    store.record_attempt_error(operation["operationId"], error=first)
    store.record_attempt_error(operation["operationId"], error=latest)

    saved = store.load(operation["operationId"])
    summary = saved["errorSummary"]
    assert len(summary) < 2000  # The Operations projection bounds summaries here.
    assert "attempt 1: container moonmind-api-1 is unhealthy" in summary
    assert "latest attempt 2: container moonmind-api-1 exited (1)" in summary
    assert "Waiting" not in summary
    assert [attempt["error"] for attempt in saved["attempts"]] == [first, latest]


def test_old_records_expose_compact_summaries_without_rewriting_diagnostics(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    operation = store.begin(stack="moonmind", desired_image="image", source_revision="")
    operation["status"] = "failed"
    operation["attempts"] = [
        {"attempt": 1, "error": "progress\ncontainer api is unhealthy"}
    ]
    operation["errorSummary"] = "attempt 1: progress\n" * 300
    path = tmp_path / "operations" / f"{operation['operationId']}.json"
    path.write_text(json.dumps(operation))

    for observed in (store.load(operation["operationId"]), store.list_terminal()[0]):
        assert observed["errorSummary"] == "attempt 1: container api is unhealthy"
        assert observed["attempts"] == operation["attempts"]
    assert json.loads(path.read_text())["errorSummary"] == operation["errorSummary"]


def test_error_summary_bounds_a_long_cause_without_losing_its_ending(controller_path):
    record = _record_module(controller_path)
    cause = "progress " * 600 + "container api is unhealthy"
    summary = record.error_summary(
        [
            {"attempt": 1, "error": cause},
            {"attempt": 3, "error": cause},
        ]
    )
    assert len(summary) < 2000
    assert summary.count("container api is unhealthy") == 2


def test_error_summary_preserves_context_that_already_leads_with_the_cause(
    controller_path,
):
    record = _record_module(controller_path)
    cause = "RuntimeError: Refusing Omnigent select: required capability unavailable"
    lead = f"Omnigent selection failed: omnigent-select failed (exit 1) {cause}"

    summary = record.error_summary(
        [
            {"attempt": 1, "error": f"{lead}\npull progress\nTraceback:\n{cause}"},
        ]
    )

    assert summary == f"attempt 1: {lead}"


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


def test_begin_with_caller_operation_id_observes_its_recorded_success(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
        operation_id="ui-1",
    )
    store.confirm_installed("ui-1", image="ghcr.io/org/app@sha256:abc")
    # A retried submission after a lost terminal response observes the
    # recorded success instead of repeating the Compose mutation.
    again = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
        operation_id="ui-1",
    )
    assert again["operationId"] == op["operationId"]
    assert again["status"] == "succeeded"
    assert again["installed"]["image"] == "ghcr.io/org/app@sha256:abc"
    assert len(list((tmp_path / "operations").glob("*.json"))) == 1


def test_new_request_for_an_installed_tag_is_new_work(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    first = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app:latest",
        source_revision="",
        operation_id="ui-1",
    )
    store.confirm_installed("ui-1", image="ghcr.io/org/app:latest")
    # `latest` names whatever the registry publishes next, so a new update
    # request must pull and apply again, never answer with the old success.
    again = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app:latest",
        source_revision="",
        operation_id="ui-2",
    )
    assert again["operationId"] == "ui-2"
    assert again["status"] == "pending"
    assert store.load(first["operationId"])["status"] == "succeeded"


def test_rollback_to_an_earlier_image_is_new_work(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    first = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    store.confirm_installed(first["operationId"], image="ghcr.io/org/app@sha256:abc")
    second = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:def",
        source_revision="abc124",
    )
    store.confirm_installed(second["operationId"], image="ghcr.io/org/app@sha256:def")
    again = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    assert again["operationId"] not in {first["operationId"], second["operationId"]}
    assert again["status"] == "pending"


def test_begin_still_forks_a_new_operation_for_a_new_image(
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
    other = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:def",
        source_revision="abc124",
    )
    assert other["operationId"] != op["operationId"]
    assert other["status"] == "pending"


def test_supersede_closes_stale_open_without_applying(controller_path, tmp_path):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    closed = store.supersede(op["operationId"], reason="stale test target")
    assert closed["status"] == "superseded"
    assert closed["supersededReason"] == "stale test target"
    assert store.list_open(stack="moonmind") == []
    assert store.load(op["operationId"])["attempts"] == []


def test_supersede_never_clears_a_confirmed_installation(
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
    kept = store.supersede(op["operationId"], reason="must not apply")
    assert kept["status"] == "succeeded"
    assert kept["installed"]["image"] == "ghcr.io/org/app@sha256:abc"


def test_unsafe_operation_ids_never_reach_the_filesystem(
    controller_path, tmp_path
):
    import pytest

    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    for unsafe in ("", "../evil", "a/b", ".", "..", "x" * 200, "op id", "op;id"):
        with pytest.raises(ValueError):
            store.load(unsafe)
    # Generated UUID ids keep working.
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    assert store.load(op["operationId"])["operationId"] == op["operationId"]


def test_begin_with_caller_operation_id_reattaches_after_a_lost_ack(
    controller_path, tmp_path
):
    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
        operation_id="ui-1234",
    )
    assert op["operationId"] == "ui-1234"
    # A terminal failure is still the same operation: resubmitting the same
    # identity observes it instead of launching a second attempt.
    for _ in range(record.MAX_AUTO_ATTEMPTS):
        store.record_attempt_error("ui-1234", error="pull failed")
    again = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
        operation_id="ui-1234",
    )
    assert again["operationId"] == "ui-1234"
    assert again["status"] == "failed"
    assert len(list((tmp_path / "operations").glob("*.json"))) == 1


def test_begin_refuses_an_unsafe_caller_operation_id(controller_path, tmp_path):
    import pytest

    record = _record_module(controller_path)
    store = record.OperationStore(tmp_path)
    with pytest.raises(ValueError):
        store.begin(
            stack="moonmind",
            desired_image="img",
            source_revision="",
            operation_id="../escape",
        )
