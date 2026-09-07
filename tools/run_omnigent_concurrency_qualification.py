#!/usr/bin/env python3
"""Produce the versioned Omnigent concurrency qualification record.

Source issue: MoonLadderStudios/MoonMind#3885.

This is the reporting entrypoint for the layered N-way program. It does not
schedule anything and it does not decide whether a level is safe: it runs the
layer's owning tests, converts each (layer, level) outcome into one
:class:`~moonmind.omnigent.concurrency_qualification.ConcurrencyQualificationRow`,
and writes the record.

The behaviour that matters is what happens when a layer cannot run. A runner
that cannot reach the Docker daemon, the built images, the PostgreSQL cluster a
layer's owners require, or the protected live route emits an ``unavailable`` or
``blocked`` row — never a silent skip and never an omitted row — so the missing
level is visible in the record and the validated level does not rise past what
was actually observed. Each layer's precondition is composed from that layer's
own catalog owners by :func:`layer_preconditions`, so an owner cannot bring an
environment its layer never checks.

The exit code answers for *this invocation*: zero only when every requested
``(layer, level)`` row passed, and a requested row that was never produced
counts as a failure. That is deliberately not
``record.validated_concurrency_level``, which is the cross-layer advertisement
and needs a record carrying every required layer at the same level. Each CI job
runs one layer, so gating a job on the cross-layer level would make it
impossible to pass.

The support identity is resolved *before* any layer runs, and the record is
written before the gate is evaluated. A wave is never spent under an identity
that cannot be built, and evidence that was earned is never withheld because
the gate failed.

Usage::

    python tools/run_omnigent_concurrency_qualification.py \\
        --layer exact_docker --levels 2,4,8 \\
        --support-combination-key omnigent-support:sha256:... \\
        --moonmind-commit "$GITHUB_SHA" \\
        --host-image-ref ghcr.io/example/opencode-host@sha256:... \\
        --worker-build-ref moonmind-worker@2026.09 \\
        --worker-topology-ref single-replica@1 \\
        --resource-class ci-standard-4x8@1 \\
        --output artifacts/omnigent-concurrency/record.json

The machine dimensions are never arguments. The runner measures the cores and
memory this process may actually use — affinity mask, CPU quota, memory limit —
so the published resource class describes the machine that actually ran the
level. ``--resource-class`` names the class the rows are filed under, and a ref
that carries its own ``<cores>x<gib>`` dimensions has to agree with that
measurement or the identity is refused before a layer runs. ``--host-image-ref``
is held to the image the layer will actually launch for the same reason: the
support key is a digest and cannot be recomputed from a substituted artifact.

``--levels`` is bounded by the layer's own declared matrix. This program spends
real containers and a shared provider route, so a level nobody declared is
refused before anything is launched rather than after.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

# Allow execution as a script from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moonmind.omnigent.concurrency_qualification import (  # noqa: E402
    CONCURRENCY_EVIDENCE_DIR_ENV,
    CONCURRENCY_LEVEL_ENV,
    CONCURRENCY_SCENARIO_CATALOG_VERSION,
    EXACT_DOCKER_LEVELS,
    EXACT_HOST_IMAGE_ENV,
    HERMETIC_LEVELS,
    PROTECTED_LIVE_ADMISSION_ENV,
    PROTECTED_LIVE_PROVIDER_PROFILE_ENV,
    ConcurrencyQualificationLayer,
    ConcurrencyQualificationRecord,
    ConcurrencyQualificationRow,
    ConcurrencyRowStatus,
    ConcurrencySupportIdentity,
    MachineResourceClass,
    allowed_concurrency_levels,
    compute_concurrency_evidence_digest,
    layer_requires_postgres,
    load_observed_overlap,
    observed_overlap_evidence_path,
    owning_test_files,
    unowned_scenarios,
    unsatisfied_exact_docker_environment,
    unsatisfied_protected_live_environment,
)

DEFAULT_LEVELS = {
    ConcurrencyQualificationLayer.hermetic: HERMETIC_LEVELS,
    ConcurrencyQualificationLayer.exact_docker: EXACT_DOCKER_LEVELS,
    ConcurrencyQualificationLayer.protected_live: (2,),
}

#: Every identity input, with the CLI flag that carries it and the scheduled
#: workflow's repository variable behind that flag. An unset GitHub variable
#: expands to the empty string, which argparse happily accepts *over* the
#: default, so a blank value here is a misconfiguration rather than a request
#: for a default — and it has to be caught before a layer spends its matrix,
#: not after, or the run throws away the only evidence it earned.
IDENTITY_ARGUMENT_SOURCES: dict[str, tuple[str, str]] = {
    "support_combination_key": (
        "--support-combination-key",
        "OMNIGENT_CONCURRENCY_SUPPORT_KEY",
    ),
    "moonmind_commit": ("--moonmind-commit", "github.sha"),
    "host_image_ref": (
        "--host-image-ref",
        "OMNIGENT_CONFORMANCE_OPENCODE_HOST_IMAGE",
    ),
    "worker_build_ref": ("--worker-build-ref", "OMNIGENT_WORKER_BUILD_REF"),
    "provider_capacity_policy_version": (
        "--provider-capacity-policy-version",
        "",
    ),
    "host_capacity_policy_version": ("--host-capacity-policy-version", ""),
    "transport_pool_policy_version": ("--transport-pool-policy-version", ""),
    "worker_topology_ref": ("--worker-topology-ref", "OMNIGENT_WORKER_TOPOLOGY_REF"),
    "resource_class": (
        "--resource-class",
        "OMNIGENT_CONCURRENCY_RESOURCE_CLASS",
    ),
}

#: Model field names — in either casing the models accept — mapped back to the
#: flag an operator can actually change, so a rejected identity names an input
#: instead of a pydantic location.
_FLAGS_BY_FIELD: dict[str, str] = {
    **{field: flag for field, (flag, _) in IDENTITY_ARGUMENT_SOURCES.items()},
    "supportCombinationKey": "--support-combination-key",
    "moonmindCommit": "--moonmind-commit",
    "hostImageRef": "--host-image-ref",
    "workerBuildRef": "--worker-build-ref",
    "providerCapacityPolicyVersion": "--provider-capacity-policy-version",
    "hostCapacityPolicyVersion": "--host-capacity-policy-version",
    "transportPoolPolicyVersion": "--transport-pool-policy-version",
    "workerTopologyRef": "--worker-topology-ref",
    "resource_class_ref": "--resource-class",
}


class LayerUnavailable(RuntimeError):
    """The layer's environment is absent; its rows are ``unavailable``."""


