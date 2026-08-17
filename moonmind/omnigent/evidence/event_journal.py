"""Bounded, deduplicated event journal index.

A projection over normalized observations that keeps a bounded in-memory index
(most-recent-N) while the authoritative event bodies live as artifacts. The
journal indexes evidence; it never decides lifecycle.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JournalEntry:
    sequence: int
    normalized_status: str
    deduplication_key: str
    artifact_ref: str | None = None


class EventJournal:
    def __init__(self, *, max_entries: int = 512) -> None:
        self._entries: "deque[JournalEntry]" = deque(maxlen=max_entries)
        self._keys: set[str] = set()

    def record(self, entry: JournalEntry) -> bool:
        """Record an entry. Returns ``False`` if the dedup key was already seen."""

        if entry.deduplication_key in self._keys:
            return False
        # ``maxlen`` eviction can drop a key from the deque; keep the key set
        # bounded to the live window so replays past the window re-index.
        if len(self._entries) == self._entries.maxlen and self._entries:
            evicted = self._entries[0]
            self._keys.discard(evicted.deduplication_key)
        self._keys.add(entry.deduplication_key)
        self._entries.append(entry)
        return True

    def recent(self) -> list[JournalEntry]:
        return list(self._entries)


__all__ = ["EventJournal", "JournalEntry"]
