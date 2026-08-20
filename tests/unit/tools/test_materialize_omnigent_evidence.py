"""Tests for the durable Omnigent release-support evidence materializer.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json

from tools import materialize_omnigent_evidence as materialize

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"


def test_evidence_sources_write_the_compose_default_filenames() -> None:
    """Destinations must match the paths Compose defaults the API to.

    The deployed catalog reads these filenames from the read-only evidence
    mount with no per-file configuration, so a rename here silently leaves the
    catalog reporting its fail-closed support reason forever.
    """
    destinations = {source.destination for source in materialize.EVIDENCE_SOURCES}

    assert destinations == {
        "acceptance-manifest.json",
        "exact-artifact-projection.json",
        "live-health-projection.json",
    }


def test_select_runs_for_commit_ignores_other_commits_and_failures() -> None:
    runs = materialize.select_runs_for_commit(
        [
            {"id": 1, "head_sha": OTHER_COMMIT, "status": "completed", "conclusion": "success"},
            {"id": 2, "head_sha": COMMIT, "status": "completed", "conclusion": "failure"},
            {"id": 3, "head_sha": COMMIT, "status": "in_progress", "conclusion": None},
            {
                "id": 4,
                "head_sha": COMMIT,
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-08-18T00:00:00Z",
            },
            {
                "id": 5,
                "head_sha": COMMIT,
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-08-19T00:00:00Z",
            },
        ],
        commit=COMMIT,
    )

    assert [run["id"] for run in runs] == [5, 4]


def test_select_artifact_skips_expired_and_prefers_newest() -> None:
    selected = materialize.select_artifact(
        [
            {"id": 1, "name": "other", "created_at": "2026-08-20T00:00:00Z"},
            {
                "id": 2,
                "name": "omnigent-exact-artifact-9-1",
                "created_at": "2026-08-18T00:00:00Z",
            },
            {
                "id": 3,
                "name": "omnigent-exact-artifact-9-2",
                "created_at": "2026-08-19T00:00:00Z",
            },
            {
                "id": 4,
                "name": "omnigent-exact-artifact-9-3",
                "created_at": "2026-08-20T00:00:00Z",
                "expired": True,
            },
        ],
        prefix="omnigent-exact-artifact-",
    )

    assert selected is not None
    assert selected["name"] == "omnigent-exact-artifact-9-2"


def test_materialize_source_writes_evidence_for_the_deployed_commit(
    monkeypatch, tmp_path
) -> None:
    source = materialize.EVIDENCE_SOURCES[1]
    document = {"sourceCommit": COMMIT, "verdict": "passed"}

    monkeypatch.setattr(
        materialize,
        "_gh_api",
        lambda repository, path: (
            {
                "workflow_runs": [
                    {
                        "id": 42,
                        "head_sha": COMMIT,
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
            if "workflows/" in path
            else {
                "artifacts": [
                    {
                        "id": 7,
                        "name": "omnigent-exact-artifact-42-1",
                        "created_at": "2026-08-19T00:00:00Z",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        materialize, "_download", lambda *_args, **_kwargs: document
    )

    result = materialize.materialize_source(
        source, repository="owner/repo", commit=COMMIT, destination_dir=tmp_path
    )

    assert result["status"] == "materialized"
    written = json.loads(
        (tmp_path / "exact-artifact-projection.json").read_text(encoding="utf-8")
    )
    assert written == document


def test_materialize_source_rejects_evidence_for_another_commit(
    monkeypatch, tmp_path
) -> None:
    """Evidence naming another commit proves nothing about the deployed image."""
    source = materialize.EVIDENCE_SOURCES[1]

    monkeypatch.setattr(
        materialize,
        "_gh_api",
        lambda repository, path: (
            {
                "workflow_runs": [
                    {
                        "id": 42,
                        "head_sha": COMMIT,
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
            if "workflows/" in path
            else {
                "artifacts": [
                    {"id": 7, "name": "omnigent-exact-artifact-42-1", "created_at": "x"}
                ]
            }
        ),
    )
    monkeypatch.setattr(
        materialize,
        "_download",
        lambda *_args, **_kwargs: {"sourceCommit": OTHER_COMMIT, "verdict": "passed"},
    )

    result = materialize.materialize_source(
        source, repository="owner/repo", commit=COMMIT, destination_dir=tmp_path
    )

    assert result["status"] == "missing"
    assert not (tmp_path / "exact-artifact-projection.json").exists()
