"""JSON file-backed store for managed run records."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Union

from moonmind.schemas.agent_runtime_models import (
    AgentRunState,
    ManagedRunRecord,
    TERMINAL_AGENT_RUN_STATES,
)


class ManagedRunStore:
    """Persists ManagedRunRecord as individual JSON files under a store root."""

    def __init__(self, store_root: Union[str, Path]) -> None:
        self.store_root = Path(store_root)

    def _resolve_path(self, run_id: str) -> Path:
        """Return a safe path for a run record, rejecting traversal attempts."""
        relative = Path(run_id)
        if not relative.parts:
            raise ValueError("run_id must not be empty")
        if relative.is_absolute() or any(
            part == ".." for part in relative.parts
        ):
            raise ValueError(
                "run_id must be a relative path without traversal components"
            )
        resolved = (self.store_root / f"{run_id}.json").resolve()
        root_resolved = self.store_root.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise ValueError("run_id resolves outside store root")
        return resolved

    def save(self, record: ManagedRunRecord) -> Path:
        """Atomically write a run record to disk."""
        path = self._resolve_path(record.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = record.model_dump(mode="json", by_alias=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # Best-effort temp-file cleanup; original error is re-raised below
            raise
        return path

    def load(self, run_id: str) -> ManagedRunRecord | None:
        """Load a run record from disk, returning None if missing."""
        path = self._resolve_path(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ManagedRunRecord(**data)

    def update_status(
        self,
        run_id: str,
        status: AgentRunState,
        **kwargs: object,
    ) -> ManagedRunRecord:
        """Load a record, update its status and optional fields, then save."""
        record = self.load(run_id)
        if record is None:
            raise ValueError(f"run record not found: {run_id}")
        record.status = status
        for key, value in kwargs.items():
            if not hasattr(record, key):
                raise AttributeError(
                    f"ManagedRunRecord has no attribute '{key}'"
                )
            setattr(record, key, value)
        self.save(record)
        return record

    def list_active(self) -> list[ManagedRunRecord]:
        """Return all non-terminal run records."""
        self.store_root.mkdir(parents=True, exist_ok=True)
        records: list[ManagedRunRecord] = []
        for path in self.store_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = ManagedRunRecord(**data)
                if record.status not in TERMINAL_AGENT_RUN_STATES:
                    records.append(record)
            except (json.JSONDecodeError, ValueError):
                continue
        return records

    def find_latest_for_workflow(self, workflow_id: str) -> ManagedRunRecord | None:
        """Return the newest managed run bound to one logical workflow.

        Prefer active runs over terminal runs so task detail attaches to the
        current live run during reruns / Continue-As-New.
        """
        normalized_workflow_id = str(workflow_id or "").strip()
        if not normalized_workflow_id:
            raise ValueError("workflow_id must not be empty")

        self.store_root.mkdir(parents=True, exist_ok=True)
        candidates: list[ManagedRunRecord] = []
        for path in self.store_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = ManagedRunRecord(**data)
            except (json.JSONDecodeError, ValueError):
                continue
            if str(record.workflow_id or "").strip() != normalized_workflow_id:
                continue
            candidates.append(record)

        if not candidates:
            return None

        epoch = datetime.min.replace(tzinfo=UTC)

        def _sort_key(record: ManagedRunRecord) -> tuple[int, datetime, datetime]:
            active_priority = 1 if record.status not in TERMINAL_AGENT_RUN_STATES else 0
            activity_time = record.finished_at or record.started_at or epoch
            started_at = record.started_at or epoch
            return (active_priority, activity_time, started_at)

        return max(candidates, key=_sort_key)