class LayerBlocked(RuntimeError):
    """A policy or release gate refused the layer; its rows are ``blocked``."""


def _docker_available() -> None:
    """Raise unless a usable Docker daemon and the exact images are present.

    The environment names come from
    :data:`~moonmind.omnigent.concurrency_qualification.EXACT_DOCKER_REQUIRED_ENV`,
    the same tuple the exact-Docker owning test reads, so a runner missing one
    of them records an ``unavailable`` row naming what was absent instead of
    entering the layer and recording a ``partial`` row whose diagnostic is only
    "nothing was observed".
    """

    unsatisfied = unsatisfied_exact_docker_environment()
    if unsatisfied:
        raise LayerUnavailable(
            "the exact-image layer is not configured: " + ", ".join(unsatisfied)
        )
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LayerUnavailable(f"docker is not invocable: {exc}") from exc
    if completed.returncode != 0:
        raise LayerUnavailable("the Docker daemon did not report a server version")


def _database_available() -> None:
    """Raise unless the PostgreSQL cluster a layer's owners require is reachable.

    Several owning tests decide their invariant with real database
    constraints — one races two independent PostgreSQL transactions for the
    final slot — and their fixture fails closed rather than skipping when no
    cluster is reachable. A runner without one has *not* exercised those
    constraints, so the layer reports ``unavailable`` and names the missing
    dependency instead of entering and recording the fixture error as a
    concurrency failure.
    """

    if os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip():
        return
    if shutil.which("initdb") is not None:
        return
    if sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb")):
        return
    raise LayerUnavailable(
        "no PostgreSQL cluster is available for this layer's database "
        "constraints; set MOONMIND_TEST_POSTGRES_URL or install the PostgreSQL "
        "binaries (./tools/test_integration.sh provides both)"
    )


