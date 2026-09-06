"""Layered N-way concurrency qualification for the generic Omnigent plane.

Source issue: MoonLadderStudios/MoonMind#3885
([Omnigent concurrency] Complete layered N-way qualification with
production-boundary evidence and truthful readiness).

This module owns the *qualification program*, not another scheduler. It answers
one question honestly: **which exact deployment combination has been observed
running which concurrency level, at which layer, and with what evidence?**

Four properties are structural here, and each one exists because the opposite
is the failure this program is meant to catch:

* **A configured ceiling is not a result.** :class:`ObservedOverlapEvidence`
  refuses to record a peak that is not derived from observed per-execution
  start/end samples, so a self-asserted number or an ``asyncio.gather`` of N
  sequential runs cannot be filed as concurrency evidence.
* **A skipped row is not a pass.** :class:`ConcurrencyRowStatus` records
  ``passed``, ``failed``, ``skipped``, ``blocked``, ``unavailable`` and
  ``partial`` distinctly, and only ``passed`` rows raise the validated level.
* **Evidence does not generalize.** The validated level is the highest level
  with a passing row in *every required layer*; an ``N=2`` pass never claims
  ``N=16``, and :class:`ConcurrencySupportIdentity` binds the level to the
  exact substrate, policy versions, worker topology, resource class and
  scenario catalog version that produced it.
* **An unresolved teardown is not a zero-leak scan.** :class:`CleanupScanReport`
  cannot report a clean scan while it carries unresolved entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from moonmind.omnigent.conformance import assert_secret_free
from moonmind.omnigent.settings import is_omnigent_enabled

#: Bumped whenever the required scenario families, their layer requirements, or
#: the owning-test bindings below change. Evidence produced against an older
#: catalog cannot qualify a deployment running a newer one.
CONCURRENCY_SCENARIO_CATALOG_VERSION = "moonmind.omnigent-concurrency-scenarios/v2"

#: Bumped whenever the record schema below changes shape.
CONCURRENCY_QUALIFICATION_RECORD_VERSION = (
    "moonmind.omnigent-concurrency-qualification/v1"
)

#: The hermetic levels the program must keep correct. The exact-Docker layer
#: runs a bounded subset (:data:`EXACT_DOCKER_LEVELS`) because it launches real
#: containers; the protected-live layer runs the bounded provider-safe subset.
HERMETIC_LEVELS: tuple[int, ...] = (1, 2, 4, 8, 16)
EXACT_DOCKER_LEVELS: tuple[int, ...] = (2, 4, 8)
PROTECTED_LIVE_MINIMUM_LEVEL = 2

#: The pytest fixture that provisions an owning test's PostgreSQL cluster. It
#: fails closed rather than skipping when no cluster is reachable
#: (``tests/integration/omnigent/conftest.py``), so a layer owning a test that
#: requests it carries PostgreSQL in its declared environment and the runner's
#: precondition for that layer has to cover it.
POSTGRES_FIXTURE_NAME = "control_plane_postgres_url"

#: The protected release flag that admits the live layer. A refusal to admit is
#: an operator decision rather than a missing environment, so it stays separate
#: from :data:`PROTECTED_LIVE_REQUIRED_ENV`: one records a ``blocked`` row, the
#: other an ``unavailable`` one, and they call for different operator actions.
PROTECTED_LIVE_ADMISSION_ENV = "MOONMIND_OMNIGENT_PROTECTED_LIVE_CONCURRENCY"

#: The credentialless provider route the protected-live layer runs against.
PROTECTED_LIVE_PROVIDER_PROFILE_ENV = "MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID"

#: Everything the protected-live owning test reads before it opens a single
#: session. The runner checks this same tuple before it enters the layer and
#: the scheduled job's env block is asserted against it, so the job, the
#: precondition and the owning test cannot drift into a layer that is admitted
#: and then fails on its own configuration.
PROTECTED_LIVE_REQUIRED_ENV: tuple[str, ...] = (
    "OMNIGENT_ENABLED",
    "OMNIGENT_SERVER_URL",
    "OMNIGENT_API_TOKEN",
    "OMNIGENT_DEFAULT_AGENT_NAME",
)

#: One published casing for every model in this record. The document is read by
#: CI jobs and operators alongside the protected support index, so it uses the
#: same camelCase field names rather than mixing two conventions in one file.
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    populate_by_name=True,
    alias_generator=to_camel,
)

#: Bounded diagnostics keep failure rows actionable without turning the record
#: into a log sink.
MAX_DIAGNOSTIC_ENTRIES = 8
MAX_DIAGNOSTIC_LENGTH = 512

#: The two names the qualification runner exports to a layer's owning tests:
#: the level under test, and the directory that level's observation is
#: published to. :func:`publish_observed_overlap` and
#: :func:`load_observed_overlap` are the only reader/writer pair for that
#: directory, so a layer cannot invent an evidence filename the runner will
#: never find — which is exactly how a layer ends up permanently ``partial``.
CONCURRENCY_LEVEL_ENV = "MOONMIND_OMNIGENT_CONCURRENCY_LEVEL"
CONCURRENCY_EVIDENCE_DIR_ENV = "MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR"


class ConcurrencyQualificationLayer(StrEnum):
    """The three layers of the required test program."""

    #: Real schemas, planning, realizer, leases and stores over controlled
    #: provider/Docker boundaries.
    hermetic = "hermetic"
    #: The exact built MoonMind, Omnigent server and host artifacts under real
    #: Docker, on a declared machine resource class.
    exact_docker = "exact_docker"
    #: The exact eligible credentialless provider route under a bounded
    #: pricing/privacy/load policy.
    protected_live = "protected_live"


#: Layers that must carry a passing row before a level is validated. A hermetic
#: pass alone proves the coordinator, not the deployed artifacts.
REQUIRED_QUALIFICATION_LAYERS: frozenset[ConcurrencyQualificationLayer] = frozenset(
    {
        ConcurrencyQualificationLayer.hermetic,
        ConcurrencyQualificationLayer.exact_docker,
    }
)


class ConcurrencyScenarioFamily(StrEnum):
    """The required scenario families every layer program must own."""

    isolation_and_completion = "isolation_and_completion"
    queue_and_dynamic_limits = "queue_and_dynamic_limits"
    validation_maintenance_backpressure = "validation_maintenance_backpressure"
    host_and_transport_pressure = "host_and_transport_pressure"
    failure_at_authority_handoffs = "failure_at_authority_handoffs"
    recovery_and_release = "recovery_and_release"
    product_and_readability = "product_and_readability"


class ConcurrencyRowStatus(StrEnum):
    """Distinct outcomes for one qualification row.

    ``skipped``, ``blocked`` and ``unavailable`` are deliberately separate: an
    intentionally deselected row, a policy/authorization refusal and a missing
    environment are three different operator actions, and none of them is a
    pass.
    """

    passed = "passed"
    failed = "failed"
    #: Deselected by the risk-based matrix for this run.
    skipped = "skipped"
    #: Refused by a policy, authorization or release gate.
    blocked = "blocked"
    #: The environment (images, Docker daemon, provider route) was absent.
    unavailable = "unavailable"
    #: Executed, but only part of the family completed.
    partial = "partial"


#: Only this status raises the validated concurrency level.
PASSING_ROW_STATUSES: frozenset[ConcurrencyRowStatus] = frozenset(
    {ConcurrencyRowStatus.passed}
)


class ScenarioOwner(BaseModel):
    """One owning test for one scenario family at one layer."""

    model_config = _MODEL_CONFIG

    family: ConcurrencyScenarioFamily
    layer: ConcurrencyQualificationLayer
    #: pytest node id (or tool entrypoint) that actually executes the family.
    owning_test: str = Field(min_length=1, max_length=512)
    #: Sibling escaped regressions this owner replays, as issue refs.
    escaped_regressions: tuple[str, ...] = ()
    #: ``True`` when this owner runs in required PR CI rather than a scheduled
    #: or protected program.
    required_in_ci: bool = False

    @model_validator(mode="after")
    def validate_owner(self) -> "ScenarioOwner":
        if self.required_in_ci and self.layer is not ConcurrencyQualificationLayer.hermetic:
            raise ValueError(
                "only hermetic owners may be required in pull-request CI"
            )
        return self


def _owner(
    family: ConcurrencyScenarioFamily,
    layer: ConcurrencyQualificationLayer,
    owning_test: str,
    *,
    escaped_regressions: Sequence[str] = (),
    required_in_ci: bool = False,
) -> ScenarioOwner:
    return ScenarioOwner(
        family=family,
        layer=layer,
        owning_test=owning_test,
        escaped_regressions=tuple(escaped_regressions),
        required_in_ci=required_in_ci,
    )


_F = ConcurrencyScenarioFamily
_L = ConcurrencyQualificationLayer

#: The risk-based scenario/layer matrix. Every required family names the test
#: that actually executes it, so a family cannot quietly lose its owner: the
#: catalog conformance test resolves each ``owning_test`` against the
#: repository.
CONCURRENCY_SCENARIO_CATALOG: tuple[ScenarioOwner, ...] = (
    # 1. Isolation and ordinary completion.
    _owner(
        _F.isolation_and_completion,
        _L.hermetic,
        "tests/unit/omnigent/test_generic_plane_n_way_concurrency.py",
        required_in_ci=True,
    ),
    # The authoring boundary the realizer journey starts *after*: N
    # simultaneous submissions compiled into N immutable plans that each
    # select the generic Omnigent combination.
    _owner(
        _F.isolation_and_completion,
        _L.hermetic,
        "tests/unit/omnigent/test_generic_plane_production_boundary_concurrency.py",
        required_in_ci=True,
    ),
    _owner(
        _F.isolation_and_completion,
        _L.exact_docker,
        "tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
    ),
    # 2. Queue and dynamic limits.
    _owner(
        _F.queue_and_dynamic_limits,
        _L.hermetic,
        "tests/unit/workflows/temporal/test_omnigent_capacity_matrix.py",
        required_in_ci=True,
    ),
    # The manager ledger alone is not the dispatch boundary: this owner drives
    # N+2 submissions through ``MoonMindAgentRun`` so admission, durable
    # waiting, grant-on-release and queued cancellation are exercised where the
    # workflow actually performs them.
    _owner(
        _F.queue_and_dynamic_limits,
        _L.hermetic,
        "tests/unit/omnigent/test_generic_plane_production_boundary_concurrency.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3880",),
        required_in_ci=True,
    ),
    _owner(
        _F.queue_and_dynamic_limits,
        _L.exact_docker,
        "tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
    ),
    # 3. Validation, maintenance and provider backpressure.
    _owner(
        _F.validation_maintenance_backpressure,
        _L.hermetic,
        "tests/unit/workflows/temporal/test_omnigent_capacity_matrix.py",
        escaped_regressions=(
            "MoonLadderStudios/MoonMind#3879",
            "MoonLadderStudios/MoonMind#3882",
        ),
        required_in_ci=True,
    ),
    _owner(
        _F.validation_maintenance_backpressure,
        _L.exact_docker,
        "tests/integration/omnigent/test_provider_lease_incremental_contract_postgres.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3883",),
    ),
    # 4. Host and transport pressure.
    _owner(
        _F.host_and_transport_pressure,
        _L.hermetic,
        "tests/unit/omnigent/test_generic_plane_n_way_concurrency.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3884",),
        required_in_ci=True,
    ),
    # The hermetic layer's declared environment includes real database
    # constraints. The final-slot race is decided by two *independent*
    # PostgreSQL transactions racing the same budget, which an in-memory
    # ledger cannot reproduce: this owner holds one transaction open between
    # the count and the commit and proves the second is serialized behind it.
    _owner(
        _F.host_and_transport_pressure,
        _L.hermetic,
        "tests/integration/omnigent/test_machine_capacity_reservations_postgres.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3881",),
    ),
    _owner(
        _F.host_and_transport_pressure,
        _L.exact_docker,
        "tests/integration/omnigent/test_machine_capacity_reservations_postgres.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3881",),
    ),
    # 5. Failure at authority handoffs.
    _owner(
        _F.failure_at_authority_handoffs,
        _L.hermetic,
        "tests/unit/omnigent/test_generic_plane_n_way_concurrency.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3880",),
        required_in_ci=True,
    ),
    _owner(
        _F.failure_at_authority_handoffs,
        _L.exact_docker,
        "tests/integration/omnigent/test_capacity_admission_fence_postgres.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3883",),
    ),
    # 6. Recovery and release.
    _owner(
        _F.recovery_and_release,
        _L.hermetic,
        "tests/unit/workflows/temporal/test_provider_profile_lease_durability.py",
        escaped_regressions=("MoonLadderStudios/MoonMind#3883",),
        required_in_ci=True,
    ),
    _owner(
        _F.recovery_and_release,
        _L.exact_docker,
        "tests/integration/omnigent/test_embedded_recovery.py",
    ),
    # 7. Product and readability.
    _owner(
        _F.product_and_readability,
        _L.hermetic,
        "tests/unit/omnigent/test_control_plane_readiness.py",
        required_in_ci=True,
    ),
    _owner(
        _F.product_and_readability,
        _L.exact_docker,
        "tests/integration/omnigent/test_control_plane_postgres.py",
    ),
    # Protected-live owns the credentialless OpenCode Zen route only. Its owner
    # fails rather than skips without admission and credentials, so the runner
    # can tell "the route refused us" from "we never asked".
    _owner(
        _F.isolation_and_completion,
        _L.protected_live,
        "tests/provider/omnigent/test_omnigent_concurrency.py",
    ),
)


def scenario_owners(
    *,
    layer: ConcurrencyQualificationLayer | None = None,
    family: ConcurrencyScenarioFamily | None = None,
) -> tuple[ScenarioOwner, ...]:
    """Return the catalog entries matching an optional layer/family filter."""

    return tuple(
        entry
        for entry in CONCURRENCY_SCENARIO_CATALOG
        if (layer is None or entry.layer is layer)
        and (family is None or entry.family is family)
    )


#: The checkout this module was loaded from. Catalog owning tests are paths
#: relative to it, so the runner resolves the same files whatever directory it
#: was invoked from.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def owning_test_files(
    layer: ConcurrencyQualificationLayer, *, root: Path | None = None
) -> tuple[Path, ...]:
    """Return the distinct files that execute ``layer``'s owning tests.

    This is the one resolution of a layer's owners into paths: the runner
    spawns exactly these files, and :func:`layer_requires_postgres` reads
    exactly these files to decide what environment the layer needs. A file the
    catalog names but the checkout does not carry is dropped here, so a caller
    never has to distinguish "no owner" from "an owner that cannot be read".
    """

    base = REPOSITORY_ROOT if root is None else Path(root)
    named = sorted(
        {owner.owning_test.split("::")[0] for owner in scenario_owners(layer=layer)}
    )
    return tuple(path for path in (base / name for name in named) if path.is_file())


def layer_requires_postgres(
    layer: ConcurrencyQualificationLayer, *, root: Path | None = None
) -> bool:
    """Return whether any of ``layer``'s owners needs a PostgreSQL cluster.

    Derived from the catalog instead of listed per layer. An owner brings its
    environment with it, so a PostgreSQL-dependent test added to a layer cannot
    outrun that layer's precondition and turn a missing cluster into a row that
    reads like a concurrency defect.
    """

    for path in owning_test_files(layer, root=root):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable owner is not a claim
            continue
        if POSTGRES_FIXTURE_NAME in source:
            return True
    return False


def unsatisfied_protected_live_environment(
    env: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return the protected-live variables that are absent or refuse the route.

    Both the qualification runner's precondition and the owning test read this
    one answer, so the layer is never entered on an environment its own owner
    will reject. A value that is present but turns Omnigent off is reported the
    same way an absent one is — either way no session can be opened, and naming
    the variable is what makes the row actionable — but only once everything
    else is set, so an absent server URL is not reported as a disabled gate.
    """

    source: Mapping[str, Any] = os.environ if env is None else env
    missing = [
        name
        for name in PROTECTED_LIVE_REQUIRED_ENV
        if not str(source.get(name) or "").strip()
    ]
    if not missing and not is_omnigent_enabled(env=source):
        missing.append("OMNIGENT_ENABLED")
    return tuple(sorted(missing))


