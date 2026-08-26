"""Deterministic helpers shared by escaped-failure journey replays."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPLAY_ROOT = Path(__file__).with_name("replays")


def load_replay(failure_shape_id: str, filename: str) -> Any:
    """Load reviewable replay evidence without network or credentials."""
    return json.loads(
        (REPLAY_ROOT / failure_shape_id / filename).read_text(encoding="utf-8")
    )


@dataclass
class NestedYieldProcess:
    """Deterministic model of wrapper completion before its inner process."""

    inner_session_id: str
    inner_active: bool = True
    wrapper_active: bool = True

    def first_tool_yield(self) -> dict[str, str]:
        return {"session_id": self.inner_session_id, "status": "running"}

    def wrapper_completes(self) -> dict[str, str]:
        self.wrapper_active = False
        return {"status": "completed"}

    def finish_inner(self) -> None:
        self.inner_active = False


@dataclass
class FinalizationFaultInjector:
    """Reusable fail-first wrapper for auxiliary finalization operations."""

    failures_remaining: int = 1
    calls: int = 0

    async def invoke(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("injected finalization failure")
        return await operation(*args, **kwargs)
