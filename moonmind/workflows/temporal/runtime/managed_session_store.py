"""JSON file-backed durable store for managed session supervision records."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord


TERMINAL_MANAGED_SESSION_STATUSES = frozenset({"terminated", "degraded", "failed"})


class ManagedSessionStore:
    """Persist ``CodexManagedSessionRecord`` objects under a store root."""

    def __init__(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)

    def _resolve_path(self, session_id: str) -> Path:
        relative = Path(session_id)
        if not relative.parts:
            raise ValueError("session_id must not be empty")
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ValueError(
                "session_id must be a relative path without traversal components"
            )
        resolved = (self.store_root / f"{session_id}.json").resolve()
        root_resolved = self.store_root.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise ValueError("session_id resolves outside store root")
        return resolved

    def save(self, record: CodexManagedSessionRecord) -> Path:
        path = self._resolve_path(record.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = record.model_dump(mode="json", by_alias=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return path

    def load(self, session_id: str) -> CodexManagedSessionRecord | None:
        path = self._resolve_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return CodexManagedSessionRecord(**data)

    def update(self, session_id: str, **kwargs: Any) -> CodexManagedSessionRecord:
        record = self.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        for key, value in kwargs.items():
            if not hasattr(record, key):
                raise AttributeError(
                    f"CodexManagedSessionRecord has no attribute '{key}'"
                )
            setattr(record, key, value)
        self.save(record)
        return record

    def list_active(self) -> list[CodexManagedSessionRecord]:
        self.store_root.mkdir(parents=True, exist_ok=True)
        records: list[CodexManagedSessionRecord] = []
        for path in self.store_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = CodexManagedSessionRecord(**data)
            except (json.JSONDecodeError, ValueError):
                continue
            if record.status not in TERMINAL_MANAGED_SESSION_STATUSES:
                records.append(record)
        return records
