"""Unit tests for queue artifact filesystem storage safety."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage


def test_write_artifact_scoped_to_job_root(tmp_path: Path) -> None:
    """Artifacts should be written only within the configured job directory."""

    storage = AgentQueueArtifactStorage(tmp_path)
    job_id = uuid4()

    destination, storage_path = storage.write_artifact(
        job_id=job_id,
        artifact_name="logs/output.log",
        data=b"artifact-bytes",
    )

    assert destination.exists()
    assert destination.read_bytes() == b"artifact-bytes"
    assert storage_path == f"{job_id}/logs/output.log"
    assert destination.is_relative_to((tmp_path / str(job_id)).resolve())


@pytest.mark.parametrize(
    "name",
    [
        "../escape.log",
        "/absolute.log",
        "nested/../../escape.log",
    ],
)
def test_resolve_artifact_path_rejects_traversal(tmp_path: Path, name: str) -> None:
    """Traversal and absolute artifact names should be rejected."""

    storage = AgentQueueArtifactStorage(tmp_path)
    with pytest.raises(ValueError):
        storage.resolve_artifact_path(uuid4(), name)


def test_resolve_storage_path_rejects_traversal(tmp_path: Path) -> None:
    """Stored paths with traversal tokens should be rejected."""

    storage = AgentQueueArtifactStorage(tmp_path)
    with pytest.raises(ValueError):
        storage.resolve_storage_path("../other-job/file.log")


def test_step_state_path_scoped_to_job(tmp_path: Path) -> None:
    """Resolved step state files must remain under the job directory."""

    storage = AgentQueueArtifactStorage(tmp_path)
    job_id = uuid4()

    path = storage.get_step_state_path(job_id, step_index=3)

    expected = (tmp_path / str(job_id) / "state" / "steps" / "step-0003.json").resolve()
    assert path == expected
    assert path.is_relative_to((tmp_path / str(job_id)).resolve())


def test_self_heal_attempt_path_scoped_to_job(tmp_path: Path) -> None:
    """Resolved self-heal attempt files must remain under the job directory."""

    storage = AgentQueueArtifactStorage(tmp_path)
    job_id = uuid4()

    path = storage.get_self_heal_attempt_path(job_id, step_index=1, attempt=2)

    expected = (
        tmp_path
        / str(job_id)
        / "state"
        / "self_heal"
        / "attempt-0001-0002.json"
    ).resolve()
    assert path == expected
    assert path.is_relative_to((tmp_path / str(job_id)).resolve())
