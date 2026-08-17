"""Versioned declarative fault-scenario format for the Omnigent control plane.

Owned by MoonLadderStudios/MoonMind#3709.

A scenario describes -- declaratively and deterministically -- how the
programmable fake provider, host, lease, workspace, artifact, transport, and
activity boundaries behave across a run. The canonical schema version is
``moonmind.omnigent-fault-scenario/v1``.

Example
-------
::

    schemaVersion: moonmind.omnigent-fault-scenario/v1
    seed: 12345
    steps:
      - on: ensure_session
        sideEffect: created
        response: success
      - on: submit_turn
        sideEffect: accepted
        response: drop
      - on: read_events
        emit:
          - type: turn.running
        disconnect: true
      - on: observe_snapshot
        return:
          sessionState: idle
          turnState: completed
          unfinishedToolCalls: 0
      - on: read_events
        emit: []

Compatibility policy
--------------------
Unknown schema versions are *quarantined* by default: :func:`load_scenario`
raises :class:`ScenarioSchemaError` unless ``quarantine=True`` is passed, in
which case a quarantined :class:`Scenario` is returned that refuses to execute.
This satisfies the "compatibility safety" property -- unknown provider or
scenario schema versions fail or quarantine according to declared policy rather
than being silently coerced onto the current production path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from moonmind.omnigent.faultkit.commands import CommandWindow, LogicalCommand

CANONICAL_SCENARIO_SCHEMA_VERSION = "moonmind.omnigent-fault-scenario/v1"

#: Every schema version this build knows how to execute.
SUPPORTED_SCENARIO_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    {CANONICAL_SCENARIO_SCHEMA_VERSION}
)


class ScenarioSchemaError(ValueError):
    """Raised when a scenario document is malformed or uses an unknown schema."""


class ResponseMode(str, Enum):
    """How the provider responds after (optionally) committing a side effect."""

    SUCCESS = "success"
    #: The side effect is committed but the response never reaches the caller.
    DROP = "drop"
    ERROR = "error"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    #: Response carries an unknown/newer provider schema version.
    UNKNOWN_SCHEMA = "unknown_schema"
    AUTH_FAILURE = "auth_failure"


class SideEffectKind(str, Enum):
    """The durable provider-side effect a command commits, if any."""

    NONE = "none"
    CREATED = "created"
    ATTACHED = "attached"
    ACCEPTED = "accepted"
    DELETED = "deleted"
    REPLACED = "replaced"


def _coerce_command(raw: Any) -> LogicalCommand:
    try:
        return LogicalCommand(str(raw))
    except ValueError as exc:  # pragma: no cover - exercised via load tests
        raise ScenarioSchemaError(f"unknown logical command {raw!r}") from exc


def _coerce_response(raw: Any) -> ResponseMode:
    if raw is None:
        return ResponseMode.SUCCESS
    try:
        return ResponseMode(str(raw))
    except ValueError as exc:
        raise ScenarioSchemaError(f"unknown response mode {raw!r}") from exc


def _coerce_side_effect(raw: Any) -> SideEffectKind:
    if raw is None:
        return SideEffectKind.NONE
    try:
        return SideEffectKind(str(raw))
    except ValueError as exc:
        raise ScenarioSchemaError(f"unknown side effect {raw!r}") from exc


def _coerce_window(raw: Any) -> CommandWindow | None:
    if raw is None:
        return None
    try:
        return CommandWindow(str(raw))
    except ValueError as exc:
        raise ScenarioSchemaError(f"unknown command window {raw!r}") from exc


@dataclass(frozen=True)
class ScenarioStep:
    """One declarative step describing behavior for a logical command."""

    on: LogicalCommand
    response: ResponseMode = ResponseMode.SUCCESS
    side_effect: SideEffectKind = SideEffectKind.NONE
    emit: tuple[Mapping[str, Any], ...] = ()
    snapshot: Mapping[str, Any] | None = None
    disconnect: bool = False
    duplicate: bool = False
    reorder: bool = False
    heartbeat: bool = False
    latency_ms: int = 0
    #: Crash the logical command at this window (fail-before / fail-after point).
    crash_at: CommandWindow | None = None
    #: A named host/lease/infrastructure fault to apply for this command.
    fault: str | None = None
    #: Fencing generation the provider observation belongs to.
    generation: int = 0
    #: Logical turn label; steps sharing a label share a turn idempotency identity.
    turn: str = "1"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ScenarioStep":
        if "on" not in raw:
            raise ScenarioSchemaError("scenario step is missing required key 'on'")
        emit_raw = raw.get("emit") or ()
        if not isinstance(emit_raw, Sequence) or isinstance(emit_raw, (str, bytes)):
            raise ScenarioSchemaError("'emit' must be a list of event mappings")
        emit = tuple(dict(event) for event in emit_raw)
        snapshot_raw = raw.get("return") if "return" in raw else raw.get("snapshot")
        snapshot = dict(snapshot_raw) if snapshot_raw is not None else None
        return cls(
            on=_coerce_command(raw["on"]),
            response=_coerce_response(raw.get("response")),
            side_effect=_coerce_side_effect(raw.get("sideEffect")),
            emit=emit,
            snapshot=snapshot,
            disconnect=bool(raw.get("disconnect", False)),
            duplicate=bool(raw.get("duplicate", False)),
            reorder=bool(raw.get("reorder", False)),
            heartbeat=bool(raw.get("heartbeat", False)),
            latency_ms=int(raw.get("latencyMs", 0)),
            crash_at=_coerce_window(raw.get("crashAt")),
            fault=(str(raw["fault"]) if raw.get("fault") is not None else None),
            generation=int(raw.get("generation", 0)),
            turn=str(raw.get("turn", "1")),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialize back to the declarative form (round-trip safe, no secrets)."""
        out: dict[str, Any] = {"on": self.on.value}
        if self.response is not ResponseMode.SUCCESS:
            out["response"] = self.response.value
        if self.side_effect is not SideEffectKind.NONE:
            out["sideEffect"] = self.side_effect.value
        if self.emit:
            out["emit"] = [dict(event) for event in self.emit]
        if self.snapshot is not None:
            out["return"] = dict(self.snapshot)
        if self.disconnect:
            out["disconnect"] = True
        if self.duplicate:
            out["duplicate"] = True
        if self.reorder:
            out["reorder"] = True
        if self.heartbeat:
            out["heartbeat"] = True
        if self.latency_ms:
            out["latencyMs"] = self.latency_ms
        if self.crash_at is not None:
            out["crashAt"] = self.crash_at.value
        if self.fault is not None:
            out["fault"] = self.fault
        if self.generation:
            out["generation"] = self.generation
        if self.turn != "1":
            out["turn"] = self.turn
        return out


