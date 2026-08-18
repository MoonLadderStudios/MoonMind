"""Versioned declarative fault-scenario format for the Omnigent fault lab.

Source issue: MoonLadderStudios/MoonMind#3709
([Omnigent control plane 8/11] Build a programmable fault-injection provider and
model-based reliability suite).

A *fault scenario* is a declarative, seed-based description of how the
programmable fake provider, host, lease, workspace, artifact, transport, and
activity layers should behave for one bounded lifecycle. It is intentionally
data-only: it carries **no** database, network, filesystem, Docker, artifact,
logging, telemetry, or Temporal dependency so the same scenario can be replayed
from unit, component, Temporal, integration, and browser tests.

The format is versioned (``moonmind.omnigent-fault-scenario/v1``). Unknown schema
versions fail or quarantine according to declared policy (acceptance criterion
"unknown provider or scenario schema versions fail or quarantine"), which is the
scenario-layer counterpart of the reconciler's compatibility invariant.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _ScenarioYamlLoader(yaml.SafeLoader):
    """YAML loader that does not coerce ``on``/``off``/``yes``/``no`` to bool.

    The declarative scenario format uses ``on:`` as a step key (matching the
    issue's example). Under YAML 1.1's default resolver that bare word parses as
    the boolean ``True``, which would silently corrupt every step. This loader
    restricts the boolean resolver to ``true``/``false`` only, so ``on`` stays a
    string while ``disconnect: true`` still parses as a boolean.
    """


_ScenarioYamlLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for (tag, regexp) in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ScenarioYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)

#: The single supported declarative scenario schema version.
FAULT_SCENARIO_SCHEMA_VERSION = "moonmind.omnigent-fault-scenario/v1"

#: Every schema version this loader can execute. Anything else is unknown.
KNOWN_SCENARIO_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    {FAULT_SCENARIO_SCHEMA_VERSION}
)


class UnknownScenarioSchemaVersionError(ValueError):
    """Raised when a scenario declares a schema version this build cannot run.

    Carries the offending version so the caller can quarantine it without
    echoing arbitrary scenario bytes into a log or record.
    """

    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(f"unknown omnigent-fault-scenario schema version: {version!r}")


class LogicalOperation(str, Enum):
    """The logical operations a scenario can script.

    These map onto the reducer's durable side-effect command kinds plus the
    read-only provider observation operations. Each is a *logical* operation with
    a stable idempotency identity, which is what lets the fake assert at-most-once
    behavior independently of MoonMind state.
    """

    ENSURE_PROFILE_LEASE = "ensure_profile_lease"
    ENSURE_HOST = "ensure_host"
    ENSURE_SESSION = "ensure_session"
    SUBMIT_TURN = "submit_turn"
    READ_EVENTS = "read_events"
    OBSERVE_SNAPSHOT = "observe_snapshot"
    HARVEST_EVIDENCE = "harvest_evidence"
    BEGIN_CLEANUP = "begin_cleanup"
    RELEASE_LEASES = "release_leases"


class ResponseBehavior(str, Enum):
    """How the provider transport behaves when returning a response.

    ``SUCCESS`` returns normally. Every other value models a transport or
    protocol fault. ``DROP`` is the load-bearing "side effect succeeded but the
    response was lost" case that must never produce a duplicate command.
    """

    SUCCESS = "success"
    DROP = "drop"
    LATENCY = "latency"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    UNKNOWN_SCHEMA = "unknown_schema"
    AUTH_FAILURE = "auth_failure"


class SideEffect(str, Enum):
    """Whether the provider actually performed the durable side effect.

    This is the provider-side ground truth recorded in the ledger, independent of
    what response (if any) the caller received.
    """

    NONE = "none"
    CREATED = "created"
    ACCEPTED = "accepted"
    RECORDED = "recorded"
    RELEASED = "released"
    REMOVED = "removed"


class CommandWindow(str, Enum):
    """Shared fail-before / fail-after injection points for a logical command.

    A crash at one of these windows models the API or worker process restarting
    at an explicit command boundary. The five windows are the ones named in the
    issue and are the only supported crash points, so every logical command has
    the same, testable set of interruption sites.
    """

    BEFORE_CLAIM = "before_claim"
    AFTER_CLAIM_BEFORE_SIDE_EFFECT = "after_claim_before_side_effect"
    AFTER_SIDE_EFFECT_BEFORE_RECEIPT = "after_side_effect_before_receipt"
    AFTER_RECEIPT_BEFORE_STATE_TRANSITION = "after_receipt_before_state_transition"
    AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE = (
        "after_transition_before_activity_response"
    )


#: Deterministic ordering of the command windows, earliest to latest. A crash at
#: an earlier window strictly precedes the corresponding side effect and durable
#: transition, which the harness relies on when applying a command.
COMMAND_WINDOW_ORDER: tuple[CommandWindow, ...] = (
    CommandWindow.BEFORE_CLAIM,
    CommandWindow.AFTER_CLAIM_BEFORE_SIDE_EFFECT,
    CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT,
    CommandWindow.AFTER_RECEIPT_BEFORE_STATE_TRANSITION,
    CommandWindow.AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE,
)


class _ScenarioModel(BaseModel):
    """Base for scenario objects: camelCase wire form, snake_case construction."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class EmittedEvent(_ScenarioModel):
    """A single event a ``read_events`` step emits onto the frontier."""

    type: str
    #: Optional monotonic cursor label. When two steps reuse a cursor the harness
    #: can model a replay gap or a duplicate delivery.
    cursor: str | None = None


class SnapshotReturn(_ScenarioModel):
    """What an ``observe_snapshot`` step reports.

    ``session_state`` / ``turn_state`` mirror the raw provider vocabulary the
    reducer classifies; the fields are deliberately raw strings so a scenario can
    inject unknown vocabulary without the loader rejecting it.
    """

    session_state: str | None = None
    turn_state: str | None = None
    unfinished_tool_calls: int = 0
    #: When set, the snapshot names a *different* provider session id than the
    #: durable one, modelling a stale/contradictory observation.
    provider_session_id: str | None = None


class ScenarioStep(_ScenarioModel):
    """One scripted step of provider / infrastructure behavior."""

    on: LogicalOperation
    side_effect: SideEffect = SideEffect.NONE
    response: ResponseBehavior = ResponseBehavior.SUCCESS
    emit: tuple[EmittedEvent, ...] = ()
    ret: SnapshotReturn | None = Field(default=None, alias="return")
    #: Drop the transport connection after this step (SSE/WebSocket disconnect).
    disconnect: bool = False
    #: Re-deliver the previous batch (duplicate) on the next read.
    duplicate: bool = False
    #: Deliver this step's events out of frontier order (reorder).
    reorder: bool = False
    #: Crash the worker/API at this window while handling this command.
    crash_at: CommandWindow | None = None


class FaultScenario(_ScenarioModel):
    """A complete, versioned declarative fault scenario.

    ``ground_truth_terminal`` records what the provider *actually* did so the
    reference model and invariant checks can assert convergence to the correct
    terminal independently of MoonMind's own state. ``recovery_round`` bounds the
    fault window: after it, the world reports the ground truth honestly, which is
    what makes eventual convergence a decidable property.
    """

    schema_version: Literal["moonmind.omnigent-fault-scenario/v1"] = (
        FAULT_SCENARIO_SCHEMA_VERSION
    )
    seed: int = 0
    #: Stable identifier used when this scenario is stored in the corpus.
    scenario_id: str | None = None
    #: Source incident or PR reference, e.g. ``"#3698"``. No free-form logs.
    source_ref: str | None = None
    steps: tuple[ScenarioStep, ...] = ()
    ground_truth_terminal: Literal["success", "failure", "cancelled"] = "success"
    #: Lifecycle knobs that control host, lease, cleanup, and activity behavior.
    requires_profile_lease: bool = True
    requires_host: bool = True
    requires_cleanup: bool = True
    desired_cancel: bool = False
    max_turn_attempts: int = Field(default=3, gt=0)
    #: The world stops injecting faults once this many reconcile rounds elapse, so
    #: the "provider remains observable" precondition of eventual convergence is
    #: eventually met. Must be positive.
    recovery_round: int = Field(default=8, gt=0)
    #: Optional generalized invariant name this scenario is meant to protect.
    invariant: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialize to a plain camelCase dict suitable for YAML/JSON storage."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def load_scenario(
    data: dict[str, Any],
    *,
    on_unknown: Literal["fail", "quarantine"] = "fail",
) -> FaultScenario | None:
    """Load and validate a declarative scenario dict.

    Compatibility policy (acceptance criterion "unknown provider or scenario
    schema versions fail or quarantine according to declared policy"):

    * ``on_unknown="fail"`` raises :class:`UnknownScenarioSchemaVersionError`.
    * ``on_unknown="quarantine"`` returns ``None`` so the caller can skip the
      scenario without crashing a batch.
    """

    version = data.get("schemaVersion") or data.get("schema_version")
    if version not in KNOWN_SCENARIO_SCHEMA_VERSIONS:
        if on_unknown == "quarantine":
            return None
        raise UnknownScenarioSchemaVersionError(str(version))
    return FaultScenario.model_validate(data)


def dumps_scenario(scenario: FaultScenario) -> str:
    """Serialize a scenario to deterministic YAML for the replay corpus."""

    return yaml.safe_dump(scenario.to_wire(), sort_keys=True, default_flow_style=False)


def loads_scenario(
    text: str,
    *,
    on_unknown: Literal["fail", "quarantine"] = "fail",
) -> FaultScenario | None:
    """Parse a YAML scenario document with the declared compatibility policy."""

    return load_scenario(yaml.load(text, Loader=_ScenarioYamlLoader), on_unknown=on_unknown)


__all__ = [
    "FAULT_SCENARIO_SCHEMA_VERSION",
    "KNOWN_SCENARIO_SCHEMA_VERSIONS",
    "UnknownScenarioSchemaVersionError",
    "LogicalOperation",
    "ResponseBehavior",
    "SideEffect",
    "CommandWindow",
    "COMMAND_WINDOW_ORDER",
    "EmittedEvent",
    "SnapshotReturn",
    "ScenarioStep",
    "FaultScenario",
    "load_scenario",
    "dumps_scenario",
    "loads_scenario",
]
