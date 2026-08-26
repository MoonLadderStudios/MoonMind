"""Behavior tests for the shared CI changed-file helper.

MoonLadderStudios/MoonMind#3326: one reusable event classifier is shared by the
selector, deployment-safety validation, and the generated-contract detector.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ci" / "compute_changed_files.sh"

ZERO_SHA = "0" * 40


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _run_helper(
    repo: Path,
    tmp_path: Path,
    *,
    event_name: str,
    event_payload: dict | None = None,
    github_sha: str = "",
) -> tuple[str, list[str]]:
    out = tmp_path / "changed.txt"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_SHA": github_sha,
    }
    if event_payload is not None:
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event_payload), encoding="utf-8")
        env["GITHUB_EVENT_PATH"] = str(event_path)

    result = subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    changed = out.read_text(encoding="utf-8").split() if out.exists() else []
    return result.stdout.strip(), changed


def test_push_event_computes_exact_tree_diff(tmp_path) -> None:
    repo, base, head = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="push",
        event_payload={"before": base, "after": head},
        github_sha=head,
    )

    assert stdout == "resolution=known"
    assert changed == ["b.txt"]


def test_pull_request_event_computes_exact_tree_diff(tmp_path) -> None:
    repo, base, head = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="pull_request",
        event_payload={
            "pull_request": {"base": {"sha": base}, "head": {"sha": head}}
        },
    )

    assert stdout == "resolution=known"
    assert changed == ["b.txt"]


def test_merge_group_event_computes_exact_tree_diff(tmp_path) -> None:
    repo, base, head = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="merge_group",
        event_payload={"merge_group": {"base_sha": base, "head_sha": head}},
    )

    assert stdout == "resolution=known"
    assert changed == ["b.txt"]


def test_push_with_zero_before_is_unknown(tmp_path) -> None:
    repo, _, head = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="push",
        event_payload={"before": ZERO_SHA, "after": head},
        github_sha=head,
    )

    assert stdout == "resolution=unknown"
    assert changed == []


def test_workflow_dispatch_is_unknown(tmp_path) -> None:
    repo, _, _ = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="workflow_dispatch",
        event_payload={},
    )

    assert stdout == "resolution=unknown"
    assert changed == []


def test_schedule_is_unknown(tmp_path) -> None:
    repo, _, _ = _init_repo(tmp_path)

    stdout, changed = _run_helper(
        repo,
        tmp_path,
        event_name="schedule",
        event_payload={},
    )

    assert stdout == "resolution=unknown"
    assert changed == []


def test_creates_custom_output_parent_directory(tmp_path) -> None:
    repo, base, head = _init_repo(tmp_path)
    output = tmp_path / "nested" / "changed.txt"
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"before": base, "after": head}), encoding="utf-8"
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), str(output)],
        cwd=repo,
        env={
            "PATH": os.environ["PATH"],
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_SHA": head,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert output.read_text(encoding="utf-8").splitlines() == ["b.txt"]
