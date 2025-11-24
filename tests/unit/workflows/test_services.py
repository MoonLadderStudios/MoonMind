from datetime import UTC, datetime
from uuid import uuid4

import pytest

from moonmind.workflows.speckit_celery import services


@pytest.mark.parametrize("attempt,expected_suffix", [(1, ""), (2, "-r2"), (3, "-r3")])
def test_derive_branch_name_deterministic(attempt, expected_suffix):
    run_id = uuid4()
    feature_key = "FR-008/idempotent-branch"
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)

    branch = services.derive_branch_name(feature_key, run_id, attempt=attempt, timestamp=timestamp)

    parts = branch.split("/")
    assert parts[0] == "fr-008-idempotent-branch"
    assert parts[1] == "20240101"
    assert parts[2].startswith(run_id.hex[:8])
    assert parts[2].endswith(expected_suffix)


def test_resolve_codex_logs_path_creates_parents(tmp_path):
    run_id = uuid4()
    path = services.resolve_codex_logs_path(run_id, artifacts_root=tmp_path)

    assert path.name == "codex.jsonl"
    assert path.parent.exists()
    assert path.parent == tmp_path / str(run_id)


def test_push_commits_skips_in_test_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    ref = services.push_commits(repo, "feature/test", test_mode=True)

    assert ref == "origin/feature/test"
