"""Command log port: durable record of commands an application use case emitted.

Separated from the decision log so command execution (side effects) and decision
provenance (why) can evolve and be audited independently.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from moonmind.omnigent.domain.commands import Command


@runtime_checkable
class CommandLog(Protocol):
    async def record(self, bridge_session_id: str, command: Command) -> None: ...

    async def pending(self, bridge_session_id: str) -> Sequence[Command]: ...


__all__ = ["CommandLog"]