def _protected_live_admitted() -> None:
    """Raise unless the release boundary admitted this run *and* the route exists.

    Admission and configuration are answered here, before the layer is
    entered, because the owning test hard-requires the same values and fails
    rather than skips: checking a different pair here is what turns an
    unconfigured repository into a ``failed`` row that reads like a
    concurrency defect. Admission stays a separate ``blocked`` outcome — a
    refusal to admit and a missing credential are different operator actions.
    """

    if os.getenv(PROTECTED_LIVE_ADMISSION_ENV, "").strip() != "1":
        raise LayerBlocked(
            "protected-live concurrency is opt-in and was not admitted for this run"
        )
    if not os.getenv(PROTECTED_LIVE_PROVIDER_PROFILE_ENV, "").strip():
        raise LayerUnavailable(
            "no credentialless provider route is configured for protected live"
        )
    unsatisfied = unsatisfied_protected_live_environment()
    if unsatisfied:
        raise LayerUnavailable(
            "the protected-live provider route is not configured: "
            + ", ".join(unsatisfied)
        )


def layer_preconditions(
    layer: ConcurrencyQualificationLayer,
) -> tuple[Callable[[], None], ...]:
    """Return the environment checks ``layer`` must pass before it is entered.

    The database check is *derived* from the layer's own catalog owners rather
    than listed against a layer name, so a PostgreSQL-dependent owner added to
    any layer brings the precondition with it. That is the whole contract this
    program advertises: a missing dependency is recorded as ``unavailable``
    naming what was absent, never as a ``failed`` row.
    """

    checks: list[Callable[[], None]] = []
    if layer is ConcurrencyQualificationLayer.exact_docker:
        checks.append(_docker_available)
    elif layer is ConcurrencyQualificationLayer.protected_live:
        checks.append(_protected_live_admitted)
    if layer_requires_postgres(layer):
        checks.append(_database_available)
    return tuple(checks)


class MachineNotObservable(RuntimeError):
    """The machine this invocation runs on cannot be measured."""


#: The cgroup interface files that bound what this process may actually use.
#: A container is namespaced onto the root of its own hierarchy, so these are
#: the paths a confined runner reads; on an unconfined host they read ``max``
#: (v2) or are absent (v1), and the host measurement stands.
CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
CGROUP_V1_MEMORY_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")

#: cgroup v1 spells "unlimited" as a saturated 64-bit sentinel rather than a
#: word, so any limit at or above this is no limit at all.
_CGROUP_V1_UNLIMITED = 1 << 62


