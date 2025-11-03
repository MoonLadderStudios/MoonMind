"""Client adapter for GitHub automation used by Spec Kit workflows."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4


@dataclass(slots=True)
class GitHubPublishResult:
    """Metadata returned when publishing a branch and PR."""

    branch_name: str
    pr_url: str
    response_path: Path


class GitHubClient:
    """Minimal GitHub adapter supporting the workflow publish stage."""

    def __init__(
        self,
        *,
        repository: Optional[str] = None,
        token: Optional[str] = None,
        test_mode: bool = False,
    ) -> None:
        self._repository = repository
        self._token = token or os.getenv("GITHUB_TOKEN")
        self._test_mode = test_mode or bool(int(os.getenv("SPEC_WORKFLOW_TEST_MODE", "0")))

    def publish(
        self,
        *,
        feature_key: str,
        task_identifier: str,
        patch_path: Path,
        artifacts_dir: Path,
    ) -> GitHubPublishResult:
        """Publish the Codex patch by creating a branch and pull request."""

        artifacts_dir.mkdir(parents=True, exist_ok=True)

        if self._test_mode:
            branch_name = f"{feature_key}/{task_identifier.lower()}"
            pr_url = f"https://example.com/{feature_key}/{uuid4().hex[:6]}"
            response_path = artifacts_dir / f"{branch_name.replace('/', '_')}_pr.json"
            response_payload = {
                "repository": self._repository or "moonmind/test-repo",
                "branch": branch_name,
                "patch": patch_path.name,
                "url": pr_url,
            }
            response_path.write_text(json.dumps(response_payload, indent=2), encoding="utf-8")
            return GitHubPublishResult(
                branch_name=branch_name,
                pr_url=pr_url,
                response_path=response_path,
            )

        raise RuntimeError(
            "Real GitHub publication is not yet implemented. Enable test mode via "
            "SPEC_WORKFLOW_TEST_MODE=1 for local development."
        )


__all__ = ["GitHubClient", "GitHubPublishResult"]
