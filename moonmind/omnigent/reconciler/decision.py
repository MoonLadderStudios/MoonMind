"""The versioned :class:`ReconciliationDecision` output type.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

A decision is a pure value object. It carries everything a downstream executor
needs to act safely under optimistic concurrency without re-deriving intent:

* the closed :class:`~moonmind.omnigent.reconciler.vocabulary.DecisionAction`;
* stable reason codes;
* the expected durable revision and fencing generation (so a command cannot
  ignore concurrency authority — invariant 11 / acceptance criterion);
* a deterministic command identity/spec when a side effect is required;
* a bounded next reconciliation deadline or an explicit external wait authority
  for every nonterminal decision (invariant 10, enforced in ``__post_init__``);
* declared evidence requirements;
* whether the decision changes product-visible state.

The object self-validates. It performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from moonmind.omnigent.reconciler.versions import (
    DECISION_SCHEMA_VERSION,
    SUPPORTED_DECISION_VERSIONS,
    require_supported_version,
)
from moonmind.omnigent.reconciler.vocabulary import DecisionAction, ReasonCode


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A side-effect command specification with a deterministic identity.

    ``command_id`` is derived deterministically from durable identity so a
    re-reconciliation of an unchanged state proposes the *same* command id,
    supporting at-most-once execution downstream (invariant 7). ``parameters``
    is a bounded tuple of non-secret string pairs only.
    """

    kind: str
    command_id: str
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """A named evidence requirement and whether it is currently satisfied."""

    name: str
    satisfied: bool


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """The explicit, versioned decision produced by :func:`reconcile`."""

    action: DecisionAction
    reason_codes: tuple[ReasonCode, ...]
    expected_revision: int
    expected_fencing_generation: int
    changes_product_visible_state: bool
    terminal: bool
    command: CommandSpec | None = None
    next_deadline: datetime | None = None
    wait_authority: str | None = None
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    schema_version: str = DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_supported_version(
            "ReconciliationDecision",
            self.schema_version,
            SUPPORTED_DECISION_VERSIONS,
        )
        if not self.reason_codes:
            raise ValueError("ReconciliationDecision requires at least one reason code")
        # Invariant 10: every nonterminal decision must bound the next step,
        # either with a concrete deadline or an explicit external wait authority.
        if not self.terminal and self.next_deadline is None and not self.wait_authority:
            raise ValueError(
                "Nonterminal decision must carry a next_deadline or wait_authority "
                f"(action={self.action.value})"
            )

    @property
    def reason_code_values(self) -> tuple[str, ...]:
        return tuple(code.value for code in self.reason_codes)


__all__ = [
    "CommandSpec",
    "EvidenceRequirement",
    "ReconciliationDecision",
]