def _cgroup_value(path: Path) -> str:
    """Return one cgroup interface file's contents, or ``""`` when unreadable."""

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def cgroup_cpu_quota_cores() -> int | None:
    """Return the whole cores a CPU quota permits, or ``None`` when unconfined.

    A cpuset shows up in the affinity mask; a CFS quota does not. A runner
    confined to two cores by ``cpu.max`` still sees every host CPU in its mask,
    so the quota is read separately. A fractional quota is floored: a row must
    not claim more parallelism than the cgroup will actually schedule.
    """

    v2 = _cgroup_value(CGROUP_V2_CPU_MAX).split()
    if len(v2) == 2 and v2[0] != "max":
        quota, period = v2
    else:
        quota = _cgroup_value(CGROUP_V1_CPU_QUOTA)
        period = _cgroup_value(CGROUP_V1_CPU_PERIOD)
    try:
        quota_us, period_us = int(quota), int(period)
    except ValueError:
        return None
    if quota_us <= 0 or period_us <= 0:
        return None
    return max(1, quota_us // period_us)


def cgroup_memory_limit_bytes() -> int | None:
    """Return the memory a cgroup permits, or ``None`` when unconfined.

    ``SC_PHYS_PAGES`` is host physical memory and ignores a cgroup limit
    entirely, so a container-confined runner would otherwise publish the host's
    memory as the machine that ran the level.
    """

    for path in (CGROUP_V2_MEMORY_MAX, CGROUP_V1_MEMORY_MAX):
        raw = _cgroup_value(path)
        if not raw or raw == "max":
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        if 0 < limit < _CGROUP_V1_UNLIMITED:
            return limit
    return None


def observed_cpu_cores() -> int:
    """Return the CPU cores this process may actually run on.

    The affinity mask, not :func:`os.cpu_count`, is what a cpuset-confined CI
    runner is allowed to use, and a CFS quota bounds it further without
    changing the mask, so the smaller of the two wins. The resource class has
    to name the machine the wave really ran on.
    """

    if hasattr(os, "sched_getaffinity"):
        cores = len(os.sched_getaffinity(0))
    else:  # pragma: no cover - not reachable on the supported platforms
        cores = os.cpu_count() or 0
    quota_cores = cgroup_cpu_quota_cores()
    if quota_cores is not None:
        cores = min(cores, quota_cores)
    if cores < 1:
        raise MachineNotObservable(
            "the CPU core count of this machine could not be observed"
        )
    return cores


def observed_memory_mib() -> int:
    """Return the memory this machine actually offers, in whole MiB.

    Whole GiB cannot express this measurement: ``MemTotal`` is physical memory
    minus the kernel's reservation, so a nominal 8-GiB machine reports about
    7947 MiB and flooring it to 7 GiB would make it a machine no 8-GiB class
    could ever name. A cgroup limit, when one is in force, is the real
    ceiling and replaces the host's physical size.
    """

    try:
        total_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError) as exc:  # pragma: no cover
        raise MachineNotObservable(
            f"the physical memory of this machine could not be observed: {exc}"
        ) from exc
    limit = cgroup_memory_limit_bytes()
    if limit is not None:
        total_bytes = min(total_bytes, limit)
    memory_mib = int(total_bytes // (1024**2))
    if memory_mib < 1:
        raise MachineNotObservable(
            "this machine reports less than one MiB of usable memory"
        )
    return memory_mib


def _resource_class(args: argparse.Namespace) -> MachineResourceClass:
    """Return the machine this invocation ran on, always measured.

    There is no declaration path. A flag that could name the machine was the
    only way to publish a row for substrate that never ran the level, and it
    made the invariant opt-out on exactly the invocation an operator controls.
    ``--resource-class`` names the class the rows are filed under; when that
    ref carries its own ``<cores>x<gib>`` dimensions,
    :class:`MachineResourceClass` holds the measurement to them, so the ref is
    a claim this machine has to satisfy rather than a label it may carry.
    """

    return MachineResourceClass(
        resource_class_ref=args.resource_class,
        cpu_cores=observed_cpu_cores(),
        memory_mib=observed_memory_mib(),
    )


def _named_arguments(exc: ValidationError, *, fallback: str) -> str:
    """Render a pydantic rejection as the flags an operator can actually set.

    A whole-model validator carries no field location, so ``fallback`` names
    the argument that owns the rejected model instead of leaving the operator
    with a bare pydantic message.
    """

    details: list[str] = []
    for error in exc.errors():
        field = str(error["loc"][-1]) if error["loc"] else ""
        flag = _FLAGS_BY_FIELD.get(field, field) or fallback
        details.append(f"{flag}: {error['msg']}")
    return "; ".join(details)


def _exercised_host_image(args: argparse.Namespace) -> str:
    """Return the exact host image this invocation runs under.

    The support key is a digest of the whole exact combination, so it cannot be
    inverted back into the artifacts it was built from: an operator who
    dispatches a different host image while a repository variable still names
    the previous combination would otherwise file rows under an identity whose
    host artifacts were never exercised. The declared ref is therefore held to
    the image the layer will actually launch — the one the owning tests read
    from :data:`EXACT_HOST_IMAGE_ENV` — and a disagreement is refused here, before
    the matrix is spent.
    """

    declared = str(getattr(args, "host_image_ref", "") or "").strip()
    observed = os.getenv(EXACT_HOST_IMAGE_ENV, "").strip()
    if observed and observed != declared:
        raise SystemExit(
            "the declared host image does not match the image this run would "
            f"launch: --host-image-ref names {declared!r} while {EXACT_HOST_IMAGE_ENV} "
            f"names {observed!r}; the support combination key is a digest of the "
            "exact artifacts and cannot be recomputed from a substituted image, "
            "so no layer was run"
        )
    return declared


def build_identity(args: argparse.Namespace) -> ConcurrencySupportIdentity:
    """Validate the support identity before any layer spends its matrix.

    The record is what an exact-image wave exists to produce, and it is built
    from these arguments. Constructing the identity last meant a blank
    repository variable threw away a completed exact-image matrix — hours of
    real hosts on a real daemon — and handed the operator a pydantic traceback
    instead of the name of the variable they had to set. So the identity is
    resolved first: nothing is spent until the record it would be filed under
    is known to be constructible.
    """

    blank = [
        f"{flag} (repository variable {variable})" if variable else flag
        for attribute, (flag, variable) in IDENTITY_ARGUMENT_SOURCES.items()
        if not str(getattr(args, attribute, "") or "").strip()
    ]
    if blank:
        raise SystemExit(
            "the concurrency support identity is incomplete, so no layer was "
            "run and no evidence was spent; supply " + ", ".join(blank)
        )
    try:
        resource_class = _resource_class(args)
    except MachineNotObservable as exc:
        raise SystemExit(
            "the machine resource class could not be resolved, so no layer was "
            f"run: {exc}; the qualification layers must run on a machine this "
            "process can measure, because the published class is a measurement"
        ) from exc
    except ValidationError as exc:
        raise SystemExit(
            "the machine resource class was refused, so no layer was run: "
            + _named_arguments(exc, fallback="--resource-class")
        ) from exc
    try:
        return ConcurrencySupportIdentity(
            supportCombinationKey=args.support_combination_key,
            moonmindCommit=args.moonmind_commit,
            hostImageRef=_exercised_host_image(args),
            workerBuildRef=args.worker_build_ref,
            providerCapacityPolicyVersion=args.provider_capacity_policy_version,
            hostCapacityPolicyVersion=args.host_capacity_policy_version,
            transportPoolPolicyVersion=args.transport_pool_policy_version,
            workerTopologyRef=args.worker_topology_ref,
            resourceClass=resource_class,
            scenarioCatalogVersion=CONCURRENCY_SCENARIO_CATALOG_VERSION,
        )
    except ValidationError as exc:
        raise SystemExit(
            "the concurrency support identity was refused, so no layer was "
            "run: " + _named_arguments(exc, fallback="--support-combination-key")
        ) from exc


def _run_owning_tests(
    layer: ConcurrencyQualificationLayer, level: int, args_evidence_dir: str
) -> int:
    """Execute the layer's owning tests for one level and return the exit code."""

    # The same resolution the layer's precondition read, so the files whose
    # environment was checked are exactly the files that run.
    targets = [str(path) for path in owning_test_files(layer)]
    if not targets:
        raise LayerUnavailable(f"no owning test resolves for layer {layer.value}")
    env = dict(os.environ)
    env[CONCURRENCY_LEVEL_ENV] = str(level)
    # The owning test publishes its observed overlap where build_rows looks for
    # it, so a pass that produced no observation is recorded as ``partial``.
    env[CONCURRENCY_EVIDENCE_DIR_ENV] = str(Path(args_evidence_dir))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q"],
        env=env,
        check=False,
    )
    return completed.returncode


