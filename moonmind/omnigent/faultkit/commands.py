"""Logical command vocabulary and command-window injection points.

Owned by MoonLadderStudios/MoonMind#3709.

Every operation the Omnigent control plane performs against a provider, host,
lease, or infrastructure resource is expressed as one :class:`LogicalCommand`.
Each logical command executes through the same five-phase window so a crash can
be injected deterministically at every boundary that let a production incident
escape.
"""

from __future__ import annotations

from enum import Enum


class LogicalCommand(str, Enum):
    """A single logical operation the control plane performs.

    The value is the stable string used in the declarative scenario ``on:``
    field and in recorded evidence, so it must not be renamed without updating
    every scenario fixture in the corpus.
    """

    # Discovery
    DISCOVER = "discover"
    # Provider session lifecycle
    ENSURE_SESSION = "ensure_session"
    ATTACH_SESSION = "attach_session"
    SUBMIT_TURN = "submit_turn"
    READ_EVENTS = "read_events"
    OBSERVE_SNAPSHOT = "observe_snapshot"
    READ_TRANSCRIPT = "read_transcript"
    DELETE_SESSION = "delete_session"
    # Host lifecycle
    HOST_REGISTER = "host_register"
    HOST_HEARTBEAT = "host_heartbeat"
    HOST_EXIT = "host_exit"
    HOST_REPLACE = "host_replace"
    # Provider Profile lease lifecycle
    LEASE_ACQUIRE = "lease_acquire"
    LEASE_RENEW = "lease_renew"
    LEASE_EXPIRE = "lease_expire"
    LEASE_RELEASE = "lease_release"
    LEASE_REPLACE = "lease_replace"
    # Infrastructure
    WORKSPACE_MATERIALIZE = "workspace_materialize"
    WORKSPACE_PUBLISH = "workspace_publish"
    ARTIFACT_WRITE = "artifact_write"
    ARTIFACT_READ = "artifact_read"
    CLEANUP = "cleanup"
    # Control-plane reconciliation tick
    RECONCILE = "reconcile"


class CommandWindow(str, Enum):
    """Shared fail-before / fail-after injection points for a logical command.

    These windows are the boundaries the brief requires: a crash injected at
    each one must still converge to a safe terminal or active state.
    """

    BEFORE_CLAIM = "before_claim"
    AFTER_CLAIM_BEFORE_SIDE_EFFECT = "after_claim_before_side_effect"
    AFTER_SIDE_EFFECT_BEFORE_RECEIPT = "after_side_effect_before_receipt"
    AFTER_RECEIPT_BEFORE_STATE_TRANSITION = "after_receipt_before_state_transition"
    AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE = (
        "after_transition_before_activity_response"
    )


#: Ordered command windows, from earliest to latest inside one logical command.
COMMAND_WINDOWS: tuple[CommandWindow, ...] = (
    CommandWindow.BEFORE_CLAIM,
    CommandWindow.AFTER_CLAIM_BEFORE_SIDE_EFFECT,
    CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT,
    CommandWindow.AFTER_RECEIPT_BEFORE_STATE_TRANSITION,
    CommandWindow.AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE,
)


class CommandWindowCrash(RuntimeError):
    """Raised by an injector when a scenario crashes a logical command window.

    The reconciler treats this as an activity crash: the command's remaining
    phases do not run, but any side effect already committed by the fake
    provider remains recorded so at-most-once behavior can be asserted.
    """

    def __init__(self, command: LogicalCommand, window: CommandWindow) -> None:
        self.command = command
        self.window = window
        super().__init__(f"injected crash for {command.value} at {window.value}")


__all__ = [
    "LogicalCommand",
    "CommandWindow",
    "COMMAND_WINDOWS",
    "CommandWindowCrash",
]
