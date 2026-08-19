"""CLI tests for the Omnigent live-verification health and failure tools.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tools import omnigent_live_verification_health as health_cli
from tools import stage_omnigent_failure_evidence as failure_cli
from tools.assemble_omnigent_live_status import build_status_document
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

    assert document["runner"] == {"status": "online", "busy": True}
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