def _discard_stale_evidence(
    evidence_dir: str,
    layer: ConcurrencyQualificationLayer,
    level: int,
) -> None:
    """Remove any observation left at this row's evidence path by an earlier run."""

    try:
        observed_overlap_evidence_path(evidence_dir, layer, level).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        # An evidence slot that cannot be emptied cannot be trusted, and
        # continuing would file the previous run's observation under this one.
        raise SystemExit(
            "the observed-overlap evidence slot for "
            f"{layer.value} N={level} could not be cleared before the row ran: "
            f"{exc}"
        ) from exc


#: The ambient run context :func:`durable_evidence_ref` derives a publication
#: reference from when ``--evidence-base-ref`` is not supplied, in the order it
#: reads them. Declared as one tuple because the fallback is only meaningful
#: relative to what the function actually consults: a suite that means "no
#: durable publication context" clears exactly this, and cannot drift out of
#: agreement with the function by clearing a stale list of names.
GITHUB_RUN_CONTEXT_ENV: tuple[str, ...] = (
    "GITHUB_SERVER_URL",
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
)


def durable_evidence_ref(args: argparse.Namespace, path: Path) -> str:
    """Return the reference a reader can resolve this observation from.

    The workspace path the file was written to is meaningless once the record
    is downloaded into the protected publish run and embedded in the index: it
    names no workflow artifact and does not survive the runner. When a durable
    publication context exists — a GitHub run, or an explicit
    ``--evidence-base-ref`` — the row carries the immutable run/artifact
    reference instead. Outside one, the ref is an explicit ``file:`` URI, which
    the publisher refuses to admit, so a workspace-local observation can never
    reach the published index while pretending to be resolvable.
    """

    base = str(getattr(args, "evidence_base_ref", "") or "").strip().rstrip("/")
    if not base:
        server, repository, run_id, attempt = (
            os.getenv(name, "").strip() for name in GITHUB_RUN_CONTEXT_ENV
        )
        server = server.rstrip("/")
        attempt = attempt or "1"
        if server and repository and run_id:
            base = f"{server}/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    if not base:
        return path.resolve().as_uri()
    artifact = str(getattr(args, "evidence_artifact", "") or "").strip()
    if artifact:
        return f"{base}#artifact={artifact}/{path.name}"
    return f"{base}#{path.name}"


