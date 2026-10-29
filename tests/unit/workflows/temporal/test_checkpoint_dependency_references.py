"""Artifact closures distinguish retained workflow history from blob inputs."""

import json

import pytest

from moonmind.schemas.saved_work_models import saved_work_artifact_dependencies


def _checkpoint():
    return {
        "source": {"workflowId": "workflow-1", "runId": "run-1"},
        "taskInputSnapshotRef": "temporal://workflow-1/run-1/task-input",
        "planRef": "temporal://workflow-1/run-1/plan",
        "workspace": {"archiveRef": "artifact:art_archive", "archiveDigest": "a" * 64},
    }


def test_history_backed_inputs_do_not_become_artifact_dependencies():
    entries = saved_work_artifact_dependencies(
        "step_execution_checkpoint", json.dumps(_checkpoint()).encode()
    )
    assert entries == [
        {
            "artifact_id": "art_archive",
            "sha256": "a" * 64,
            "required": True,
            "role": "checkpoint.workspace.archiveRef",
        }
    ]


def test_artifact_backed_inputs_remain_required():
    checkpoint = _checkpoint()
    checkpoint.update(
        taskInputSnapshotRef="artifact:art_task", planRef="artifact:art_plan"
    )
    entries = saved_work_artifact_dependencies(
        "step_execution_checkpoint", json.dumps(checkpoint).encode()
    )
    assert {entry["artifact_id"] for entry in entries} == {
        "art_task",
        "art_plan",
        "art_archive",
    }
    assert all(entry["required"] for entry in entries)


@pytest.mark.parametrize(
    "field,ref",
    [
        ("taskInputSnapshotRef", "temporal://foreign/run-1/task-input"),
        ("taskInputSnapshotRef", "temporal://workflow-1/old-run/task-input"),
        ("taskInputSnapshotRef", "temporal://workflow-1/run-1/plan"),
        ("planRef", "https://example.invalid/plan"),
        ("planRef", "temporal://workflow-1/run-1/plan/extra"),
    ],
)
def test_noncanonical_input_reference_is_not_silently_dropped(field, ref):
    checkpoint = _checkpoint()
    checkpoint[field] = ref
    with pytest.raises(ValueError, match="durable artifact"):
        saved_work_artifact_dependencies(
            "step_execution_checkpoint", json.dumps(checkpoint).encode()
        )


def test_history_reference_cannot_replace_required_workspace_bytes():
    checkpoint = _checkpoint()
    checkpoint["workspace"]["archiveRef"] = checkpoint["taskInputSnapshotRef"]
    with pytest.raises(ValueError, match="durable artifact"):
        saved_work_artifact_dependencies(
            "step_execution_checkpoint", json.dumps(checkpoint).encode()
        )
