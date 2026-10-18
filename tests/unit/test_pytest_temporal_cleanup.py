"""Keep fatal test-server cleanup bounded and restricted to owned processes."""

from __future__ import annotations

import select
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_timeout

from tests import conftest


def _cleanup_processes(
    monkeypatch, tmp_path, *, parent=42, command=None, count=1, remaining_event=0
):
    closed, signaled, waits = [], [], []
    command = command or b"/custom temporal executable\0server\0start-dev\0"
    for pid in range(123, 123 + count):
        proc = tmp_path / str(pid)
        proc.mkdir()
        (proc / "status").write_text(f"PPid:\t{parent}\n")
        (proc / "cmdline").write_bytes(command)

    class Poll:
        def register(self, fd, events):
            pass

        def unregister(self, fd):
            pass

        def poll(self, timeout):
            waits.append(timeout)
            if len(waits) == 1 and count > 1:
                return [(10, select.POLLIN)]
            return [(11, remaining_event)] if remaining_event else []

    monkeypatch.setattr(
        conftest,
        "os",
        SimpleNamespace(
            getpid=lambda: 42,
            pidfd_open=lambda pid: pid - 113,
            close=closed.append,
        ),
    )
    monkeypatch.setattr(
        conftest,
        "signal",
        SimpleNamespace(
            SIGKILL=signal.SIGKILL,
            pidfd_send_signal=lambda fd, sig: signaled.append((fd, sig)),
        ),
    )
    monkeypatch.setattr(
        conftest.subprocess,
        "check_output",
        lambda *args, **kwargs: "\n".join(str(pid) for pid in range(123, 123 + count)),
    )
    monkeypatch.setattr(conftest, "Path", lambda path: tmp_path / Path(path).name)
    monkeypatch.setattr(
        conftest,
        "select",
        SimpleNamespace(
            poll=Poll,
            POLLIN=select.POLLIN,
            POLLHUP=select.POLLHUP,
            POLLERR=select.POLLERR,
            POLLNVAL=select.POLLNVAL,
        ),
    )
    return closed, signaled, waits


@pytest.mark.parametrize("missing", ["pidfd_open", "pidfd_send_signal"])
def test_missing_pidfd_support_leaves_cleanup_safe(monkeypatch, tmp_path, missing):
    closed, signaled, waits = _cleanup_processes(monkeypatch, tmp_path)
    monkeypatch.delattr(
        conftest.os if missing == "pidfd_open" else conftest.signal, missing
    )

    def unexpected_discovery(*args, **kwargs):
        pytest.fail("Unsupported cleanup must not fall back to numeric-PID signals")

    monkeypatch.setattr(conftest.subprocess, "check_output", unexpected_discovery)
    conftest._kill_owned_temporal_servers()
    assert (closed, signaled, waits) == ([], [], [])


@pytest.mark.parametrize(
    "parent, command",
    [(99, None), (42, b"/bin/sleep\0" + b"10\0")],
)
def test_cleanup_rechecks_parent_and_invocation(monkeypatch, tmp_path, parent, command):
    closed, signaled, waits = _cleanup_processes(
        monkeypatch, tmp_path, parent=parent, command=command
    )
    conftest._kill_owned_temporal_servers()
    assert closed == [10]
    assert signaled == []
    assert waits == []


@pytest.mark.parametrize("remaining_event", [0, select.POLLERR, select.POLLNVAL])
def test_unconfirmed_exit_is_bounded_and_keeps_fatal_callback(
    monkeypatch, tmp_path, capfd, remaining_event
):
    closed, signaled, waits = _cleanup_processes(
        monkeypatch, tmp_path, count=2, remaining_event=remaining_event
    )
    times = iter([10, 10.25, 11.75])
    monkeypatch.setattr(
        conftest, "time", SimpleNamespace(monotonic=lambda: next(times))
    )
    timers, fatal = [], []

    def timer_factory(timeout, callback):
        timer = SimpleNamespace(start=lambda: None, callback=callback)
        timers.append(timer)
        return timer

    def fatal_callback(item, settings):
        assert "Temporal cleanup did not observe exit: [124]" in capfd.readouterr().err
        fatal.append(item)

    monkeypatch.setattr(conftest.threading, "Timer", timer_factory)
    monkeypatch.setattr(pytest_timeout, "timeout_timer", fatal_callback)
    item = SimpleNamespace(nodeid="owned-worker")
    settings = SimpleNamespace(
        method="thread", timeout=3, disable_debugger_detection=True
    )
    assert conftest.pytest_timeout_set_timer(item, settings) is True
    timers[0].callback()
    assert waits == [1750, 250]
    assert closed == [10, 11]
    assert signaled == [(10, signal.SIGKILL), (11, signal.SIGKILL)]
    assert fatal == [item]