def build_rows(
    args: argparse.Namespace,
    layer: ConcurrencyQualificationLayer,
    levels: tuple[int, ...],
) -> list[ConcurrencyQualificationRow]:
    """Return one honest row per requested level for ``layer``."""

    try:
        for precondition in layer_preconditions(layer):
            precondition()
    except LayerUnavailable as exc:
        return [
            ConcurrencyQualificationRow(
                layer=layer,
                level=level,
                status=ConcurrencyRowStatus.unavailable,
                diagnostics=(str(exc),),
            )
            for level in levels
        ]
    except LayerBlocked as exc:
        return [
            ConcurrencyQualificationRow(
                layer=layer,
                level=level,
                status=ConcurrencyRowStatus.blocked,
                diagnostics=(str(exc),),
            )
            for level in levels
        ]

    rows: list[ConcurrencyQualificationRow] = []
    for level in levels:
        # The evidence path is fixed per (layer, level), so a file left by an
        # earlier invocation against the same directory — a local rerun, a
        # persistent self-hosted workspace — would be read back as *this*
        # invocation's observation. A row that observed nothing has to come out
        # ``partial``, so the slot is emptied before the owners are spawned
        # rather than trusted afterwards.
        _discard_stale_evidence(args.evidence_dir, layer, level)
        code = _run_owning_tests(layer, level, args.evidence_dir)
        if code != 0:
            rows.append(
                ConcurrencyQualificationRow(
                    layer=layer,
                    level=level,
                    status=ConcurrencyRowStatus.failed,
                    diagnostics=(f"owning tests exited {code}",),
                )
            )
            continue
        # A passing execution still has to publish its observed overlap through
        # the layer's evidence file. Without it the row is ``partial``: the
        # tests passed, but nothing observed the concurrency being claimed.
        evidence_path = observed_overlap_evidence_path(
            args.evidence_dir, layer, level
        )
        overlap = load_observed_overlap(args.evidence_dir, layer, level)
        if overlap is None:
            rows.append(
                ConcurrencyQualificationRow(
                    layer=layer,
                    level=level,
                    status=ConcurrencyRowStatus.partial,
                    diagnostics=(
                        f"no observed-overlap evidence at {evidence_path.name}",
                    ),
                )
            )
            continue
        payload = overlap.model_dump(mode="json", by_alias=True)
        rows.append(
            ConcurrencyQualificationRow(
                layer=layer,
                level=level,
                status=ConcurrencyRowStatus.passed,
                overlap=overlap,
                evidence_ref=durable_evidence_ref(args, evidence_path),
                evidence_digest=compute_concurrency_evidence_digest(payload),
                resource_class=_resource_class(args),
            )
        )
    return rows


