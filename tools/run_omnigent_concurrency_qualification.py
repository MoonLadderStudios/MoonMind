#!/usr/bin/env python3
"""Produce the versioned Omnigent concurrency qualification record.

Source issue: MoonLadderStudios/MoonMind#3885.

This is the reporting entrypoint for the layered N-way program. It does not
schedule anything and it does not decide whether a level is safe: it runs the
layer's owning tests, converts each (layer, level) outcome into one
:class:`~moonmind.omnigent.concurrency_qualification.ConcurrencyQualificationRow`,
and writes the record.

The behaviour that matters is what happens when a layer cannot run. A runner
that cannot reach the Docker daemon, the built images, the hermetic layer's
database, or the protected live route emits an ``unavailable`` or ``blocked``
row — never a silent skip and never an omitted row — so the missing level is
visible in the record and the validated level does not rise past what was
actually observed.

The exit code answers for *this invocation*: zero only when every requested
``(layer, level)`` row passed, and a requested row that was never produced
counts as a failure. That is deliberately not
``record.validated_concurrency_level``, which is the cross-layer advertisement
and needs a record carrying every required layer at the same level. Each CI job
runs one layer, so gating a job on the cross-layer level would make it
impossible to pass.

Usage::

    python tools/run_omnigent_concurrency_qualification.py \\
        --layer exact_docker --levels 2,4,8 \\
        --support-combination-key omnigent-support:sha256:... \\
        --moonmind-commit "$GITHUB_SHA" \\
        --output artifacts/omnigent-concurrency/record.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

# Allow execution as a script from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moonmind.omnigent.concurrency_qualification import (  # noqa: E402
    CONCURRENCY_EVIDENCE_DIR_ENV,
    CONCURRENCY_LEVEL_ENV,
    CONCURRENCY_SCENARIO_CATALOG_VERSION,
    EXACT_DOCKER_LEVELS,
    HERMETIC_LEVELS,
    ConcurrencyQualificationLayer,
    ConcurrencyQualificationRecord,
    ConcurrencyQualificationRow,
    ConcurrencyRowStatus,
    ConcurrencySupportIdentity,
    MachineResourceClass,
    compute_concurrency_evidence_digest,
    load_observed_overlap,
    observed_overlap_evidence_path,
    scenario_owners,
    unowned_scenarios,
)

DEFAULT_LEVELS = {
    ConcurrencyQualificationLayer.hermetic: HERMETIC_LEVELS,
    ConcurrencyQualificationLayer.exact_docker: EXACT_DOCKER_LEVELS,
    ConcurrencyQualificationLayer.protected_live: (2,),
}


class LayerUnavailable(RuntimeError):
    """The layer's environment is absent; its rows are ``unavailable``."""


class LayerBlocked(RuntimeError):
    """A policy or release gate refused the layer; its rows are ``blocked``."""


def _docker_available() -> None:
    """Raise unless a usable Docker daemon and the exact images are present.

    The checks here mirror the exact-Docker owning test's own environment
    preconditions, so a runner missing one of them records an ``unavailable``
    row naming what was absent instead of entering the layer and recording a
    ``partial`` row whose diagnostic is only "nothing was observed".
    """

    if os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "").strip() == "":
        raise LayerUnavailable(
            "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE is not a digest-pinned image"
        )
    if os.getenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "").strip() == "":
        raise LayerUnavailable(
            "MOONMIND_OMNIGENT_HOST_SERVER_URL names no host server endpoint"
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


def _hermetic_database_available() -> None:
    """Raise unless the hermetic layer's real database constraints can run.

    The hermetic layer's declared environment includes real database
    constraints: one of its owners races two independent PostgreSQL
    transactions for the final slot. A runner without a cluster has *not*
    exercised that constraint, so the layer reports ``unavailable`` and names
    the missing dependency rather than entering and recording the resulting
    fixture error as a concurrency failure.
    """

    if os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip():
        return
    if shutil.which("initdb") is not None:
        return
    if sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb")):
        return
    raise LayerUnavailable(
        "no PostgreSQL cluster is available for the hermetic layer's database "
        "constraints; set MOONMIND_TEST_POSTGRES_URL or install the PostgreSQL "
        "binaries (./tools/test_integration.sh provides both)"
    )


def _protected_live_admitted() -> None:
    """Raise unless the protected release admission boundary authorized this run."""

    if os.getenv("MOONMIND_OMNIGENT_PROTECTED_LIVE_CONCURRENCY", "").strip() != "1":
        raise LayerBlocked(
            "protected-live concurrency is opt-in and was not admitted for this run"
        )
    if not os.getenv("MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID", "").strip():
        raise LayerUnavailable(
            "no credentialless provider route is configured for protected live"
        )


def _resource_class(args: argparse.Namespace) -> MachineResourceClass:
    return MachineResourceClass(
        resource_class_ref=args.resource_class,
        cpu_cores=args.cpu_cores,
        memory_gib=args.memory_gib,
    )


def _run_owning_tests(
    layer: ConcurrencyQualificationLayer, level: int, args_evidence_dir: str
) -> int:
    """Execute the layer's owning tests for one level and return the exit code."""

    targets = sorted({owner.owning_test.split("::")[0] for owner in scenario_owners(layer=layer)})
    targets = [target for target in targets if Path(target).exists()]
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


def build_rows(
    args: argparse.Namespace,
    layer: ConcurrencyQualificationLayer,
    levels: tuple[int, ...],
) -> list[ConcurrencyQualificationRow]:
    """Return one honest row per requested level for ``layer``."""

    try:
        if layer is ConcurrencyQualificationLayer.hermetic:
            _hermetic_database_available()
        elif layer is ConcurrencyQualificationLayer.exact_docker:
            _docker_available()
        elif layer is ConcurrencyQualificationLayer.protected_live:
            _protected_live_admitted()
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
                evidence_ref=str(evidence_path),
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
                tuple(int(item) for item in args.levels.split(",") if item.strip())
                if args.levels and args.layer != "all"
                else DEFAULT_LEVELS[layer]
            ),
        )
        for layer in layers
    )


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
    unowned = unowned_scenarios()
    if unowned:
        raise SystemExit(
            "concurrency scenario families without an owning test: "
            + ", ".join(f"{family.value}/{layer.value}" for family, layer in unowned)
        )
    rows: list[ConcurrencyQualificationRow] = []
    for layer, levels in requested_matrix(args):
        rows.extend(build_rows(args, layer, levels))
    identity = ConcurrencySupportIdentity(
        supportCombinationKey=args.support_combination_key,
        moonmindCommit=args.moonmind_commit,
        workerBuildRef=args.worker_build_ref,
        providerCapacityPolicyVersion=args.provider_capacity_policy_version,
        hostCapacityPolicyVersion=args.host_capacity_policy_version,
        transportPoolPolicyVersion=args.transport_pool_policy_version,
        workerTopologyRef=args.worker_topology_ref,
        resourceClass=_resource_class(args),
        scenarioCatalogVersion=CONCURRENCY_SCENARIO_CATALOG_VERSION,
    )
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
    parser.add_argument("--resource-class", default="local-deterministic@1")
    parser.add_argument("--cpu-cores", type=int, default=4)
    parser.add_argument("--memory-gib", type=int, default=8)
    parser.add_argument(
        "--evidence-dir", default="artifacts/omnigent-concurrency/evidence"
    )
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
