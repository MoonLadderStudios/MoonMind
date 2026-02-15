"""Helper functions for Codex and GitHub workflow interactions.

These utilities centralize branch naming, commit publishing, and log path
handling so Celery tasks can remain focused on orchestration.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery.workspace import (
    generate_branch_name,
    with_retry_suffix,
)

logger = logging.getLogger(__name__)

__all__ = [
    "derive_branch_name",
    "push_commits",
    "resolve_codex_logs_path",
]


def derive_branch_name(
    feature_key: str,
    run_id: UUID | str,
    *,
    attempt: int = 1,
    timestamp: Optional[datetime] = None,
) -> str:
    """Return a deterministic branch name for the workflow run.

    Branches are generated from the feature identifier and run id, with
    optional retry suffixes for subsequent attempts. The timestamp parameter
    enables deterministic outputs in tests.
    """

    base_branch = generate_branch_name(run_id, prefix=feature_key, timestamp=timestamp)
    return with_retry_suffix(base_branch, attempt)


def resolve_codex_logs_path(
    run_id: UUID | str,
    *,
    artifacts_root: str | Path | None = None,
    task_id: str | None = None,
    ensure_parents: bool = True,
) -> Path:
    """Return the JSONL log path for Codex streaming output.

    The default location mirrors the spec: ``var/artifacts/spec_workflows/<run>/``.
    When ``task_id`` is provided, the file name is derived from it to preserve
    unique logs per Codex submission.
    """

    root = Path(artifacts_root or settings.spec_workflow.artifacts_root)
    filename = f"{task_id}.jsonl" if task_id else "codex.jsonl"
    log_path = root.joinpath(str(run_id), filename)
    if ensure_parents:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def push_commits(
    repo_path: str | Path,
    branch_name: str,
    *,
    remote: str = "origin",
    force: bool = False,
    test_mode: Optional[bool] = None,
) -> str:
    """Push commits for ``branch_name`` to ``remote`` using git.

    In test mode the push is skipped but the intended ref is returned so callers
    can proceed with deterministic behavior during contract tests.
    """

    repository = Path(repo_path)
    if not repository.is_dir():
        raise ValueError(
            f"Repository path {repository} does not exist or is not a directory"
        )

    ref = f"{remote}/{branch_name}"
    if test_mode is None:
        test_mode = settings.spec_workflow.test_mode

    if test_mode:
        logger.info(
            "Skipping git push in test mode",
            extra={
                "repository": str(repository),
                "branch": branch_name,
                "remote": remote,
            },
        )
        return ref

    if remote.startswith("-"):
        raise ValueError("Remote name cannot start with '-'")
    if branch_name.startswith("-"):
        raise ValueError("Branch name cannot start with '-'")

    command = ["git", "-C", str(repository), "push"]
    if force:
        command.append("--force-with-lease")
    command.extend([remote, branch_name])

    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Failed to push branch to remote. "
            f"Command '{' '.join(exc.cmd)}' exited with return code {exc.returncode}."
        ) from exc

    logger.info(
        "Pushed branch",
        extra={"repository": str(repository), "branch": branch_name, "remote": remote},
    )
    return ref
