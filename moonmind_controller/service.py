"""Restart recovery for the standalone controller.

Stdlib-only. On restart the controller inspects Docker and its local
operation record, then converges only unfinished work toward the same
target:

- a record already marked installed for the desired image is complete and
  must not trigger another apply;
- a record with attempts exhausted waits for an explicit Retry instead of
  relaunching automatically;
- before launching a competing Compose child, an existing child owned by
  the same operation is reconciled (reattached) or stopped.

Reporting or cleanup failures never erase a confirmed installation and
never hide a failed mandatory verification.
"""

from __future__ import annotations

from moonmind_controller import state


def converge_on_restart(
    record: dict, *, child_running: bool, child_owner_matches: bool
) -> str:
    """Decide the restart action: ``complete``, ``resume``, ``await_retry``.

    ``resume`` still requires the caller to reconcile or stop a competing
    Compose child first (see :func:`reconcile_child`).
    """
    if state.apply_already_complete(record):
        return "complete"
    attempts = list(record.get("attempts") or [])
    if len(attempts) >= state.MAX_ATTEMPTS:
        return "await_retry"
    if child_running and not child_owner_matches:
        return "await_retry"
    return "resume"


def reconcile_child(*, child_running: bool, child_owner_matches: bool) -> str:
    """Reconcile a possibly-competing Compose child before launching anew."""
    if not child_running:
        return "launch"
    if child_owner_matches:
        return "reattach"
    return "stop_competing"


def failure_summary(history: list) -> str:
    """Name the failure that started the operation, not only the last one."""
    if not history:
        return "Deployment operation exhausted its bounded retry budget"
    first, last = history[0], history[-1]
    if first.get("error") == last.get("error"):
        return str(first.get("error"))
    return (
        f"attempt {first.get('attempt')}: {first.get('error')} "
        f"(final attempt {last.get('attempt')}: {last.get('error')})"
    )
