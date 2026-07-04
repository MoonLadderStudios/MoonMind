from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.publish_auto_evidence import (
    parse_auto_publish_evidence,
)


HELPER_PATH = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "_shared"
    / "publish_evidence.py"
)


@pytest.fixture()
def helper_module() -> Any:
    spec = importlib.util.spec_from_file_location("publish_evidence_helper", HELPER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _completed(stdout: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_pushed_verifies_exact_remote_head(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "ls-remote", "origin", "refs/heads/feature"]:
            return _completed("abc123\trefs/heads/feature\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "write-pushed",
                "--skill-id",
                "fix-ci",
                "--repo",
                "MoonLadderStudios/MoonMind",
                "--branch",
                "feature",
            ]
        )
        == 0
    )

    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.finish_code == "PUBLISHED_BRANCH"
    assert evidence.local_head == "abc123"


def test_write_pushed_reports_redacted_command_stderr(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="fatal token=super-secret failed",
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "write-pushed",
                "--skill-id",
                "fix-ci",
                "--repo",
                "o/r",
                "--branch",
                "feature",
            ]
        )
        == 2
    )
    stderr = capsys.readouterr().err
    assert "[redacted]" in stderr
    assert "super-secret" not in stderr


def test_write_merged_verifies_pr_state(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd[:4] == ["gh", "pr", "view", "https://github.com/o/r/pull/1"]:
            return _completed(
                json.dumps(
                    {
                        "state": "MERGED",
                        "mergedAt": "2026-01-01T00:00:00Z",
                        "mergeCommit": {"oid": "def456"},
                        "headRefOid": "abc123",
                    }
                )
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "write-merged",
                "--skill-id",
                "pr-resolver",
                "--repo",
                "o/r",
                "--branch",
                "feature",
                "--pr-url",
                "https://github.com/o/r/pull/1",
            ]
        )
        == 0
    )

    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.finish_code == "PUBLISHED_PR"
    assert evidence.remote_verified is True


def test_write_no_op_and_blocked_outcomes(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "ls-remote", "origin", "refs/heads/feature"]:
            return _completed("abc123\trefs/heads/feature\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "write-no-op",
                "--skill-id",
                "fix-comments",
                "--repo",
                "o/r",
                "--branch",
                "feature",
            ]
        )
        == 0
    )
    no_op = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert no_op.finish_code == "NO_COMMIT"

    assert (
        helper_module.main(
            [
                "write-blocked",
                "--skill-id",
                "fix-comments",
                "--repo",
                "o/r",
                "--branch",
                "feature",
                "--reason",
                "publish_unavailable",
            ]
        )
        == 0
    )
    blocked = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert blocked.status == "blocked"
    assert blocked.blocked_reason == "publish_unavailable"