@dataclass(frozen=True)
class Scenario:
    """A parsed, versioned, deterministic fault scenario."""

    schema_version: str
    seed: int
    steps: tuple[ScenarioStep, ...]
    name: str = "unnamed"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    quarantined: bool = False

    @property
    def supported(self) -> bool:
        return self.schema_version in SUPPORTED_SCENARIO_SCHEMA_VERSIONS

    def require_executable(self) -> None:
        """Fail fast if this scenario must not run on the production path."""
        if self.quarantined or not self.supported:
            raise ScenarioSchemaError(
                "refusing to execute quarantined/unsupported scenario schema "
                f"{self.schema_version!r}"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "seed": self.seed,
            "name": self.name,
            "metadata": dict(self.metadata),
            "steps": [step.to_mapping() for step in self.steps],
        }


def load_scenario(
    document: Mapping[str, Any],
    *,
    quarantine: bool = False,
) -> Scenario:
    """Parse a declarative scenario document into a :class:`Scenario`.

    Unknown schema versions raise :class:`ScenarioSchemaError` unless
    ``quarantine`` is set, which returns a non-executable quarantined scenario.
    """

    if not isinstance(document, Mapping):
        raise ScenarioSchemaError("scenario document must be a mapping")
    schema_version = str(
        document.get("schemaVersion", CANONICAL_SCENARIO_SCHEMA_VERSION)
    )
    if schema_version not in SUPPORTED_SCENARIO_SCHEMA_VERSIONS:
        if not quarantine:
            raise ScenarioSchemaError(
                f"unsupported scenario schemaVersion {schema_version!r}; "
                f"supported: {sorted(SUPPORTED_SCENARIO_SCHEMA_VERSIONS)}"
            )
        return Scenario(
            schema_version=schema_version,
            seed=int(document.get("seed", 0)),
            steps=(),
            name=str(document.get("name", "quarantined")),
            metadata=dict(document.get("metadata", {})),
            quarantined=True,
        )

    steps_raw = document.get("steps")
    if steps_raw is None or not isinstance(steps_raw, Sequence):
        raise ScenarioSchemaError("scenario is missing a 'steps' list")
    steps = tuple(ScenarioStep.from_mapping(step) for step in steps_raw)
    seed_raw = document.get("seed", 0)
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError) as exc:
        raise ScenarioSchemaError(f"seed must be an integer, got {seed_raw!r}") from exc
    return Scenario(
        schema_version=schema_version,
        seed=seed,
        steps=steps,
        name=str(document.get("name", "unnamed")),
        metadata=dict(document.get("metadata", {})),
    )


def _faultkit_yaml_loader():  # type: ignore[no-untyped-def]
    """A SafeLoader where ``on``/``off``/``yes``/``no`` stay strings.

    The declarative format uses ``on:`` as a step key; YAML 1.1 would otherwise
    coerce it (and ``yes``/``no``/``off``) to a boolean. Only ``true``/``false``
    remain implicit booleans, so ``disconnect: true`` still parses as a bool.
    """
    import re

    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    # Copy the shared resolver table so we never mutate the base SafeLoader.
    _Loader.yaml_implicit_resolvers = {
        key: list(value)
        for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    for first_char in "yYnNtTfFoO":
        resolvers = _Loader.yaml_implicit_resolvers.get(first_char)
        if resolvers:
            _Loader.yaml_implicit_resolvers[first_char] = [
                (tag, regexp)
                for tag, regexp in resolvers
                if tag != "tag:yaml.org,2002:bool"
            ]
    _Loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    return _Loader


def load_scenario_yaml(text: str, *, quarantine: bool = False) -> Scenario:
    """Parse a YAML scenario document."""
    import yaml

    document = yaml.load(text, Loader=_faultkit_yaml_loader())
    return load_scenario(document, quarantine=quarantine)


def load_scenario_file(path: str | Path, *, quarantine: bool = False) -> Scenario:
    """Load a scenario from a ``.yaml``/``.yml`` or ``.json`` file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        return load_scenario_yaml(text, quarantine=quarantine)
    import json

    return load_scenario(json.loads(text), quarantine=quarantine)


__all__ = [
    "CANONICAL_SCENARIO_SCHEMA_VERSION",
    "SUPPORTED_SCENARIO_SCHEMA_VERSIONS",
    "ScenarioSchemaError",
    "ResponseMode",
    "SideEffectKind",
    "ScenarioStep",
    "Scenario",
    "load_scenario",
    "load_scenario_yaml",
    "load_scenario_file",
]