def unowned_scenarios() -> tuple[tuple[ConcurrencyScenarioFamily, ConcurrencyQualificationLayer], ...]:
    """Return required family/layer pairs with no owning test.

    A non-empty result is a program gap, not a passing configuration.
    """

    owned = {(entry.family, entry.layer) for entry in CONCURRENCY_SCENARIO_CATALOG}
    return tuple(
        (family, layer)
        for family in ConcurrencyScenarioFamily
        for layer in sorted(REQUIRED_QUALIFICATION_LAYERS)
        if (family, layer) not in owned
    )


class ExecutionOverlapSample(BaseModel):
    """One observed execution window, in monotonic seconds from run start."""

    model_config = _MODEL_CONFIG

    execution_ref: str = Field(min_length=1, max_length=255)
    started_at: float = Field(ge=0.0)
    ended_at: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_window(self) -> "ExecutionOverlapSample":
        if self.ended_at < self.started_at:
            raise ValueError("an execution cannot end before it starts")
        return self


def observed_peak_overlap(samples: Iterable[ExecutionOverlapSample]) -> int:
    """Return the maximum number of simultaneously open execution windows.

    This is a sweep over observed start/end evidence, so N executions that
    merely *ran* returns 1 while N executions that genuinely overlapped
    returns N. It is the only accepted source of a peak in
    :class:`ObservedOverlapEvidence`.
    """

    events: list[tuple[float, int]] = []
    for sample in samples:
        # Ends are processed before starts at the same instant so two adjacent,
        # non-overlapping windows never read as overlap.
        events.append((sample.started_at, 1))
        events.append((sample.ended_at, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    peak = 0
    for _instant, delta in events:
        active += delta
        peak = max(peak, active)
    return peak


class ObservedOverlapEvidence(BaseModel):
    """Proof that useful execution actually overlapped at the claimed level."""

    model_config = _MODEL_CONFIG

    requested_level: int = Field(ge=1)
    #: The effective limit in force. When every configured limit permits the
    #: requested level this equals ``requested_level``; under a lower limit the
    #: expected peak is this value and the remainder must be durable waiters.
    effective_limit: int = Field(ge=1)
    #: Every admitted execution released its barrier before any completed, so
    #: the peak below is overlap and not a scheduling coincidence.
    barrier_synchronized: bool
    samples: tuple[ExecutionOverlapSample, ...]
    durable_waiters: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_observation(self) -> "ObservedOverlapEvidence":
        if not self.samples:
            raise ValueError(
                "observed overlap requires per-execution start/end evidence; a "
                "configured value or self-asserted peak is not an observation"
            )
        if len({sample.execution_ref for sample in self.samples}) != len(self.samples):
            raise ValueError("overlap samples must name distinct executions")
        if not self.barrier_synchronized:
            raise ValueError(
                "overlap must be proven with barriers or controlled holds, not "
                "concurrent scheduling alone"
            )
        expected_peak = min(self.requested_level, self.effective_limit)
        if self.observed_peak != expected_peak:
            raise ValueError(
                "observed peak concurrency "
                f"{self.observed_peak} does not match the effective limit "
                f"{expected_peak}"
            )
        if self.requested_level > self.effective_limit:
            expected_waiters = self.requested_level - self.effective_limit
            if self.durable_waiters != expected_waiters:
                raise ValueError(
                    "work above the effective limit must be observed as durable "
                    f"waiters ({expected_waiters} expected, "
                    f"{self.durable_waiters} observed)"
                )
        elif self.durable_waiters:
            raise ValueError(
                "no durable waiters are expected when the effective limit "
                "admits the requested level"
            )
        return self

    @property
    def observed_peak(self) -> int:
        return observed_peak_overlap(self.samples)


#: A resource-class ref may name the machine it stands for as
#: ``<cpuCores>x<memoryGib>`` — ``ci-standard-4x8@1``. When it does, the
#: *measured* machine has to be the machine the ref names. A row that names a
#: 4x8 class while the wave ran on a sixteen-core runner describes substrate
#: that never ran the level, and the class is the only thing that makes that
#: row's thresholds or a level exception mean anything.
_RESOURCE_CLASS_DIMENSIONS = re.compile(r"(?<![0-9A-Za-z])(\d+)x(\d+)(?![0-9A-Za-z])")

#: No machine measures the size its class names. The kernel reserves firmware,
#: memmap and crashkernel pages before ``MemTotal`` is computed, so a nominal
#: 8-GiB VM measures about 7.8 GiB and a 16-GiB CI runner about 15.6 GiB.
#: Comparing whole floored GiB against the ref therefore refused every real
#: machine — the documented ``ci-standard-4x8@1`` could not be published by any
#: honest 8-GiB host. The agreement is a band around the nominal size instead: a
#: measurement may land up to this fraction below the size its class names and
#: may never exceed it. Ten percent covers every reservation a Linux host takes
#: while staying far narrower than the gap to the next smaller whole-GiB class,
#: so a genuine 7-GiB machine (~6.8 GiB measured) is still refused a 4x8 class.
MACHINE_MEMORY_TOLERANCE = 0.10


def nominal_memory_band_mib(nominal_gib: int) -> tuple[int, int]:
    """Return the measured-memory band, in MiB, a ``nominal_gib`` class accepts.

    The ceiling is the nominal size exactly: a machine with *more* memory than
    its class names is refused too, because thresholds calibrated for the
    smaller class would pass on headroom the class does not describe.
    """

    nominal_mib = nominal_gib * 1024
    return int(nominal_mib * (1.0 - MACHINE_MEMORY_TOLERANCE)), nominal_mib


class MachineResourceClass(BaseModel):
    """The machine the exact-Docker layer ran on, as it was measured.

    Performance thresholds and any level exception are only meaningful against
    a named class, so the class travels with the row. ``cpu_cores`` and
    ``memory_mib`` are measurements of the machine that ran the level — the
    runner has no way to declare them — and a ref that names its own dimensions
    is a claim that measurement has to satisfy. Memory is carried in MiB
    because whole GiB cannot express the difference between a nominal 8-GiB
    machine and a genuine 7-GiB one.
    """

    model_config = _MODEL_CONFIG

    resource_class_ref: str = Field(min_length=1, max_length=128)
    cpu_cores: int = Field(ge=1)
    memory_mib: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_resource_class(self) -> "MachineResourceClass":
        named = _RESOURCE_CLASS_DIMENSIONS.search(self.resource_class_ref)
        if named is None:
            return self
        cores, nominal_gib = int(named.group(1)), int(named.group(2))
        floor_mib, ceiling_mib = nominal_memory_band_mib(nominal_gib)
        if self.cpu_cores != cores or not floor_mib <= self.memory_mib <= ceiling_mib:
            raise ValueError(
                f"resource class {self.resource_class_ref!r} names a "
                f"{cores}-core/{nominal_gib}-GiB machine, but the measured "
                f"machine has {self.cpu_cores} cores and {self.memory_mib} MiB; "
                f"a {nominal_gib}-GiB machine measures {floor_mib}-{ceiling_mib} MiB"
            )
        return self


class ConcurrencyQualificationRow(BaseModel):
    """One (layer, level) outcome with its own resolvable evidence."""

    model_config = _MODEL_CONFIG

    layer: ConcurrencyQualificationLayer
    level: int = Field(ge=1)
    status: ConcurrencyRowStatus
    #: Required for a passing row; forbidden otherwise, because an outcome that
    #: did not run cannot carry an observation.
    overlap: ObservedOverlapEvidence | None = None
    evidence_ref: str = Field(default="", max_length=512)
    evidence_digest: str = Field(default="")
    resource_class: MachineResourceClass | None = None
    #: An explicit, named policy that lowers the required exact-Docker level.
    #: Absent, a missing required level is simply not qualified.
    resource_class_exception_ref: str = Field(default="", max_length=255)
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_row(self) -> "ConcurrencyQualificationRow":
        if self.evidence_digest and not self.evidence_digest.startswith("sha256:"):
            raise ValueError("row evidence digest must be a sha256 digest")
        if self.status is ConcurrencyRowStatus.passed:
            if self.overlap is None:
                raise ValueError(
                    "a passing concurrency row requires observed overlap evidence"
                )
            if self.overlap.requested_level != self.level:
                raise ValueError(
                    "observed overlap evidence belongs to a different level"
                )
            if self.overlap.effective_limit < self.level:
                raise ValueError(
                    "a passing row cannot observe fewer active consumers than "
                    "its claimed level"
                )
            if not (self.evidence_ref and self.evidence_digest):
                raise ValueError(
                    "a passing concurrency row requires independently "
                    "resolvable evidence"
                )
        elif self.overlap is not None:
            raise ValueError(
                "only a passing row may carry observed overlap evidence"
            )
        if (
            self.layer is ConcurrencyQualificationLayer.exact_docker
            and self.status is ConcurrencyRowStatus.passed
            and self.resource_class is None
        ):
            raise ValueError(
                "exact-Docker rows must declare the machine resource class"
            )
        if len(self.diagnostics) > MAX_DIAGNOSTIC_ENTRIES:
            raise ValueError("row diagnostics exceed the bounded entry limit")
        for entry in self.diagnostics:
            if len(entry) > MAX_DIAGNOSTIC_LENGTH:
                raise ValueError("row diagnostics exceed the bounded length limit")
        assert_secret_free(self.model_dump(mode="json", by_alias=True))
        return self

    @property
    def qualifies(self) -> bool:
        return self.status in PASSING_ROW_STATUSES


class ConcurrencySupportIdentity(BaseModel):
    """The concurrency dimension of an exact support combination.

    Bound to the substrate identity by ``supportCombinationKey``: a level
    validated for one harness, image, architecture, host mode, materializer or
    policy version says nothing about another.
    """

    model_config = _MODEL_CONFIG

    support_combination_key: str = Field(min_length=1, max_length=255)
    moonmind_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    worker_build_ref: str = Field(min_length=1, max_length=255)
    provider_capacity_policy_version: str = Field(min_length=1, max_length=128)
    host_capacity_policy_version: str = Field(min_length=1, max_length=128)
    transport_pool_policy_version: str = Field(min_length=1, max_length=128)
    worker_topology_ref: str = Field(min_length=1, max_length=255)
    resource_class: MachineResourceClass
    scenario_catalog_version: Literal[CONCURRENCY_SCENARIO_CATALOG_VERSION] = (
        CONCURRENCY_SCENARIO_CATALOG_VERSION
    )


class ConcurrencyQualificationRecord(BaseModel):
    """The versioned concurrency support record for one exact combination."""

    model_config = _MODEL_CONFIG

    schema_version: str = CONCURRENCY_QUALIFICATION_RECORD_VERSION
    identity: ConcurrencySupportIdentity
    generated_at: datetime
    rows: tuple[ConcurrencyQualificationRow, ...]

    @model_validator(mode="after")
    def validate_record(self) -> "ConcurrencyQualificationRecord":
        if self.schema_version != CONCURRENCY_QUALIFICATION_RECORD_VERSION:
            raise ValueError("unsupported concurrency qualification schema version")
        if self.generated_at.tzinfo is None:
            raise ValueError("concurrency qualification timestamps require timezones")
        seen: set[tuple[ConcurrencyQualificationLayer, int]] = set()
        for row in self.rows:
            key = (row.layer, row.level)
            if key in seen:
                raise ValueError(
                    "a layer/level pair may hold only one outcome; two rows for "
                    "the same pair hide a failure behind a pass"
                )
            seen.add(key)
        if self.identity.scenario_catalog_version != CONCURRENCY_SCENARIO_CATALOG_VERSION:
            raise ValueError(
                "concurrency evidence was produced against a different scenario "
                "catalog version"
            )
        assert_secret_free(self.model_dump(mode="json", by_alias=True))
        return self

    def row(
        self, layer: ConcurrencyQualificationLayer, level: int
    ) -> ConcurrencyQualificationRow | None:
        for entry in self.rows:
            if entry.layer is layer and entry.level == level:
                return entry
        return None

    @property
    def validated_concurrency_level(self) -> int:
        """Return the highest level with a passing row in every required layer.

        A level is validated only when it is *itself* observed: a passing
        ``N=8`` row does not retroactively validate ``N=16``, and a passing
        hermetic row alone never validates any level, because the deployed
        artifacts were not exercised.
        """

        levels = sorted(
            {
                row.level
                for row in self.rows
                if row.layer in REQUIRED_QUALIFICATION_LAYERS
            }
        )
        validated = 0
        for level in levels:
            rows = [
                self.row(layer, level) for layer in REQUIRED_QUALIFICATION_LAYERS
            ]
            if any(row is None or not row.qualifies for row in rows):
                continue
            validated = max(validated, level)
        return validated

    @property
    def unqualified_rows(self) -> tuple[ConcurrencyQualificationRow, ...]:
        """Return every recorded row that is not a pass, in record order."""

        return tuple(row for row in self.rows if not row.qualifies)

    def advertised_concurrency_level(self, operator_ceiling: int | None = None) -> int:
        """Return the level this deployment may advertise.

        Readiness may advertise the validated level or a lower operator
        ceiling, never more. This function never rewrites the configured
        ceiling; it only reports the smaller of the two.
        """

        validated = self.validated_concurrency_level
        if operator_ceiling is None:
            return validated
        if operator_ceiling < 0:
            raise ValueError("an operator concurrency ceiling cannot be negative")
        return min(validated, operator_ceiling)

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class CleanupScanEntry(BaseModel):
    """One resource observed by a post-run cleanup scan."""

    model_config = _MODEL_CONFIG

    resource_ref: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64)
    #: ``True`` once the owning cleanup authority proved teardown. ``False``
    #: means the resource is still present or its teardown is unproven.
    resolved: bool
    #: A resource MoonMind does not own (unlabeled, foreign, or a
    #: profile-owned credential home) is reported and never deleted.
    foreign: bool = False


class CleanupScanReport(BaseModel):
    """An honest post-concurrency teardown scan.

    ``zero_leak`` is derived, never asserted: a scan that carries unresolved
    MoonMind-owned entries cannot report a clean sweep, so an unresolved
    teardown is visible instead of counted as a pass.
    """

    model_config = _MODEL_CONFIG

    scanned_at: datetime
    entries: tuple[CleanupScanEntry, ...] = ()

    @model_validator(mode="after")
    def validate_report(self) -> "CleanupScanReport":
        if self.scanned_at.tzinfo is None:
            raise ValueError("cleanup scan timestamps require timezones")
        for entry in self.entries:
            if entry.foreign and entry.resolved:
                raise ValueError(
                    "a foreign or profile-owned resource is never torn down by "
                    "this authority and cannot be reported as resolved"
                )
        return self

    @property
    def unresolved(self) -> tuple[CleanupScanEntry, ...]:
        return tuple(
            entry for entry in self.entries if not entry.resolved and not entry.foreign
        )

    @property
    def foreign_preserved(self) -> tuple[CleanupScanEntry, ...]:
        return tuple(entry for entry in self.entries if entry.foreign)

    @property
    def zero_leak(self) -> bool:
        return not self.unresolved

    def as_payload(self) -> dict[str, Any]:
        return {
            "scannedAt": self.scanned_at.astimezone(UTC).isoformat(),
            "zeroLeak": self.zero_leak,
            "unresolved": [entry.resource_ref for entry in self.unresolved],
            "foreignPreserved": [
                entry.resource_ref for entry in self.foreign_preserved
            ],
        }


class WaveObservation(BaseModel):
    """Measured cost of one bounded concurrency wave."""

    model_config = _MODEL_CONFIG

    wave_index: int = Field(ge=0)
    observed_peak: int = Field(ge=0)
    #: Control-plane latencies in seconds, excluding provider round-trips so a
    #: deterministic local budget does not measure the network.
    wait_seconds: float = Field(ge=0.0)
    launch_seconds: float = Field(ge=0.0)
    registration_seconds: float = Field(ge=0.0)
    control_seconds: float = Field(ge=0.0)
    cleanup_seconds: float = Field(ge=0.0)
    lease_mutations: int = Field(ge=0)
    registration_requests: int = Field(ge=0)
    transport_pool_peak: int = Field(ge=0)
    residual_resources: int = Field(ge=0)


class RepeatedWaveThresholds(BaseModel):
    """Per-resource-class budgets for the repeated-wave program."""

    model_config = _MODEL_CONFIG

    resource_class_ref: str = Field(min_length=1, max_length=128)
    #: One control-plane budget, applied to *every* control latency a wave
    #: reports. A single stalled phase — a wait that never gets scheduled, a
    #: teardown that hangs — breaches it on its own, so a wave cannot hide a
    #: stalled control operation inside an otherwise fast total.
    max_control_seconds: float = Field(gt=0.0)
    max_lease_mutations_per_execution: int = Field(ge=1)
    max_registration_requests_per_execution: int = Field(ge=1)
    max_transport_pool_peak: int = Field(ge=1)


#: The deterministic local budget. Provider latency is excluded by construction:
#: :class:`WaveObservation` only carries control-plane timings.
DEFAULT_REPEATED_WAVE_THRESHOLDS = RepeatedWaveThresholds(
    resource_class_ref="local-deterministic@1",
    max_control_seconds=30.0,
    max_lease_mutations_per_execution=8,
    max_registration_requests_per_execution=2,
    max_transport_pool_peak=32,
)

#: The exact-image budget for the documented CI machine class. Real containers
#: on a real daemon are slower than the deterministic substrate, so the control
#: budget is wider. The per-execution mutation, registration and transport-pool
#: budgets are identical, because those count control-plane work per execution
#: and must not grow just because the machine did.
EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS = RepeatedWaveThresholds(
    resource_class_ref="ci-standard-4x8@1",
    max_control_seconds=300.0,
    max_lease_mutations_per_execution=8,
    max_registration_requests_per_execution=2,
    max_transport_pool_peak=32,
)

#: Every declared budget, keyed by the resource class it binds to. A wave
#: program resolves its budget from the class it actually ran on, so the
#: deterministic budget can never be borrowed to pass an exact-image wave and
#: an exact-image budget can never loosen the deterministic one.
REPEATED_WAVE_THRESHOLDS: Mapping[str, RepeatedWaveThresholds] = {
    thresholds.resource_class_ref: thresholds
    for thresholds in (
        DEFAULT_REPEATED_WAVE_THRESHOLDS,
        EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS,
    )
}


def repeated_wave_thresholds(resource_class_ref: str) -> RepeatedWaveThresholds:
    """Return the declared repeated-wave budget for one resource class.

    An undeclared class is refused rather than silently given the deterministic
    budget: thresholds are only meaningful against the machine that ran the
    wave, so a class nobody budgeted has no verdict to offer.
    """

    try:
        return REPEATED_WAVE_THRESHOLDS[resource_class_ref]
    except KeyError:
        raise ValueError(
            "no repeated-wave budget is declared for resource class "
            f"{resource_class_ref!r}; declared classes: "
            + ", ".join(sorted(REPEATED_WAVE_THRESHOLDS))
        ) from None


class RepeatedWaveReport(BaseModel):
    """Bounded-growth verdict across repeated waves at one level."""

    model_config = _MODEL_CONFIG

    level: int = Field(ge=1)
    thresholds: RepeatedWaveThresholds
    waves: tuple[WaveObservation, ...]

    @model_validator(mode="after")
    def validate_waves(self) -> "RepeatedWaveReport":
        if len(self.waves) < 2:
            raise ValueError(
                "bounded growth needs at least two waves to compare against"
            )
        return self

    @property
    def violations(self) -> tuple[str, ...]:
        """Return every threshold or growth breach, most specific first.

        The checks are deliberately absolute *and* comparative: a per-wave
        budget catches a slow allocator, while the residual and mutation
        comparisons catch a leak that stays under budget on any single wave but
        grows wave over wave.
        """

        found: list[str] = []
        first = self.waves[0]
        for wave in self.waves:
            label = f"wave {wave.wave_index}"
            if wave.observed_peak < self.level:
                found.append(
                    f"{label}: observed peak {wave.observed_peak} below level "
                    f"{self.level}"
                )
            for phase, seconds in (
                ("wait", wave.wait_seconds),
                ("launch", wave.launch_seconds),
                ("registration", wave.registration_seconds),
                ("control", wave.control_seconds),
                ("cleanup", wave.cleanup_seconds),
            ):
                if seconds > self.thresholds.max_control_seconds:
                    found.append(
                        f"{label}: {phase} control latency {seconds}s exceeds "
                        f"{self.thresholds.max_control_seconds}s"
                    )
            if wave.lease_mutations > (
                self.thresholds.max_lease_mutations_per_execution * self.level
            ):
                found.append(
                    f"{label}: {wave.lease_mutations} lease mutations exceed the "
                    "per-execution budget"
                )
            if wave.registration_requests > (
                self.thresholds.max_registration_requests_per_execution * self.level
            ):
                found.append(
                    f"{label}: {wave.registration_requests} registration requests "
                    "exceed the per-execution budget"
                )
            if wave.transport_pool_peak > self.thresholds.max_transport_pool_peak:
                found.append(
                    f"{label}: transport pool peak {wave.transport_pool_peak} "
                    "exceeds the pool budget"
                )
            if wave.residual_resources:
                found.append(
                    f"{label}: {wave.residual_resources} residual resources "
                    "remained after teardown"
                )
            if wave.lease_mutations > first.lease_mutations:
                found.append(
                    f"{label}: per-lease database mutations grew from "
                    f"{first.lease_mutations} to {wave.lease_mutations}"
                )
            if wave.registration_requests > first.registration_requests:
                found.append(
                    f"{label}: registration requests grew from "
                    f"{first.registration_requests} to "
                    f"{wave.registration_requests}"
                )
        return tuple(found)

    @property
    def bounded(self) -> bool:
        return not self.violations


def build_row_for_unavailable_environment(
    *,
    layer: ConcurrencyQualificationLayer,
    level: int,
    reason: str,
) -> ConcurrencyQualificationRow:
    """Return the honest row for a layer whose environment was absent.

    A runner that cannot reach real Docker or the live provider route emits
    this instead of skipping quietly, so the missing level is visible in the
    record and never raises the validated level.
    """

    return ConcurrencyQualificationRow(
        layer=layer,
        level=level,
        status=ConcurrencyRowStatus.unavailable,
        diagnostics=(reason[:MAX_DIAGNOSTIC_LENGTH],),
    )


def compute_concurrency_evidence_digest(payload: Mapping[str, Any]) -> str:
    """Return the canonical digest of one concurrency evidence document."""

    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def requested_concurrency_level(*, default: int) -> int:
    """Return the level the runner selected for this layer invocation.

    A layer's owning tests are executed once per level, so the level arrives
    through the environment rather than through pytest parametrization. An
    unparsable value fails rather than silently collapsing to the default,
    because a mistyped level would otherwise publish evidence for a level
    nobody asked for.
    """

    raw = os.environ.get(CONCURRENCY_LEVEL_ENV, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(
            f"{CONCURRENCY_LEVEL_ENV} must be a positive integer, got {raw!r}"
        )
    return int(raw)


def observed_overlap_evidence_path(
    evidence_dir: str | Path,
    layer: ConcurrencyQualificationLayer,
    level: int,
) -> Path:
    """Return the one path a layer's observation for ``level`` lives at."""

    return Path(evidence_dir) / f"{layer.value}-{level}.json"


def publish_observed_overlap(
    layer: ConcurrencyQualificationLayer,
    overlap: ObservedOverlapEvidence,
    *,
    evidence_dir: str | Path | None = None,
) -> Path | None:
    """Publish one layer's observation where the runner reads it.

    Returns ``None`` when no evidence directory was requested, which is the
    ordinary case for a developer running the owning test directly. The file is
    keyed by the observation's own ``requested_level``, so an observation can
    never be filed under a level it did not measure.
    """

    directory = str(
        evidence_dir
        if evidence_dir is not None
        else os.environ.get(CONCURRENCY_EVIDENCE_DIR_ENV, "")
    ).strip()
    if not directory:
        return None
    target = observed_overlap_evidence_path(
        directory, layer, overlap.requested_level
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            overlap.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def load_observed_overlap(
    evidence_dir: str | Path,
    layer: ConcurrencyQualificationLayer,
    level: int,
) -> ObservedOverlapEvidence | None:
    """Return the published observation for ``(layer, level)``, if any.

    ``None`` means the owning tests passed without observing anything, which
    the runner records as ``partial`` rather than as a pass. Evidence that
    measured a different level is refused outright: a stale file from an
    earlier level would otherwise qualify a level that never ran.
    """

    path = observed_overlap_evidence_path(evidence_dir, layer, level)
    if not path.exists():
        return None
    overlap = ObservedOverlapEvidence.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if overlap.requested_level != level:
        raise ValueError(
            f"{path.name} observed level {overlap.requested_level}, not {level}"
        )
    return overlap


__all__ = [
    "CONCURRENCY_EVIDENCE_DIR_ENV",
    "CONCURRENCY_LEVEL_ENV",
    "CONCURRENCY_QUALIFICATION_RECORD_VERSION",
    "CONCURRENCY_SCENARIO_CATALOG",
    "CONCURRENCY_SCENARIO_CATALOG_VERSION",
    "DEFAULT_REPEATED_WAVE_THRESHOLDS",
    "EXACT_DOCKER_LEVELS",
    "EXACT_DOCKER_REPEATED_WAVE_THRESHOLDS",
    "HERMETIC_LEVELS",
    "MACHINE_MEMORY_TOLERANCE",
    "MAX_DIAGNOSTIC_ENTRIES",
    "MAX_DIAGNOSTIC_LENGTH",
    "PASSING_ROW_STATUSES",
    "POSTGRES_FIXTURE_NAME",
    "PROTECTED_LIVE_ADMISSION_ENV",
    "PROTECTED_LIVE_MINIMUM_LEVEL",
    "PROTECTED_LIVE_PROVIDER_PROFILE_ENV",
    "PROTECTED_LIVE_REQUIRED_ENV",
    "REPEATED_WAVE_THRESHOLDS",
    "REPOSITORY_ROOT",
    "REQUIRED_QUALIFICATION_LAYERS",
    "CleanupScanEntry",
    "CleanupScanReport",
    "ConcurrencyQualificationLayer",
    "ConcurrencyQualificationRecord",
    "ConcurrencyQualificationRow",
    "ConcurrencyRowStatus",
    "ConcurrencyScenarioFamily",
    "ConcurrencySupportIdentity",
    "ExecutionOverlapSample",
    "MachineResourceClass",
    "ObservedOverlapEvidence",
    "RepeatedWaveReport",
    "RepeatedWaveThresholds",
    "ScenarioOwner",
    "WaveObservation",
    "build_row_for_unavailable_environment",
    "compute_concurrency_evidence_digest",
    "layer_requires_postgres",
    "load_observed_overlap",
    "nominal_memory_band_mib",
    "observed_overlap_evidence_path",
    "observed_peak_overlap",
    "owning_test_files",
    "publish_observed_overlap",
    "repeated_wave_thresholds",
    "requested_concurrency_level",
    "scenario_owners",
    "unowned_scenarios",
    "unsatisfied_protected_live_environment",
]
