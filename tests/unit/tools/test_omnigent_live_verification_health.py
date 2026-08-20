"""CLI tests for the Omnigent live-verification health and failure tools.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tools import omnigent_live_verification_health as health_cli
from tools import stage_omnigent_failure_evidence as failure_cli
from tools import assemble_omnigent_live_status as assemble
from tools.assemble_omnigent_live_status import (
    aggregate_provider_runner_fleet,
    build_status_document,
    read_manifest_from_download,
    select_manifest_artifact,
)
from moonmind.omnigent.live_verification_health import REQUIRED_LIVE_MATRIX_MODES

COMMIT = "0123456789abcdef0123456789abcdef01234567"
SERVER_DIGEST = "sha256:" + "a" * 64
HOST_DIGEST = "sha256:" + "b" * 64


def _healthy_document() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "deployedCommit": COMMIT,
        "requiredDigests": {"server": SERVER_DIGEST, "host": HOST_DIGEST},
        "runner": {"status": "online", "busy": False},
        "queue": {"oldestQueuedAgeSeconds": 30},
        "latestRun": {
            "status": "success",
            "sourceCommit": COMMIT,
            "startedAt": (now - timedelta(minutes=20)).isoformat(),
            "completedAt": (now - timedelta(minutes=2)).isoformat(),
            "modes": list(REQUIRED_LIVE_MATRIX_MODES),
        },
        "manifest": {
            "generatedAt": (now - timedelta(hours=1)).isoformat(),
            "expiresAt": (now + timedelta(days=29)).isoformat(),
            "sourceCommit": COMMIT,
            "images": {"serverDigest": SERVER_DIGEST, "hostDigest": HOST_DIGEST},
        },
    }


def test_health_cli_ready_exits_zero(tmp_path, capsys) -> None:
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_healthy_document()), encoding="utf-8")
    output = tmp_path / "projection.json"

    rc = health_cli.main(["--status", str(status), "--output", str(output)])

    assert rc == 0
    projection = json.loads(output.read_text(encoding="utf-8"))
    assert projection["rolloutReady"] is True


def test_health_cli_creates_missing_output_dir(tmp_path, capsys) -> None:
    # CI writes the projection to a nested path that does not yet exist on a
    # fresh checkout; the upload step must still find the file (#3710).
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_healthy_document()), encoding="utf-8")
    output = tmp_path / "artifacts" / "omnigent-live-health" / "projection.json"

    rc = health_cli.main(["--status", str(status), "--output", str(output)])

    assert rc == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["rolloutReady"] is True


def test_health_cli_offline_runner_exits_one(tmp_path, capsys) -> None:
    document = _healthy_document()
    document["runner"] = {"status": "offline", "busy": False}
    status = tmp_path / "status.json"
    status.write_text(json.dumps(document), encoding="utf-8")

    rc = health_cli.main(["--status", str(status)])

    assert rc == 1
    assert "not ready" in capsys.readouterr().out


def test_health_cli_malformed_status_exits_two(tmp_path, capsys) -> None:
    status = tmp_path / "status.json"
    status.write_text("{not json", encoding="utf-8")

    rc = health_cli.main(["--status", str(status)])

    assert rc == 2


def test_stage_failure_evidence_writes_redacted_diagnostics(tmp_path) -> None:
    log_file = tmp_path / "case.log"
    log_file.write_text(
        "starting\nAuthorization: Bearer github_pat_11ABC\nfailed\n",
        encoding="utf-8",
    )
    upload_dir = tmp_path / "upload"

    rc = failure_cli.main(
        [
            "--mode",
            "product",
            "--outcome",
            "failure",
            "--setup-stage",
            "run-credentialed-live-matrix-case",
            "--failure-summary",
            "case failed with token=ghp_secretsecretsecret",
            "--runner-status",
            "self-hosted omnigent-provider-verification",
            "--duration-seconds",
            "12.5",
            "--log-file",
            str(log_file),
            "--upload-dir",
            str(upload_dir),
        ]
    )

    assert rc == 0
    diagnostics = json.loads(
        (upload_dir / "failure-diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["status"] == "failed"
    assert diagnostics["durationSeconds"] == 12.5
    assert "ghp_secret" not in diagnostics["failureSummary"]
    assert "github_pat_11ABC" not in "\n".join(diagnostics["logTail"])
    assert diagnostics["secretScan"]["status"] == "passed"


# --- Status assembly transform ------------------------------------------------


def test_build_status_document_projects_runner_and_matrix() -> None:
    now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    document = build_status_document(
        runners=[
            {"name": "other", "status": "online", "busy": False, "labels": ["x"]},
            {
                "name": "omni-1",
                "status": "online",
                "busy": True,
                "labels": [{"name": "self-hosted"}, {"name": "omnigent-provider-verification"}],
            },
        ],
        workflow_runs=[
            {
                "status": "completed",
                "conclusion": "success",
                "head_sha": COMMIT,
                "run_started_at": (now - timedelta(minutes=30)).isoformat(),
                "updated_at": (now - timedelta(minutes=1)).isoformat(),
            },
            {
                "status": "queued",
                "created_at": (now - timedelta(hours=2)).isoformat(),
            },
        ],
        latest_run_jobs=[
            {"name": f"Live {mode} conformance", "conclusion": "success"}
            for mode in REQUIRED_LIVE_MATRIX_MODES
        ],
        manifest=None,
        deployed_commit=COMMIT,
        required_digests={"server": SERVER_DIGEST, "host": HOST_DIGEST},
        now=now,
    )

    assert document["runner"]["status"] == "online"
    assert document["runner"]["busy"] is True
    assert document["runner"]["matchingCount"] == 1
    assert document["queue"]["oldestQueuedAgeSeconds"] == 2 * 60 * 60
    assert document["latestRun"]["status"] == "success"
    assert document["latestRun"]["sourceCommit"] == COMMIT
    assert set(document["latestRun"]["modes"]) == set(REQUIRED_LIVE_MATRIX_MODES)


def test_build_status_document_reports_offline_when_runner_absent() -> None:
    document = build_status_document(
        runners=[{"name": "unrelated", "status": "online", "labels": ["x"]}],
        workflow_runs=[],
        manifest=None,
        deployed_commit=COMMIT,
        required_digests={"server": SERVER_DIGEST, "host": HOST_DIGEST},
        now=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert document["runner"]["status"] == "offline"
    assert document["latestRun"] is None
    assert document["queue"]["oldestQueuedAgeSeconds"] is None


# --- Provider runner fleet aggregation ---------------------------------------


def _labeled(name: str, *, status: str, busy: bool) -> dict:
    return {
        "name": name,
        "status": status,
        "busy": busy,
        "labels": [{"name": "self-hosted"}, {"name": "omnigent-provider-verification"}],
    }


def test_fleet_is_online_when_any_labeled_runner_is_online() -> None:
    """An arbitrarily-ordered offline runner must not hide an online one."""
    fleet = aggregate_provider_runner_fleet(
        [
            _labeled("omni-offline", status="offline", busy=False),
            _labeled("omni-online", status="online", busy=False),
        ]
    )

    assert fleet["status"] == "online"
    assert fleet["busy"] is False
    assert fleet == {
        "status": "online",
        "busy": False,
        "matchingCount": 2,
        "onlineCount": 1,
        "idleCount": 1,
    }


def test_fleet_reports_idle_capacity_across_the_whole_fleet() -> None:
    fleet = aggregate_provider_runner_fleet(
        [
            _labeled("omni-busy", status="online", busy=True),
            _labeled("omni-idle", status="online", busy=False),
        ]
    )

    assert fleet["status"] == "online"
    # One eligible runner is idle, so the tier has capacity.
    assert fleet["busy"] is False
    assert fleet["idleCount"] == 1


def test_fleet_is_busy_only_when_no_online_runner_is_idle() -> None:
    fleet = aggregate_provider_runner_fleet(
        [
            _labeled("omni-a", status="online", busy=True),
            _labeled("omni-b", status="online", busy=True),
        ]
    )

    assert fleet["status"] == "online"
    assert fleet["busy"] is True
    assert fleet["idleCount"] == 0


def test_fleet_ignores_unlabeled_runners() -> None:
    fleet = aggregate_provider_runner_fleet(
        [{"name": "generic", "status": "online", "busy": False, "labels": ["x"]}]
    )

    assert fleet == {
        "status": "offline",
        "busy": False,
        "matchingCount": 0,
        "onlineCount": 0,
        "idleCount": 0,
    }


# --- Published acceptance-manifest retrieval ---------------------------------


def test_select_manifest_artifact_prefers_newest_unexpired() -> None:
    selected = select_manifest_artifact(
        [
            {"id": 1, "name": "unrelated", "created_at": "2026-08-19T00:00:00Z"},
            {
                "id": 2,
                "name": "omnigent-live-published-matrix-10-1",
                "created_at": "2026-08-18T00:00:00Z",
            },
            {
                "id": 3,
                "name": "omnigent-live-published-matrix-10-2",
                "created_at": "2026-08-19T00:00:00Z",
            },
            {
                "id": 4,
                "name": "omnigent-live-published-matrix-11-1",
                "created_at": "2026-08-20T00:00:00Z",
                "expired": True,
            },
        ]
    )

    assert selected is not None
    assert selected["name"] == "omnigent-live-published-matrix-10-2"


def test_select_manifest_artifact_returns_none_without_candidates() -> None:
    assert select_manifest_artifact([{"id": 1, "name": "other"}]) is None
    assert (
        select_manifest_artifact(
            [{"id": 1, "name": "omnigent-live-published-matrix-1-1", "expired": True}]
        )
        is None
    )


def test_read_manifest_from_download_reads_nested_manifest(tmp_path) -> None:
    nested = tmp_path / "published"
    nested.mkdir()
    (nested / "published-matrix.json").write_text(
        json.dumps({"sourceCommit": COMMIT}), encoding="utf-8"
    )

    manifest = read_manifest_from_download(tmp_path)

    assert manifest == {"sourceCommit": COMMIT}


def test_read_manifest_from_download_tolerates_missing_or_malformed(tmp_path) -> None:
    assert read_manifest_from_download(tmp_path) is None
    (tmp_path / "published-matrix.json").write_text("{not json", encoding="utf-8")
    assert read_manifest_from_download(tmp_path) is None


def test_load_published_manifest_uses_newest_passing_run(monkeypatch) -> None:
    """The status document must carry the manifest the live matrix published.

    Passing ``manifest=None`` would make ``evidence_fresh`` and
    ``evidence_digests_match`` fail on every run, so the protected tier could
    never become ready after a fully successful matrix.
    """
    calls: list[tuple] = []

    def fake_gh_api(repository: str, path: str):
        calls.append(("api", repository, path))
        assert path.startswith("actions/runs/77/artifacts")
        return {
            "artifacts": [
                {
                    "id": 9,
                    "name": "omnigent-live-published-matrix-77-1",
                    "created_at": "2026-08-19T00:00:00Z",
                }
            ]
        }

    def fake_download(repository: str, run_id, artifact_name: str):
        calls.append(("download", run_id, artifact_name))
        return {"sourceCommit": COMMIT, "expiresAt": "2026-09-19T00:00:00Z"}

    monkeypatch.setattr(assemble, "_gh_api", fake_gh_api)
    monkeypatch.setattr(assemble, "_download_published_manifest", fake_download)

    manifest = assemble.load_published_manifest(
        "owner/repo",
        [
            {"id": 88, "status": "completed", "conclusion": "failure"},
            {"id": 77, "status": "completed", "conclusion": "success"},
        ],
    )

    assert manifest == {"sourceCommit": COMMIT, "expiresAt": "2026-09-19T00:00:00Z"}
    assert ("download", 77, "omnigent-live-published-matrix-77-1") in calls


def test_load_published_manifest_returns_none_when_absent(monkeypatch) -> None:
    monkeypatch.setattr(assemble, "_gh_api", lambda *_: {"artifacts": []})
    assert assemble.load_published_manifest("owner/repo", []) is None
    assert (
        assemble.load_published_manifest(
            "owner/repo", [{"id": 5, "status": "completed", "conclusion": "success"}]
        )
        is None
    )
