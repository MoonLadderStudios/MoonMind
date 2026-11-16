"""Offline readiness uses the selected tools and verified installed data."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.security import scanner_feeds as feeds
from moonmind.security import scanner_readiness as readiness

REAL_TOOL_CHECK = readiness._tool_check

NOW = datetime(2026, 11, 9, tzinfo=UTC)


@pytest.fixture(autouse=True)
def installed_tool(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "_tool_check",
        lambda scanner: {
            "component": scanner,
            "status": "ready",
            "code": "scanner_available",
        },
    )


def check(tmp_path, **scan):
    return readiness.inspect_selection(
        {"repositoryScan": {"scanner": "trivy", "outputDirectory": "report", **scan}},
        database=tmp_path,
        rules_root=tmp_path / "rules",
        now=NOW,
    )


def publish(root, age=0):
    (root / "snapshots").mkdir()
    stage = root / "staging" / "fixture"
    db = stage / "db"
    db.mkdir(parents=True)
    (db / "trivy.db").write_bytes(b"fixture database")
    (db / "metadata.json").write_text(
        json.dumps(
            {
                "Version": 2,
                "UpdatedAt": (NOW - timedelta(hours=age)).isoformat(),
            }
        )
    )
    return feeds.publish_snapshot(
        root,
        stage,
        components=["vulnerability"],
        acquisition="download",
        upstreams=feeds.UPSTREAMS,
        now=NOW,
    )[0]


def codes(report):
    return {row["code"] for row in report["checks"]}


def test_missing_required_feed_blocks(tmp_path):
    assert check(tmp_path)["status"] == "blocked"
    assert "feed_snapshot_unavailable" in codes(check(tmp_path))


def test_optional_embedded_checks_need_no_feed(tmp_path):
    report = check(tmp_path, trivyScanners=["misconfig"], requireFreshFeeds=True)
    assert report["status"] == "ready"
    assert "embedded_checks" in codes(report)
    assert "feed_freshness_verified" not in codes(report)


def test_current_snapshot_verified_and_java_left_unverified(tmp_path):
    snapshot = publish(tmp_path)
    report = check(tmp_path)
    assert report["status"] == "ready"
    assert "java_requirement_source_dependent" in codes(report)
    assert {
        "component": "feeds",
        "status": "ready",
        "code": "feed_freshness_verified",
    } in report["checks"]
    (tmp_path / "snapshots" / snapshot.snapshot_id / "db" / "trivy.db").write_bytes(
        b"tampered"
    )
    assert "feed_snapshot_corrupt" in codes(check(tmp_path))


def test_staleness_blocks_only_when_requested(tmp_path):
    publish(tmp_path, age=100)
    assert check(tmp_path)["status"] == "ready"
    assert "feed_snapshot_stale" in codes(check(tmp_path))
    assert "feed_freshness_verified" not in codes(check(tmp_path))
    assert check(tmp_path, requireFreshFeeds=True)["status"] == "blocked"


def test_opengrep_default_rules_required_but_gitleaks_optional(tmp_path):
    assert check(tmp_path, scanner="opengrep")["status"] == "blocked"
    assert check(tmp_path, scanner="gitleaks")["status"] == "ready"


def test_sbom_generation_needs_no_feed(tmp_path):
    report = readiness.inspect_selection(
        {"imageAssessment": {"operation": "sbom-generate", "target": "source"}},
        database=tmp_path,
    )
    assert report["status"] == "ready"


def test_invalid_selection_never_echoes_input(capsys):
    assert readiness.main(['{"repositoryScan":{"scanner":"secret-value"}}']) == 2
    result = capsys.readouterr()
    assert "secret-value" not in result.out + result.err
    assert "invalid_selection" in result.out


def test_extra_selection_rejected():
    with pytest.raises(ValueError):
        readiness.inspect_selection(
            {
                "repositoryScan": {"scanner": "gitleaks", "outputDirectory": "report"},
                "command": ["anything"],
            }
        )


def test_image_matching_checks_selected_digest(tmp_path):
    snapshot = publish(tmp_path)
    selection = {
        "imageAssessment": {
            "operation": "sbom-reassess",
            "target": "sbom",
            "inputArtifactRef": "art_input",
            "databaseCacheRef": "scanner-db",
            "databaseDigest": snapshot.snapshot_id,
        }
    }
    assert (
        readiness.inspect_selection(selection, database=tmp_path, now=NOW)["status"]
        == "ready"
    )
    selection["imageAssessment"]["databaseDigest"] = "f" * 64
    assert (
        readiness.inspect_selection(selection, database=tmp_path, now=NOW)["status"]
        == "blocked"
    )


def test_selected_rule_bytes_checked(tmp_path):
    root = tmp_path / "rules"
    root.mkdir()
    (root / "rule.yml").write_text("changed")
    report = check(
        tmp_path,
        scanner="opengrep",
        rules=[
            {
                "id": "selected",
                "path": "/opt/moonmind-scanner-rules/rule.yml",
                "sha256": "0" * 64,
                "license": "MIT",
            }
        ],
    )
    assert "scanner_rule_integrity_failed" in codes(report)


@pytest.mark.parametrize(
    "scanner,flag",
    [("gitleaks", "version"), ("opengrep", "--version"), ("trivy", "--version")],
)
def test_version_probe_is_static_and_does_not_inherit_credentials(
    monkeypatch, scanner, flag
):
    monkeypatch.setenv("SECRET_TOKEN", "must-not-leak")
    monkeypatch.setattr(
        readiness.shutil, "which", lambda name, path: "/usr/bin/" + name
    )

    def run(self, argv, *, cwd, env, capture):
        assert argv == ["/usr/bin/" + scanner, flag]
        assert "SECRET_TOKEN" not in env
        assert env["HOME"] == str(cwd)
        capture.write_bytes(b"1.2.3")
        return 0, "succeeded"

    monkeypatch.setattr(readiness.BoundedProcesses, "run", run)
    assert REAL_TOOL_CHECK(scanner)["status"] == "ready"


def test_version_failure_does_not_expose_output(monkeypatch):
    monkeypatch.setattr(
        readiness.shutil, "which", lambda name, path: "/usr/bin/" + name
    )

    def run(self, argv, *, cwd, env, capture):
        capture.write_bytes(b"credential-data")
        return 1, "succeeded"

    monkeypatch.setattr(readiness.BoundedProcesses, "run", run)
    assert REAL_TOOL_CHECK("trivy") == {
        "component": "trivy",
        "status": "blocked",
        "code": "scanner_version_unavailable",
    }


def test_report_is_bound_to_raw_selection(tmp_path):
    selection = {"repositoryScan": {"scanner": "gitleaks", "outputDirectory": "report"}}
    report = readiness.inspect_selection(selection, database=tmp_path)
    expected = hashlib.sha256(
        json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert report["selectionDigest"] == expected
    selection["repositoryScan"]["requireFreshFeeds"] = False
    assert (
        readiness.inspect_selection(selection, database=tmp_path)["selectionDigest"]
        != expected
    )


def test_blocked_report_is_bound_to_valid_selection(tmp_path):
    selection = {"repositoryScan": {"scanner": "trivy", "outputDirectory": "report"}}
    report = readiness.inspect_selection(selection, database=tmp_path)
    assert report["status"] == "blocked"
    assert (
        report["selectionDigest"]
        == hashlib.sha256(
            json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
