#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from moonmind.workflows.temporal.deployment_safety import (
    AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
    AgentSessionDeploymentSafetyError,
    resolve_active_feature_dir,
    validate_agent_session_deployment_safety,
)

def _run_git(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]

def _changed_paths(base_ref: str) -> list[str]:
    merge_base = _run_git(["merge-base", base_ref, "HEAD"])[0]
    committed = _run_git(["diff", "--name-only", f"{merge_base}..HEAD"])
    staged = _run_git(["diff", "--name-only", "--cached"])
    unstaged = _run_git(["diff", "--name-only"])
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"])
    return sorted(set(committed + staged + unstaged + untracked))


def _changed_paths_from_file(path: str) -> list[str]:
    """Load an exact, event-derived changed-file list produced by CI.

    The file is written by ``tools/ci/compute_changed_files.sh`` from the exact
    base/head tree diff, so no merge-base discovery is needed. An empty or
    missing file means "no changed paths", which keeps the gate fail-open for
    unknown or unavailable change sets.
    """

    file_path = Path(path)
    if not file_path.is_file():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    return sorted({line.strip() for line in lines if line.strip()})

def _repo_paths() -> list[str]:
    tracked = _run_git(["ls-files"])
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"])
    return sorted(set(tracked + untracked))

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate AgentSession workflow deployment-safety gates."
    )
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument(
        "--changed-files-file",
        default=None,
        help=(
            "Path to a newline-delimited list of exact changed files (as computed "
            "by tools/ci/compute_changed_files.sh). When supplied, the validator "
            "uses this exact event-derived list and skips merge-base discovery. "
            "Preserves the --base-ref workflow for local development."
        ),
    )
    parser.add_argument(
        "--feature-dir",
        default=os.environ.get("SPECIFY_FEATURE"),
        help=(
            "Optional active MoonSpec feature directory or name. Defaults to "
            "SPECIFY_FEATURE when set, which lets local non-feature branches use "
            "the intended feature artifacts."
        ),
    )
    args = parser.parse_args(argv)

    playbook = REPO_ROOT / AGENT_SESSION_CUTOVER_PLAYBOOK_PATH
    playbook_text = playbook.read_text(encoding="utf-8") if playbook.exists() else ""

    try:
        active_feature_dir = resolve_active_feature_dir(
            repo_root=REPO_ROOT,
            active_feature=args.feature_dir,
        )
        if args.changed_files_file is not None:
            changed_paths = _changed_paths_from_file(args.changed_files_file)
        else:
            changed_paths = _changed_paths(args.base_ref)
        report = validate_agent_session_deployment_safety(
            changed_paths=changed_paths,
            repo_paths=_repo_paths(),
            cutover_playbook_text=playbook_text,
            active_feature_dir=active_feature_dir,
        )
    except AgentSessionDeploymentSafetyError as exc:
        print(f"AgentSession deployment safety validation failed: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        details = "\n".join(
            part.strip()
            for part in (str(exc), exc.stderr or "", exc.stdout or "")
            if part.strip()
        )
        print(
            f"AgentSession deployment safety validation failed: {details}",
            file=sys.stderr,
        )
        return 1

    if report.required:
        changed = ", ".join(report.changed_sensitive_paths)
        print(
            "AgentSession deployment safety validation passed "
            f"for sensitive changes: {changed}"
        )
    else:
        print("AgentSession deployment safety validation passed; no sensitive changes.")
    if report.active_feature_dir:
        print(f"Active MoonSpec feature: {report.active_feature_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