def test_write_blocked_allows_unknown_repo_and_branch(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        return _completed("", returncode=1)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "write-blocked",
                "--skill-id",
                "fix-ci",
                "--repo",
                "",
                "--branch",
                "",
                "--reason",
                "publish_unavailable",
            ]
        )
        == 0
    )
    payload = _payload(tmp_path / "artifacts" / "publish_result.json")
    assert payload["repository"] == "unknown/unknown"
    assert payload["branch"] == "unknown"


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_action"),
    [
        (
            {
                "status": "merged",
                "merge_outcome": "merged",
                "mergeAutomationDisposition": "merged",
            },
            "verified",
            "merge",
        ),
        (
            {
                "status": "attempts_exhausted",
                "reason": "ci_failures",
                "mergeAutomationDisposition": "manual_review",
            },
            "blocked",
            "none",
        ),
        (
            {
                "status": "failed",
                "reason": "boom",
                "mergeAutomationDisposition": "failed",
            },
            "failed",
            "none",
        ),
    ],
)
def test_from_pr_resolver_result_translates_terminal_states(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: dict[str, Any],
    expected_status: str,
    expected_action: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    result_path = tmp_path / "var" / "pr_resolver" / "result.json"
    snapshot_path = tmp_path / "var" / "pr_resolver" / "snapshot.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(result), encoding="utf-8")
    snapshot_path.write_text(
        json.dumps(
            {
                "pr": {
                    "url": "https://github.com/o/r/pull/1",
                    "headRefName": "feature",
                    "headRefOid": "abc123",
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd[:4] == ["gh", "pr", "view", "https://github.com/o/r/pull/1"]:
            return _completed(
                json.dumps(
                    {
                        "state": "MERGED",
                        "mergedAt": "2026-01-01T00:00:00Z",
                        "mergeCommit": {"oid": "def456"},
                    }
                )
            )
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "ls-remote", "origin", "refs/heads/feature"]:
            return _completed("abc123\trefs/heads/feature\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "from-pr-resolver-result",
                "--result",
                str(result_path),
                "--snapshot",
                str(snapshot_path),
            ]
        )
        == 0
    )

    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.status == expected_status
    assert evidence.action == expected_action


def test_from_pr_resolver_result_reenter_gate_requires_remote_verification(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    result_path = tmp_path / "var" / "pr_resolver" / "result.json"
    snapshot_path = tmp_path / "var" / "pr_resolver" / "snapshot.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps({"mergeAutomationDisposition": "reenter_gate"}),
        encoding="utf-8",
    )
    snapshot_path.write_text(
        json.dumps(
            {
                "pr": {
                    "url": "https://github.com/o/r/pull/1",
                    "headRefName": "feature",
                    "headRefOid": "abc123",
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "ls-remote", "origin", "refs/heads/feature"]:
            return _completed("abc123\trefs/heads/feature\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "from-pr-resolver-result",
                "--result",
                str(result_path),
                "--snapshot",
                str(snapshot_path),
            ]
        )
        == 0
    )

    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.finish_code == "PUBLISHED_BRANCH"


def test_from_pr_resolver_result_blocks_unresolved_reenter_gate(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    result_path = tmp_path / "var" / "pr_resolver" / "result.json"
    snapshot_path = tmp_path / "var" / "pr_resolver" / "snapshot.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "attempts_exhausted",
                "reason": "actionable_comments",
                "mergeAutomationDisposition": "reenter_gate",
            }
        ),
        encoding="utf-8",
    )
    snapshot_path.write_text(
        json.dumps(
            {
                "pr": {
                    "url": "https://github.com/o/r/pull/1",
                    "headRefName": "feature",
                    "headRefOid": "abc123",
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "ls-remote", "origin", "refs/heads/feature"]:
            return _completed("abc123\trefs/heads/feature\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "from-pr-resolver-result",
                "--result",
                str(result_path),
                "--snapshot",
                str(snapshot_path),
            ]
        )
        == 0
    )
    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.status == "blocked"
    assert evidence.blocked_reason == "actionable_comments"


def test_from_pr_resolver_result_resolves_already_merged_pr_url(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    result_path = tmp_path / "var" / "pr_resolver" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "merged",
                "mergeAutomationDisposition": "already_merged",
                "pr_number": 123,
            }
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd[:4] == ["gh", "pr", "view", "123"]:
            return _completed(
                json.dumps(
                    {
                        "state": "MERGED",
                        "mergedAt": "2026-01-01T00:00:00Z",
                        "mergeCommit": {"oid": "def456"},
                        "url": "https://github.com/o/r/pull/123",
                        "headRefName": "feature",
                        "headRefOid": "abc123",
                    }
                )
            )
        if cmd[:4] == [
            "gh",
            "pr",
            "view",
            "https://github.com/o/r/pull/123",
        ]:
            return _completed(
                json.dumps(
                    {
                        "state": "MERGED",
                        "mergedAt": "2026-01-01T00:00:00Z",
                        "mergeCommit": {"oid": "def456"},
                        "headRefOid": "abc123",
                    }
                )
            )
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _completed("abc123\n")
        if cmd == ["git", "branch", "--show-current"]:
            return _completed("feature\n")
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _completed("https://github.com/o/r.git\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)

    assert (
        helper_module.main(
            [
                "from-pr-resolver-result",
                "--result",
                str(result_path),
            ]
        )
        == 0
    )
    evidence = parse_auto_publish_evidence(
        _payload(tmp_path / "artifacts" / "publish_result.json")
    )
    assert evidence.finish_code == "PUBLISHED_PR"
    assert evidence.pr_url == "https://github.com/o/r/pull/123"


def test_from_pr_resolver_result_fails_closed_for_missing_result(
    helper_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert (
        helper_module.main(
            [
                "from-pr-resolver-result",
                "--result",
                str(tmp_path / "var" / "pr_resolver" / "result.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "artifacts" / "publish_result.json").exists()
