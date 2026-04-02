import pytest
from datetime import UTC, datetime
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _make_record(run_id: str = "test-run-1", status: str = "running") -> ManagedRunRecord:
    return ManagedRunRecord(
        run_id=run_id,
        agent_id="agent-1",
        runtime_id="codex-cli",
        status=status,
        started_at=datetime.now(tz=UTC),
    )


def test_save_and_load_round_trip(tmp_path):
    store = ManagedRunStore(tmp_path)
    record = _make_record().model_copy(update={"workflow_id": "mm:wf-1"})
    store.save(record)

    loaded = store.load("test-run-1")
    assert loaded is not None
    assert loaded.run_id == "test-run-1"
    assert loaded.agent_id == "agent-1"
    assert loaded.runtime_id == "codex-cli"
    assert loaded.status == "running"
    assert loaded.workflow_id == "mm:wf-1"


def test_load_missing_returns_none(tmp_path):
    store = ManagedRunStore(tmp_path)
    assert store.load("nonexistent") is None


def test_update_status(tmp_path):
    store = ManagedRunStore(tmp_path)
    store.save(_make_record())

    updated = store.update_status("test-run-1", "completed", exit_code=0)
    assert updated.status == "completed"
    assert updated.exit_code == 0

    reloaded = store.load("test-run-1")
    assert reloaded.status == "completed"
    assert reloaded.exit_code == 0


def test_update_status_missing_raises(tmp_path):
    store = ManagedRunStore(tmp_path)
    with pytest.raises(ValueError, match="run record not found"):
        store.update_status("nonexistent", "failed")


def test_list_active(tmp_path):
    store = ManagedRunStore(tmp_path)
    store.save(_make_record("run-1", "running"))
    store.save(_make_record("run-2", "completed"))
    store.save(_make_record("run-3", "launching"))

    active = store.list_active()
    active_ids = {r.run_id for r in active}
    assert active_ids == {"run-1", "run-3"}


def test_find_latest_for_workflow_prefers_newest_active_run(tmp_path):
    store = ManagedRunStore(tmp_path)
    started_at = datetime.now(tz=UTC)

    store.save(
        _make_record("run-old-terminal", "completed").model_copy(
            update={
                "workflow_id": "mm:wf-1",
                "started_at": started_at,
                "finished_at": started_at,
            }
        )
    )
    store.save(
        _make_record("run-new-active", "running").model_copy(
            update={
                "workflow_id": "mm:wf-1",
                "started_at": started_at.replace(microsecond=started_at.microsecond + 1),
            }
        )
    )

    found = store.find_latest_for_workflow("mm:wf-1")

    assert found is not None
    assert found.run_id == "run-new-active"


def test_find_latest_for_workflow_returns_latest_terminal_when_no_active_exists(tmp_path):
    store = ManagedRunStore(tmp_path)
    started_at = datetime.now(tz=UTC)

    store.save(
        _make_record("run-first", "completed").model_copy(
            update={
                "workflow_id": "mm:wf-1",
                "started_at": started_at,
                "finished_at": started_at,
            }
        )
    )
    store.save(
        _make_record("run-second", "failed").model_copy(
            update={
                "workflow_id": "mm:wf-1",
                "started_at": started_at.replace(microsecond=started_at.microsecond + 1),
                "finished_at": started_at.replace(microsecond=started_at.microsecond + 2),
            }
        )
    )

    found = store.find_latest_for_workflow("mm:wf-1")

    assert found is not None
    assert found.run_id == "run-second"


def test_path_traversal_rejected(tmp_path):
    store = ManagedRunStore(tmp_path)

    with pytest.raises(ValueError, match="traversal"):
        store.load("../etc/passwd")

    with pytest.raises(ValueError, match="traversal"):
        store.save(_make_record(run_id="../escape"))

    with pytest.raises(ValueError, match="empty"):
        store.load("")