def requested_matrix(
    args: argparse.Namespace,
) -> tuple[tuple[ConcurrencyQualificationLayer, tuple[int, ...]], ...]:
    """Return the exact (layer, levels) this invocation was asked to produce.

    This is the invocation's own contract, and it is deliberately separate from
    :data:`REQUIRED_QUALIFICATION_LAYERS`. A job that runs one layer is
    answerable for that layer's rows; the cross-layer validated level is an
    advertisement computed from the whole record, and it can only be reached by
    combining records from several jobs.

    A requested level outside the layer's declared matrix is refused here,
    before anything is launched. This program advertises a *bounded* load, and
    an unbounded ``--levels`` would let one invocation ask a trusted
    qualification runner for an arbitrary number of real containers or provider
    sessions.
    """

    layers = (
        tuple(ConcurrencyQualificationLayer)
        if args.layer == "all"
        else (ConcurrencyQualificationLayer(args.layer),)
    )
    return tuple(
        (
            layer,
            (
                _bounded_levels(layer, args.levels)
                if args.levels and args.layer != "all"
                else DEFAULT_LEVELS[layer]
            ),
        )
        for layer in layers
    )


def _bounded_levels(
    layer: ConcurrencyQualificationLayer, raw: str
) -> tuple[int, ...]:
    """Return the requested levels, refusing any the layer does not declare."""

    allowed = allowed_concurrency_levels(layer)
    requested: list[int] = []
    refused: list[str] = []
    for item in raw.split(","):
        entry = item.strip()
        if not entry:
            continue
        try:
            level = int(entry)
        except ValueError:
            refused.append(entry)
            continue
        if level not in allowed:
            refused.append(entry)
            continue
        requested.append(level)
    if refused:
        raise SystemExit(
            f"--levels asked layer {layer.value} for "
            + ", ".join(refused)
            + "; that layer declares "
            + ", ".join(str(level) for level in allowed)
            + ", and a level with no declared matrix entry has no bounded-load "
            "contract, so no layer was run"
        )
    if not requested:
        raise SystemExit(
            f"--levels selected no level for layer {layer.value}; it declares "
            + ", ".join(str(level) for level in allowed)
        )
    return tuple(requested)


