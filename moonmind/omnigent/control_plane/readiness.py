"""Bounded new-admission readiness surface for the Omnigent control plane.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

:func:`evaluate_admission_readiness` computes one bounded readiness projection
for admitting a *new* Omnigent session. It is a pure function over capability
signals; it performs no probing I/O itself (the caller supplies observed
capability results).

Two issue invariants are structural:

* **Fail closed for new admission.** A capability is ``READY`` only when it is
  explicitly observed ready. An unknown (``None``) or negative signal makes the
  session inadmissible — admission never proceeds on absent evidence
  (acceptance criterion "new-admission readiness includes actual runtime
  capability and evidence freshness").
* **Historical reads and cleanup stay available.** Inadmissibility only gates
  *new* work; :attr:`AdmissionReadiness.allow_historical_reads` and
  :attr:`AdmissionReadiness.allow_cleanup` remain ``True`` so existing sessions
  can still be inspected and cleaned up safely.

MoonLadderStudios/MoonMind#3885 adds a third invariant:

* **Stable qualification is separate from transient availability.** A
  capability is either :attr:`ReadinessClass.STRUCTURAL` (this deployment can
  run the combination at all) or :attr:`ReadinessClass.TRANSIENT` (this
  deployment currently has a free slot). Structural capabilities fail closed on
  unknown, as before. Transient capabilities are *observed pressure*: unknown
  means "no pressure observed" and never blocks, and observed saturation makes
  new work :attr:`AdmissionReadiness.wait_for_capacity` — durable waiting —
  while :attr:`AdmissionReadiness.structurally_supported` stays ``True``. A
  busy or throttled installation therefore does not flap to structurally
  unsupported and can never be read as a reason to substitute another
  credential, profile, or runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class ReadinessState(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class ReadinessClass(str, Enum):
    """Whether a capability describes qualification or current availability."""

    #: The deployment cannot run this combination at all until it is restored.
    STRUCTURAL = "structural"
    #: The deployment can run it, but has no free slot right now.
    TRANSIENT = "transient"


class ReadinessCapability(str, Enum):
    """Closed capability vocabulary mirrored by the metrics ``capability`` label."""

    RECONCILER_GENERATION = "reconciler_generation"
    SCHEMA = "schema"
    PROVIDER_SNAPSHOT = "provider_snapshot"
    EVENT_TRANSPORT = "event_transport"
    SERVER_BUILD = "server_build"
    UI_BUILD = "ui_build"
    HOST_BUILD = "host_build"
    WEBSOCKET = "websocket"
    WORKER_BACKEND = "worker_backend"
    CONTAINER_BACKEND = "container_backend"
    OBSERVATION_FRESHNESS = "observation_freshness"
    JANITOR = "janitor"
    EXACT_IMAGE = "exact_image"
    PROTECTED_LIVE_EVIDENCE = "protected_live_evidence"
    #: Transient capacity pressure (MoonLadderStudios/MoonMind#3885).
    PROVIDER_CAPACITY = "provider_capacity"
    HOST_CAPACITY = "host_capacity"
    WORKER_CAPACITY = "worker_capacity"


#: Capabilities that describe how full the deployment is right now. Unknown
#: never blocks: an unmeasured layer has not been shown to be saturated.
TRANSIENT_CAPABILITIES: frozenset[ReadinessCapability] = frozenset(
    {
        ReadinessCapability.PROVIDER_CAPACITY,
        ReadinessCapability.HOST_CAPACITY,
        ReadinessCapability.WORKER_CAPACITY,
    }
)

#: Capabilities whose absence means this deployment is not qualified to run the
#: combination at all. These fail closed on unknown.
STRUCTURAL_CAPABILITIES: frozenset[ReadinessCapability] = frozenset(
    capability
    for capability in ReadinessCapability
    if capability not in TRANSIENT_CAPABILITIES
)


def readiness_class(capability: ReadinessCapability) -> ReadinessClass:
    """Return whether a capability is qualification or current availability."""

    return (
        ReadinessClass.TRANSIENT
        if capability in TRANSIENT_CAPABILITIES
        else ReadinessClass.STRUCTURAL
    )


#: Capabilities required before a *new* session may be admitted. Every declared
#: capability is required: admission fails closed unless all are READY.
REQUIRED_FOR_ADMISSION: frozenset[ReadinessCapability] = frozenset(ReadinessCapability)


@dataclass(frozen=True)
class CapabilityReadiness:
    capability: ReadinessCapability
    state: ReadinessState
    detail: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.state is ReadinessState.READY


@dataclass(frozen=True)
class ReadinessInputs:
    """Observed capability signals. ``None`` means *unknown* (fails closed).

    Boolean capability flags: ``True`` ready, ``False`` not ready, ``None``
    unknown. Evidence freshness is expressed as an age plus a max age; a missing
    age is unknown.
    """

    reconciler_generation_ready: Optional[bool] = None
    schema_compatible: Optional[bool] = None
    provider_snapshot_ready: Optional[bool] = None
    event_transport_ready: Optional[bool] = None
    server_build_ready: Optional[bool] = None
    ui_build_ready: Optional[bool] = None
    host_build_ready: Optional[bool] = None
    websocket_available: Optional[bool] = None
    worker_backend_ready: Optional[bool] = None
    container_backend_ready: Optional[bool] = None
    observation_age: Optional[timedelta] = None
    observation_max_age: timedelta = timedelta(minutes=10)
    janitor_healthy: Optional[bool] = None
    exact_image_conformant: Optional[bool] = None
    protected_live_evidence_age: Optional[timedelta] = None
    protected_live_evidence_max_age: timedelta = timedelta(hours=24)
    #: Observed transient pressure. ``True`` means a slot is free, ``False``
    #: means the layer is currently saturated, ``None`` means no pressure was
    #: observed. Unlike the structural flags above, ``None`` does not block:
    #: absence of an observation is not evidence of saturation, and treating it
    #: as such would make every unobserved deployment structurally unsupported.
    provider_capacity_available: Optional[bool] = None
    host_capacity_available: Optional[bool] = None
    worker_capacity_available: Optional[bool] = None


@dataclass(frozen=True)
class AdmissionReadiness:
    """Overall bounded readiness projection for new admission."""

    admit_new: bool
    capabilities: tuple[CapabilityReadiness, ...]
    #: Historical reads and cleanup for existing sessions are always safe.
    allow_historical_reads: bool = True
    allow_cleanup: bool = True

    def capability(self, capability: ReadinessCapability) -> CapabilityReadiness:
        for entry in self.capabilities:
            if entry.capability is capability:
                return entry
        raise KeyError(capability)

    @property
    def blocking(self) -> tuple[ReadinessCapability, ...]:
        """Capabilities that are not READY and therefore block admission."""

        return tuple(
            entry.capability
            for entry in self.capabilities
            if entry.capability in REQUIRED_FOR_ADMISSION and not entry.ready
        )

    @property
    def structural_blocking(self) -> tuple[ReadinessCapability, ...]:
        """Capabilities whose absence disqualifies this deployment."""

        return tuple(
            capability
            for capability in self.blocking
            if capability in STRUCTURAL_CAPABILITIES
        )

    @property
    def transient_blocking(self) -> tuple[ReadinessCapability, ...]:
        """Capacity layers observed saturated right now."""

        return tuple(
            capability
            for capability in self.blocking
            if capability in TRANSIENT_CAPABILITIES
        )

    @property
    def structurally_supported(self) -> bool:
        """Whether the deployment is qualified, ignoring current pressure."""

        return not self.structural_blocking

    @property
    def wait_for_capacity(self) -> bool:
        """Whether valid new work should wait durably instead of being refused.

        True only when every structural capability is ready and the sole
        blocker is observed capacity pressure. That is the case the issue calls
        out: a busy installation must queue, not report itself unsupported and
        invite a substitution.
        """

        return self.structurally_supported and bool(self.transient_blocking)

    def to_dict(self) -> dict[str, object]:
        return {
            "admitNew": self.admit_new,
            "allowHistoricalReads": self.allow_historical_reads,
            "allowCleanup": self.allow_cleanup,
            "structurallySupported": self.structurally_supported,
            "waitForCapacity": self.wait_for_capacity,
            "blocking": [c.value for c in self.blocking],
            "structuralBlocking": [c.value for c in self.structural_blocking],
            "transientBlocking": [c.value for c in self.transient_blocking],
            "capabilities": [
                {
                    "capability": e.capability.value,
                    "state": e.state.value,
                    "readinessClass": readiness_class(e.capability).value,
                    "detail": e.detail,
                }
                for e in self.capabilities
            ],
        }


def _flag_state(value: Optional[bool]) -> ReadinessState:
    if value is True:
        return ReadinessState.READY
    if value is False:
        return ReadinessState.NOT_READY
    return ReadinessState.UNKNOWN


def _capacity_state(value: Optional[bool]) -> ReadinessState:
    """Return the state of one observed transient capacity layer.

    Unknown reads as READY on purpose. Transient pressure is a positive
    observation about *right now*; an unobserved layer has not been shown to be
    saturated, and failing closed here would turn "we did not measure" into
    "this deployment is unsupported".
    """

    return ReadinessState.NOT_READY if value is False else ReadinessState.READY


def _fresh_state(age: Optional[timedelta], max_age: timedelta) -> ReadinessState:
    if age is None:
        return ReadinessState.UNKNOWN
    # A negative age means the evidence is timestamped in the future (clock skew
    # or malformed evidence). Treat it as unknown rather than fresh so a
    # future-dated capability probe or conformance artifact can never bypass the
    # fail-closed admission gate.
    if age < timedelta(0):
        return ReadinessState.UNKNOWN
    return ReadinessState.READY if age <= max_age else ReadinessState.NOT_READY


def evaluate_admission_readiness(
    inputs: ReadinessInputs, *, now: Optional[datetime] = None
) -> AdmissionReadiness:
    """Compute the bounded admission-readiness projection, failing closed.

    ``now`` is accepted for signature symmetry with the rest of the control
    plane; freshness is passed in as an age so this function stays pure and
    deterministic.
    """

    capabilities = (
        CapabilityReadiness(
            ReadinessCapability.RECONCILER_GENERATION,
            _flag_state(inputs.reconciler_generation_ready),
        ),
        CapabilityReadiness(ReadinessCapability.SCHEMA, _flag_state(inputs.schema_compatible)),
        CapabilityReadiness(
            ReadinessCapability.PROVIDER_SNAPSHOT, _flag_state(inputs.provider_snapshot_ready)
        ),
        CapabilityReadiness(
            ReadinessCapability.EVENT_TRANSPORT, _flag_state(inputs.event_transport_ready)
        ),
        CapabilityReadiness(ReadinessCapability.SERVER_BUILD, _flag_state(inputs.server_build_ready)),
        CapabilityReadiness(ReadinessCapability.UI_BUILD, _flag_state(inputs.ui_build_ready)),
        CapabilityReadiness(ReadinessCapability.HOST_BUILD, _flag_state(inputs.host_build_ready)),
        CapabilityReadiness(
            ReadinessCapability.WEBSOCKET,
            _flag_state(inputs.websocket_available),
            None if inputs.websocket_available else "WebSocket runtime capability unavailable",
        ),
        CapabilityReadiness(
            ReadinessCapability.WORKER_BACKEND, _flag_state(inputs.worker_backend_ready)
        ),
        CapabilityReadiness(
            ReadinessCapability.CONTAINER_BACKEND, _flag_state(inputs.container_backend_ready)
        ),
        CapabilityReadiness(
            ReadinessCapability.OBSERVATION_FRESHNESS,
            _fresh_state(inputs.observation_age, inputs.observation_max_age),
        ),
        CapabilityReadiness(ReadinessCapability.JANITOR, _flag_state(inputs.janitor_healthy)),
        CapabilityReadiness(
            ReadinessCapability.EXACT_IMAGE,
            _flag_state(inputs.exact_image_conformant),
            None if inputs.exact_image_conformant else "exact-image conformance not confirmed",
        ),
        CapabilityReadiness(
            ReadinessCapability.PROTECTED_LIVE_EVIDENCE,
            _fresh_state(
                inputs.protected_live_evidence_age, inputs.protected_live_evidence_max_age
            ),
        ),
        CapabilityReadiness(
            ReadinessCapability.PROVIDER_CAPACITY,
            _capacity_state(inputs.provider_capacity_available),
            None
            if inputs.provider_capacity_available is not False
            else "Provider Profile capacity is fully utilized; new work waits.",
        ),
        CapabilityReadiness(
            ReadinessCapability.HOST_CAPACITY,
            _capacity_state(inputs.host_capacity_available),
            None
            if inputs.host_capacity_available is not False
            else "Generic host capacity is fully utilized; new work waits.",
        ),
        CapabilityReadiness(
            ReadinessCapability.WORKER_CAPACITY,
            _capacity_state(inputs.worker_capacity_available),
            None
            if inputs.worker_capacity_available is not False
            else "Execution worker capacity is fully utilized; new work waits.",
        ),
    )

    admit_new = all(
        entry.ready for entry in capabilities if entry.capability in REQUIRED_FOR_ADMISSION
    )
    return AdmissionReadiness(admit_new=admit_new, capabilities=capabilities)


__all__ = [
    "ReadinessState",
    "ReadinessCapability",
    "ReadinessClass",
    "REQUIRED_FOR_ADMISSION",
    "STRUCTURAL_CAPABILITIES",
    "TRANSIENT_CAPABILITIES",
    "readiness_class",
    "CapabilityReadiness",
    "ReadinessInputs",
    "AdmissionReadiness",
    "evaluate_admission_readiness",
]
