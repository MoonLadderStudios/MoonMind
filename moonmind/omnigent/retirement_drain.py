"""Active-owner drain evidence for the legacy retirement guard.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 3).

Before an implementation is removed, MoonMind must *prove* no owner still uses
it: active Temporal workflows, AgentRuns and Omnigent sessions, Provider Profile
leases, host bindings and leases, credential consumers, static or on-demand
hosts, pending publication, pending checkpoint or remediation work, and
incomplete cleanup or janitor authority.

The producer here is deliberately port-shaped. Counting live owners is an I/O
side effect that belongs to adapters over the Temporal client, the control-plane
store, and the lease repositories; this module owns only the aggregation rule
and the fail-closed contract the guard depends on:

* A dependency kind the row declares but *no probe observed* is not drained.
  Missing evidence never reads as "nothing left".
* Stale evidence is not drained. An observation older than the freshness bound
  is treated the same as a missing one.
* A probe that failed is not drained, and its failure is reported by kind.
* Observations carry counts and operator-safe probe refs only — never provider
  session ids, host ids, credentials, or internal endpoints.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.omnigent.legacy_retirement import (
    ActiveOwnerKind,
    LegacyPathRecord,
)

DRAIN_CONTRACT_VERSION = "moonmind.omnigent-retirement-drain/v1"

# Drain evidence is only meaningful while it is fresh; an old snapshot cannot
# prove the current absence of owners.
DEFAULT_EVIDENCE_MAX_AGE = timedelta(hours=1)

# Operator-safe probe references are bounded identifiers, never a provider
# session id, host id, credential, or internal endpoint.
PROBE_REF_MAX_LENGTH = 64
# Dot-separated lowercase words only, at most four segments. Provider session
# ids, host ids, tokens, and endpoints all fail this shape, so an accidental
# leak into an operator-facing drain report is rejected rather than published.
_PROBE_REF_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){1,3}$")


class DrainEvidenceError(ValueError):
    """Raised when drain evidence is malformed."""


class ActiveOwnerObservation(BaseModel):
    """One probe result for one active-owner kind.

    ``active_count`` is the number of owners still holding the component. Zero
    with ``probe_succeeded`` is the only shape that drains a kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    kind: ActiveOwnerKind
    active_count: int = Field(ge=0, alias="activeCount")
    probe_ref: str = Field(alias="probeRef")
    observed_at: datetime = Field(alias="observedAt")
    probe_succeeded: bool = Field(True, alias="probeSucceeded")

    @field_validator("probe_ref")
    @classmethod
    def _validate_probe_ref(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned or len(cleaned) > PROBE_REF_MAX_LENGTH:
            raise ValueError("probe_ref must be a bounded non-empty identifier")
        if not _PROBE_REF_PATTERN.match(cleaned):
            raise ValueError(
                "probe_ref must be an operator-safe identifier, never a provider "
                "session id, host id, credential, or endpoint"
            )
        return cleaned

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


@runtime_checkable
class ActiveOwnerProbe(Protocol):
    """Adapter port that counts live owners of one component."""

    async def observe(
        self, path: LegacyPathRecord, kind: ActiveOwnerKind
    ) -> ActiveOwnerObservation:
        raise NotImplementedError


class DrainEvidence(BaseModel):
    """Aggregated, fail-closed drain evidence for one retirement row."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    generated_at: datetime = Field(alias="generatedAt")
    drained_kinds: frozenset[ActiveOwnerKind] = Field(alias="drainedKinds")
    blocking_kinds: tuple[ActiveOwnerKind, ...] = Field(alias="blockingKinds")
    missing_kinds: tuple[ActiveOwnerKind, ...] = Field(alias="missingKinds")
    stale_kinds: tuple[ActiveOwnerKind, ...] = Field(alias="staleKinds")
    failed_kinds: tuple[ActiveOwnerKind, ...] = Field(alias="failedKinds")
    contract_version: str = Field(DRAIN_CONTRACT_VERSION, alias="contractVersion")

    @property
    def fully_drained(self) -> bool:
        return not self.blocking_kinds

    def as_dict(self) -> dict[str, object]:
        payload = self.model_dump(by_alias=True, mode="json")
        payload["drainedKinds"] = sorted(kind.value for kind in self.drained_kinds)
        payload["fullyDrained"] = self.fully_drained
        return payload


def build_drain_evidence(
    path: LegacyPathRecord,
    observations: Iterable[ActiveOwnerObservation],
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_EVIDENCE_MAX_AGE,
) -> DrainEvidence:
    """Aggregate probe observations into fail-closed drain evidence.

    Only a declared dependency kind with a fresh, successful, zero-count
    observation is drained. Observations for kinds the row does not declare are
    ignored rather than widening the result.
    """

    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        raise DrainEvidenceError("now must be timezone-aware")

    latest: dict[ActiveOwnerKind, ActiveOwnerObservation] = {}
    for observation in observations:
        if observation.kind not in path.active_resource_dependencies:
            continue
        current = latest.get(observation.kind)
        if current is None or observation.observed_at > current.observed_at:
            latest[observation.kind] = observation

    drained: set[ActiveOwnerKind] = set()
    blocking: list[ActiveOwnerKind] = []
    missing: list[ActiveOwnerKind] = []
    stale: list[ActiveOwnerKind] = []
    failed: list[ActiveOwnerKind] = []

    for kind in sorted(path.active_resource_dependencies, key=lambda k: k.value):
        observation = latest.get(kind)
        if observation is None:
            missing.append(kind)
            blocking.append(kind)
            continue
        if not observation.probe_succeeded:
            failed.append(kind)
            blocking.append(kind)
            continue
        if generated_at - observation.observed_at > max_age:
            stale.append(kind)
            blocking.append(kind)
            continue
        if observation.active_count > 0:
            blocking.append(kind)
            continue
        drained.add(kind)

    return DrainEvidence(
        pathId=path.path_id,
        generatedAt=generated_at,
        drainedKinds=frozenset(drained),
        blockingKinds=tuple(blocking),
        missingKinds=tuple(missing),
        staleKinds=tuple(stale),
        failedKinds=tuple(failed),
    )


async def collect_drain_evidence(
    path: LegacyPathRecord,
    probes: Iterable[ActiveOwnerProbe],
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_EVIDENCE_MAX_AGE,
) -> DrainEvidence:
    """Run every probe for every declared dependency kind and aggregate.

    A probe that raises is recorded as a failed observation for that kind rather
    than aborting collection, so the resulting evidence names exactly which
    authority could not be proven drained.
    """

    generated_at = now or datetime.now(timezone.utc)
    observations: list[ActiveOwnerObservation] = []
    for probe in probes:
        for kind in sorted(path.active_resource_dependencies, key=lambda k: k.value):
            try:
                observations.append(await probe.observe(path, kind))
            except Exception:  # noqa: BLE001 - a failed probe never drains a kind
                observations.append(
                    ActiveOwnerObservation(
                        kind=kind,
                        activeCount=0,
                        probeRef="probe.unavailable",
                        observedAt=generated_at,
                        probeSucceeded=False,
                    )
                )
    return build_drain_evidence(path, observations, now=generated_at, max_age=max_age)


__all__ = [
    "DEFAULT_EVIDENCE_MAX_AGE",
    "DRAIN_CONTRACT_VERSION",
    "ActiveOwnerObservation",
    "ActiveOwnerProbe",
    "DrainEvidence",
    "DrainEvidenceError",
    "build_drain_evidence",
    "collect_drain_evidence",
]
