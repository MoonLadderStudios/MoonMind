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


def _repo_paths() -> list[str]:
    tracked = _run_git(["ls-files"])
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"])
    return sorted(set(tracked + untracked))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate AgentSession workflow deployment-safety gates."
    )
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument(
        "--feature-dir",
        default=os.environ.get("SPECIFY_FEATURE"),
        help=(
            "Optional active Spec Kit feature directory or name. Defaults to "
            "SPECIFY_FEATURE when set, which lets local non-feature branches use "
            "the intended feature artifacts."
        ),
    )
    args = parser.parse_args()

    playbook = REPO_ROOT / AGENT_SESSION_CUTOVER_PLAYBOOK_PATH
    playbook_text = playbook.read_text(encoding="utf-8") if playbook.exists() else ""

    try:
        active_feature_dir = resolve_active_feature_dir(
            repo_root=REPO_ROOT,
            active_feature=args.feature_dir,
        )
        report = validate_agent_session_deployment_safety(
            changed_paths=_changed_paths(args.base_ref),
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
        print(f"Active Spec Kit feature: {report.active_feature_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
