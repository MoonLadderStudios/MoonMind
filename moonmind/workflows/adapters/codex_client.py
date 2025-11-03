"""Client wrapper for interacting with Codex Cloud submissions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4


@dataclass(slots=True)
class CodexSubmissionResult:
    """Structured response from a Codex submission."""

    task_id: str
    logs_path: Path
    summary: str


@dataclass(slots=True)
class CodexDiffResult:
    """Patch retrieval result returned by Codex."""

    patch_path: Path
    description: str
    has_changes: bool = True


class CodexClient:
    """Lightweight adapter encapsulating Codex automation entrypoints."""

    def __init__(
        self,
        *,
        environment: Optional[str] = None,
        model: Optional[str] = None,
        profile: Optional[str] = None,
        test_mode: bool = False,
    ) -> None:
        self._environment = environment
        self._model = model
        self._profile = profile
        self._test_mode = test_mode or bool(int(os.getenv("SPEC_WORKFLOW_TEST_MODE", "0")))

    # ------------------------------------------------------------------
    # Submission lifecycle
    # ------------------------------------------------------------------
    def submit(
        self,
        *,
        feature_key: str,
        task_identifier: str,
        task_summary: str,
        artifacts_dir: Path,
    ) -> CodexSubmissionResult:
        """Submit the next phase to Codex and return metadata."""

        artifacts_dir.mkdir(parents=True, exist_ok=True)

        if self._test_mode:
            task_id = f"TEST-{uuid4().hex[:8]}"
            logs_path = artifacts_dir / f"{task_id}.jsonl"
            payload = {
                "event": "submitted",
                "taskId": task_id,
                "feature": feature_key,
                "taskIdentifier": task_identifier,
                "summary": task_summary,
            }
            logs_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            return CodexSubmissionResult(
                task_id=task_id,
                logs_path=logs_path,
                summary=f"Submitted {task_identifier} for {feature_key}",
            )

        raise RuntimeError(
            "Real Codex submission is not yet implemented. Enable test mode via "
            "SPEC_WORKFLOW_TEST_MODE=1 for local development."
        )

    def retrieve_patch(
        self,
        *,
        task_id: str,
        artifacts_dir: Path,
        task_identifier: str,
        task_summary: str,
    ) -> CodexDiffResult:
        """Fetch the generated patch for a previously submitted task."""

        artifacts_dir.mkdir(parents=True, exist_ok=True)

        if self._test_mode:
            patch_path = artifacts_dir / f"{task_id}.patch"
            patch_path.write_text(
                (
                    "--- a/README.md\n"
                    "+++ b/README.md\n"
                    "@@\n"
                    f"-Pending task: {task_identifier}\n"
                    f"+Completed task: {task_identifier}\n"
                ),
                encoding="utf-8",
            )
            description = (
                "Simulated Codex patch for "
                f"{task_identifier}: {task_summary.strip()}"
            )
            return CodexDiffResult(
                patch_path=patch_path,
                description=description,
                has_changes=True,
            )

        raise RuntimeError(
            "Real Codex patch retrieval is not yet implemented. Enable test mode via "
            "SPEC_WORKFLOW_TEST_MODE=1 for local development."
        )


__all__ = ["CodexClient", "CodexSubmissionResult", "CodexDiffResult"]
