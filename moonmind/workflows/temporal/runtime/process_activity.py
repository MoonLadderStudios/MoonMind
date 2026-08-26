"""Process-tree activity probing for managed run progress evidence.

A managed run's progress must be observable without cooperation from the runtime
it launched. Output is not sufficient: a one-shot CLI such as ``claude -p``
buffers everything until the turn completes, so a healthy hour-long run looks
byte-for-byte identical to a wedged one on stdout. Workspace mutation is better
but incomplete — an agent running a test suite writes only into ignored
directories, so it can look idle for tens of minutes while working hard.

CPU time closes that gap. A process that is executing accrues CPU time; one that
is blocked forever on a dead socket, awaiting stdin, or deadlocked accrues none.
This probe sums the cumulative CPU time of every process in the supervised
session and reports when that total last advanced.

Tradeoff, stated explicitly: a runtime that livelocks (busy-spins) does accrue
CPU, so it reads as progress here. That is contained by the execution budget's
hard ceiling (``ExecutionBudget.max_seconds``), which no amount of observed
progress can extend past. Killing healthy long runs at a flat deadline is the
worse failure of the two.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROC_ROOT = Path("/proc")
# ``/proc/<pid>/stat`` layout, 1-indexed per proc(5). ``comm`` (field 2) may
# contain spaces and parentheses, so the line is split at the LAST ``)`` and the
# remaining whitespace-separated tokens start at field 3.
_STAT_FIELD_OFFSET = 3
_STAT_FIELD_SESSION = 6
_STAT_FIELD_UTIME = 14
_STAT_FIELD_STIME = 15
# Minimum CPU-time advance that counts as activity. A process that is not being
# scheduled at all accrues exactly zero, so this only needs to clear scheduler
# accounting noise — it must stay small enough that a mostly-idle runtime waiting
# on a provider response still registers when it wakes to stream a response.
_MIN_CPU_DELTA_SECONDS = 0.05


def _clock_ticks_per_second() -> float:
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        return 100.0
    return float(ticks) if ticks and ticks > 0 else 100.0


class ProcessActivityProbe:
    """Tracks when a supervised process session last consumed CPU time.

    The supervised process is launched with ``start_new_session=True``, so it is
    its own session leader and every descendant — the privilege-drop wrapper, the
    runtime CLI, and anything it spawns such as a compiler or test runner —
    carries its session id. Summing by session id therefore covers the whole tree
    without needing to walk parent links or track reaped children.

    ``sample`` is cheap enough to call on every heartbeat: it reads one small
    file per running process and does no allocation-heavy parsing.

    The total is cumulative across the session's whole lifetime, not just its
    currently live processes: CPU consumed by a descendant that has since exited
    is retained, so the total never falls and a delta of zero always means the
    session really did no work between samples.
    """

    def __init__(self, *, session_pid: int) -> None:
        self._session_pid = int(session_pid)
        self._ticks_per_second = _clock_ticks_per_second()
        self._last_total_seconds: float | None = None
        self._last_activity_at: datetime | None = None
        self._unavailable = not _PROC_ROOT.is_dir()
        self._warned = False
        # Per-process CPU carried across samples so the session total is
        # genuinely cumulative. ``/proc`` only shows live processes, so summing
        # it alone makes the total *fall* when a descendant exits — a managed
        # agent running a sequence of short-lived compilers or test processes
        # would then read as stalled while working continuously, because each
        # replacement has to re-earn the CPU its predecessor took with it.
        self._live_ticks: dict[int, int] = {}
        self._retired_ticks = 0

    @property
    def available(self) -> bool:
        """Whether this platform exposes the ``/proc`` data the probe needs."""

        return not self._unavailable

    def sample(self, now: datetime) -> datetime | None:
        """Return when the session's CPU total last advanced, if ever observed.

        Returns ``None`` until an advance has actually been seen, so an
        unavailable or never-scheduled process contributes no progress evidence
        rather than a misleading timestamp.
        """

        if self._unavailable:
            return None
        total = self._total_cpu_seconds()
        if total is None:
            # The session has exited (or /proc became unreadable). Keep whatever
            # activity was already observed; the exit path owns the outcome.
            return self._last_activity_at
        previous = self._last_total_seconds
        self._last_total_seconds = total
        if previous is None:
            # Baseline sample. The CPU consumed before the first observation
            # cannot be attributed to a point in time, so it is not evidence yet.
            return self._last_activity_at
        if total - previous >= _MIN_CPU_DELTA_SECONDS:
            self._last_activity_at = now
        return self._last_activity_at

    def _total_cpu_seconds(self) -> float | None:
        """Cumulative session CPU including exited descendants, or ``None``.

        ``None`` means the session is gone; the exit path owns that outcome.
        """

        try:
            entries = os.listdir(_PROC_ROOT)
        except OSError:
            if not self._warned:
                logger.debug("Process activity probe cannot read /proc", exc_info=True)
                self._warned = True
            self._unavailable = True
            return None

        live: dict[int, int] = {}
        for entry in entries:
            if not entry.isdigit():
                continue
            sample = self._read_stat(_PROC_ROOT / entry / "stat")
            if sample is None:
                continue
            session, ticks = sample
            if session != self._session_pid:
                continue
            live[int(entry)] = ticks

        if not live:
            return None

        for pid, ticks in self._live_ticks.items():
            current = live.get(pid)
            if current is None:
                # Exited between samples: its CPU stays in the session total.
                self._retired_ticks += ticks
            elif current < ticks:
                # The pid was recycled into this session. Retire the predecessor
                # so the recycled process starts from its own zero rather than
                # dragging the total backwards.
                self._retired_ticks += ticks

        self._live_ticks = live
        return (self._retired_ticks + sum(live.values())) / self._ticks_per_second

    @staticmethod
    def _read_stat(stat_path: Path) -> tuple[int, int] | None:
        """Return ``(session_id, utime+stime_ticks)`` for one process."""

        try:
            raw = stat_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Process exited between listdir and read, or is not readable.
            return None
        close = raw.rfind(")")
        if close == -1:
            return None
        fields = raw[close + 1 :].split()

        def _field(index: int) -> int | None:
            offset = index - _STAT_FIELD_OFFSET
            if offset < 0 or offset >= len(fields):
                return None
            try:
                return int(fields[offset])
            except ValueError:
                return None

        session = _field(_STAT_FIELD_SESSION)
        utime = _field(_STAT_FIELD_UTIME)
        stime = _field(_STAT_FIELD_STIME)
        if session is None or utime is None or stime is None:
            return None
        return session, utime + stime


__all__ = ["ProcessActivityProbe"]
