from __future__ import annotations

from pathlib import Path
from typing import Any

import runpy


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-pr-resolver"
            / "bin"
            / "batch_pr_resolver.py"
        )
    )


def test_is_local_head_uses_cross_repository_flag():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": True,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False


def test_is_local_head_accepts_same_repo_without_name_with_owner():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is True


def test_is_local_head_rejects_fork_owner_mismatch():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "another-org"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False