def requested_rows_that_did_not_pass(
    record: ConcurrencyQualificationRecord,
    requested: Sequence[tuple[ConcurrencyQualificationLayer, tuple[int, ...]]],
) -> tuple[tuple[ConcurrencyQualificationLayer, int, ConcurrencyQualificationRow | None], ...]:
    """Return every requested row that is missing or did not pass.

    An empty result is this invocation's success criterion. A missing row
    counts as a failure so a layer that silently produced nothing cannot exit
    zero, and every non-pass status (failed, skipped, blocked, unavailable,
    partial) is reported with the row that carries its diagnostics.
    """

    failures: list[
        tuple[ConcurrencyQualificationLayer, int, ConcurrencyQualificationRow | None]
    ] = []
    for layer, levels in requested:
        for level in levels:
            row = record.row(layer, level)
            if row is None or not row.qualifies:
                failures.append((layer, level, row))
    return tuple(failures)


def build_record(args: argparse.Namespace) -> ConcurrencyQualificationRecord:
    # Both preconditions are resolved before a single owning test is spawned:
    # a catalog with an unowned family and an identity that cannot be built
    # both make the record unpublishable, and discovering either one after the
    # matrix has run destroys the evidence rather than reporting it.
    unowned = unowned_scenarios()
    if unowned:
        raise SystemExit(
            "concurrency scenario families without an owning test: "
            + ", ".join(f"{family.value}/{layer.value}" for family, layer in unowned)
        )
    identity = build_identity(args)
    rows: list[ConcurrencyQualificationRow] = []
    for layer, levels in requested_matrix(args):
        rows.extend(build_rows(args, layer, levels))
    return ConcurrencyQualificationRecord(
        identity=identity,
        generatedAt=datetime.now(UTC),
        rows=tuple(rows),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layer",
        choices=[item.value for item in ConcurrencyQualificationLayer] + ["all"],
        default="all",
    )
    parser.add_argument("--levels", default="")
    parser.add_argument("--support-combination-key", required=True)
    parser.add_argument("--moonmind-commit", required=True)
    # The exact host artifact the rows are earned on. Held to the image the
    # layer will actually launch; see :func:`_exercised_host_image`.
    parser.add_argument("--host-image-ref", required=True)
    parser.add_argument("--worker-build-ref", default="moonmind-worker@local")
    parser.add_argument(
        "--provider-capacity-policy-version", default="omnigent-provider-capacity@1"
    )
    parser.add_argument(
        "--host-capacity-policy-version", default="omnigent-host-capacity@1"
    )
    parser.add_argument(
        "--transport-pool-policy-version", default="omnigent-transport-pool@1"
    )
    parser.add_argument("--worker-topology-ref", default="single-replica@1")
    # The class the rows are filed under. The machine behind it is measured,
    # never declared: a flag that could name the dimensions was the only way to
    # publish a row for substrate that never ran the level.
    parser.add_argument("--resource-class", default="local-deterministic@1")
    parser.add_argument(
        "--evidence-dir", default="artifacts/omnigent-concurrency/evidence"
    )
    # How a reader resolves an observation once the record has left this
    # workspace. Defaults to this GitHub run/attempt when one is in scope; see
    # :func:`durable_evidence_ref`.
    parser.add_argument("--evidence-base-ref", default="")
    parser.add_argument("--evidence-artifact", default="")
    parser.add_argument(
        "--output", default="artifacts/omnigent-concurrency/record.json"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    requested = requested_matrix(args)
    record = build_record(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    failures = requested_rows_that_did_not_pass(record, requested)
    requested_count = sum(len(levels) for _layer, levels in requested)
    print(
        f"{requested_count - len(failures)}/{requested_count} requested rows "
        "passed"
    )
    # The validated level is the cross-layer advertisement, not this
    # invocation's gate: a single-layer job cannot reach it on its own, and
    # reporting it here keeps that distinction visible in the job log.
    print(
        "cross-layer validated concurrency level in this record: "
        f"{record.validated_concurrency_level}"
    )
    for layer, level, row in failures:
        detail = (
            f"{row.status.value}"
            + (f" — {row.diagnostics[0]}" if row.diagnostics else "")
            if row is not None
            else "no row was produced"
        )
        print(f"  {layer.value} N={level}: {detail}")
    # A non-pass row is reported, not converted into success. Every requested
    # row must pass for this invocation to succeed.
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
