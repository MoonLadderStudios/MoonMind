"""JSON file-backed store for managed run records."""

from __future__ import annotations

import json
import os
import tempfile
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
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, str(path))
        except BaseException:
            with open(os.devnull, "w"):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass  # Best-effort cleanup; original error is re-raised below
            raise
        return path

    def load(self, run_id: str) -> ManagedRunRecord | None:
        """Load a run record from disk, returning None if missing."""
        path = self._resolve_path(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
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
                data = json.loads(path.read_text())
                record = ManagedRunRecord(**data)
                if record.status not in TERMINAL_AGENT_RUN_STATES:
                    records.append(record)
            except (json.JSONDecodeError, ValueError):
                continue
        return records
