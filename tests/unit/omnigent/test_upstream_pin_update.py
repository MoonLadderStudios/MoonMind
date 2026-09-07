"""Unit tests for the omnigent upstream-pin updater contract.

Source issue: MoonLadderStudios/MoonMind#3957.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from moonmind.omnigent.upstream_pin_update import (
    AFFECTED_ASSETS,
    QUALIFICATION_SHARDS,
    UpstreamPinUpdateError,
    blocked_transient_upstream,
    build_freshness_status,
    build_update_evidence,
    is_eligible_release,
    mark_tested,
    normalize_commit,
    select_candidate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

CURRENT = "f04b0354fb5344c1ea8b92795ceb6760a9ad7595"
NEWER = "a" * 40
OLDER = "b" * 40


def _release(tag, commit, **overrides):
    release = {
        "tag_name": tag,
        "name": tag,
        "html_url": f"https://github.com/omnigent-ai/omnigent/releases/tag/{tag}",
        "published_at": "2026-08-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "resolved_commit": commit,
    }
    release.update(overrides)
    return release


def test_normalize_commit_rejects_short_or_non_hex() -> None:
    assert normalize_commit(CURRENT) == CURRENT
    with pytest.raises(UpstreamPinUpdateError):
        normalize_commit("ed9369474")
    with pytest.raises(UpstreamPinUpdateError):
        normalize_commit("not-a-sha")


def test_prereleases_excluded_unless_allowed_and_drafts_never() -> None:
    pre = _release("v0.13.0-rc1", NEWER, prerelease=True)
    draft = _release("v0.13.0", NEWER, draft=True)
    assert not is_eligible_release(pre)
    assert is_eligible_release(pre, allow_prereleases=True)
    assert not is_eligible_release(draft)
    assert not is_eligible_release(draft, allow_prereleases=True)


def test_unresolvable_tag_is_not_eligible() -> None:
    assert not is_eligible_release(_release("v0.13.0", ""))
    assert not is_eligible_release(_release("v0.13.0", "moved"))


def test_up_to_date_is_idempotent() -> None:
    releases = [_release("v0.12.0", CURRENT)]
    first = select_candidate(CURRENT, releases)
    second = select_candidate(CURRENT, releases)
    assert first.status == "up_to_date"
    assert first == second
    assert first.candidate is None


def test_selects_newest_eligible_release() -> None:
    releases = [
        _release("v0.11.0", OLDER, published_at="2026-06-01T00:00:00Z"),
        _release("v0.13.0", NEWER, published_at="2026-09-01T00:00:00Z"),
    ]
    result = select_candidate(CURRENT, releases)
    assert result.status == "candidate_available"
    assert result.candidate is not None
    assert result.candidate["tag"] == "v0.13.0"
    assert result.candidate["commit"] == NEWER
    # Tag is provenance: the candidate carries the immutable commit.
    assert len(result.candidate["commit"]) == 40


def test_no_suitable_release_on_empty_inventory() -> None:
    result = select_candidate(CURRENT, [])
    assert result.status == "no_suitable_release"
    assert result.candidate is None
    assert "main" in result.reason  # must not silently track upstream main


def test_no_suitable_release_when_only_prereleases() -> None:
    releases = [_release("v0.13.0-rc1", NEWER, prerelease=True)]
    result = select_candidate(CURRENT, releases)
    assert result.status == "no_suitable_release"
    assert result.candidate is None


def test_blocked_invalid_tag_when_no_resolvable_commit() -> None:
    releases = [_release("v0.13.0", "", published_at="2026-09-01T00:00:00Z")]
    result = select_candidate(CURRENT, releases)
    assert result.status == "blocked_invalid_tag"
    assert result.candidate is None


def test_blocked_transient_upstream_leaves_pin_intact() -> None:
    result = blocked_transient_upstream("connection reset")
    assert result.status == "blocked_transient_upstream"
    assert result.candidate is None
    assert "intact" in result.reason


def test_evidence_names_commits_provenance_assets_and_pins() -> None:
    result = select_candidate(CURRENT, [_release("v0.13.0", NEWER)])
    evidence = build_update_evidence(result=result, current_commit=CURRENT)
    assert evidence["oldCommit"] == CURRENT
    assert evidence["newCommit"] == NEWER
    assert evidence["releaseProvenance"]["tag"] == "v0.13.0"
    assert evidence["affectedAssets"] == list(AFFECTED_ASSETS)
    assert "generated adapter assets" in evidence["affectedAssets"]
    assert "native network-contract fixtures" in evidence["affectedAssets"]
    inputs = evidence["qualificationInputs"]
    assert inputs["currentCommit"] == CURRENT
    assert inputs["candidateCommit"] == NEWER
    assert inputs["qualificationShards"] == list(QUALIFICATION_SHARDS)
    assert evidence["rollbackIdentity"]["previousQualifiedCommit"] == CURRENT


def test_freshness_status_distinguishes_lifecycle_stages() -> None:
    result = select_candidate(CURRENT, [_release("v0.13.0", NEWER)])
    status = build_freshness_status(
        result=result,
        current_commit=CURRENT,
        last_qualified_tag="v0.12.0",
        checked_at="2026-09-07T00:00:00Z",
    )
    assert status["available"] == {"tag": "v0.13.0", "commit": NEWER}
    assert status["qualified"]["commit"] == CURRENT
    assert status["lastQualifiedVersion"]["commit"] == CURRENT
    assert status["promoted"]["commit"] == CURRENT
    assert status["tested"] == {"commit": "", "hermeticRunRef": ""}
    assert status["checkCadence"] == "weekly"


def test_mark_tested_never_implies_qualified_or_promoted() -> None:
    result = select_candidate(CURRENT, [_release("v0.13.0", NEWER)])
    status = build_freshness_status(result=result, current_commit=CURRENT)
    tested = mark_tested(status, commit=NEWER, hermetic_run_ref="run-123")
    assert tested["tested"] == {"commit": NEWER, "hermeticRunRef": "run-123"}
    assert tested["qualified"]["commit"] == CURRENT
    assert tested["promoted"]["commit"] == CURRENT


def test_blocked_outcome_surfaces_blocked_reason() -> None:
    result = select_candidate(CURRENT, [_release("v0.13.0", "")])
    status = build_freshness_status(result=result, current_commit=CURRENT)
    assert status["outcome"] == "blocked_invalid_tag"
    assert status["blockedReason"] == result.reason
    assert status["available"] == {"tag": "", "commit": ""}


def test_qualification_shards_cover_all_owning_suites() -> None:
    names = "\n".join(QUALIFICATION_SHARDS)
    for suite in (
        "adapter",
        "registration",
        "materializers",
        "timeline",
        "supervisor",
        "readiness",
        "native_ui",
        "facade",
        "evidence",
        "cleanup",
        "janitor",
    ):
        assert suite in names
    for shard in QUALIFICATION_SHARDS:
        assert (REPO_ROOT / shard).exists(), f"missing owning shard: {shard}"


def test_cli_writes_candidate_evidence_and_status(tmp_path: Path) -> None:
    releases_file = tmp_path / "releases.json"
    releases_file.write_text(
        json.dumps([_release("v0.13.0", NEWER)]), encoding="utf-8"
    )
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/check_omnigent_upstream_pin_update.py"),
            "--current-commit",
            CURRENT,
            "--releases-json",
            str(releases_file),
            "--output-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    candidate = json.loads((out_dir / "candidate.json").read_text(encoding="utf-8"))
    evidence = json.loads((out_dir / "evidence.json").read_text(encoding="utf-8"))
    status = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
    assert candidate["status"] == "candidate_available"
    assert candidate["candidate"]["commit"] == NEWER
    assert evidence["oldCommit"] == CURRENT
    assert evidence["newCommit"] == NEWER
    assert status["outcome"] == "candidate_available"
    assert status["available"]["commit"] == NEWER


def test_cli_reports_no_release_state_without_tracking_main(tmp_path: Path) -> None:
    releases_file = tmp_path / "releases.json"
    releases_file.write_text("[]", encoding="utf-8")
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/check_omnigent_upstream_pin_update.py"),
            "--current-commit",
            CURRENT,
            "--releases-json",
            str(releases_file),
            "--output-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    candidate = json.loads((out_dir / "candidate.json").read_text(encoding="utf-8"))
    assert candidate["status"] == "no_suitable_release"


def test_cli_rejects_tool_misuse() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/check_omnigent_upstream_pin_update.py"),
            "--current-commit",
            "short-sha",
            "--fetch-error",
            "boom",
            "--output-dir",
            "/tmp/omnigent-pin-misuse-probe",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 2
