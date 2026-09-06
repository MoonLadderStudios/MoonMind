#!/usr/bin/env python3
"""Produce the versioned Omnigent concurrency qualification record.

Source issue: MoonLadderStudios/MoonMind#3885.

This is the reporting entrypoint for the layered N-way program. It does not
schedule anything and it does not decide whether a level is safe: it runs the
layer's owning tests, converts each (layer, level) outcome into one
:class:`~moonmind.omnigent.concurrency_qualification.ConcurrencyQualificationRow`,
and writes the record.

The behaviour that matters is what happens when a layer cannot run. A runner
that cannot reach the Docker daemon, the built images, or the protected live
route emits an ``unavailable`` or ``blocked`` row — never a silent skip and
never an omitted row — so the missing level is visible in the record and the
validated level does not rise past what was actually observed.

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
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow execution as a script from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moonmind.omnigent.concurrency_qualification import (  # noqa: E402
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
    """Raise unless a usable Docker daemon and the exact images are present."""

    if os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "").strip() == "":
        raise LayerUnavailable(
            "MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE is not a digest-pinned image"
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
    env["MOONMIND_OMNIGENT_CONCURRENCY_LEVEL"] = str(level)
    # The owning test publishes its observed overlap where build_rows looks for
    # it, so a pass that produced no observation is recorded as ``partial``.
    env["MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR"] = str(
        Path(args_evidence_dir)
    )
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
        if layer is ConcurrencyQualificationLayer.exact_docker:
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
        evidence_path = Path(args.evidence_dir) / f"{layer.value}-{level}.json"
        if not evidence_path.exists():
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
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        rows.append(
            ConcurrencyQualificationRow(
                layer=layer,
                level=level,
                status=ConcurrencyRowStatus.passed,
                overlap=payload,
                evidence_ref=str(evidence_path),
                evidence_digest=compute_concurrency_evidence_digest(payload),
                resource_class=_resource_class(args),
            )
        )
    return rows


def build_record(args: argparse.Namespace) -> ConcurrencyQualificationRecord:
    unowned = unowned_scenarios()
    if unowned:
        raise SystemExit(
            "concurrency scenario families without an owning test: "
            + ", ".join(f"{family.value}/{layer.value}" for family, layer in unowned)
        )
    layers = (
        tuple(ConcurrencyQualificationLayer)
        if args.layer == "all"
        else (ConcurrencyQualificationLayer(args.layer),)
    )
    rows: list[ConcurrencyQualificationRow] = []
    for layer in layers:
        levels = (
            tuple(int(item) for item in args.levels.split(",") if item.strip())
            if args.levels and args.layer != "all"
            else DEFAULT_LEVELS[layer]
        )
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
    record = build_record(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    unqualified = record.unqualified_rows
    print(
        f"validated concurrency level: {record.validated_concurrency_level} "
        f"({len(record.rows) - len(unqualified)}/{len(record.rows)} rows passed)"
    )
    for row in unqualified:
        print(
            f"  {row.layer.value} N={row.level}: {row.status.value}"
            + (f" — {row.diagnostics[0]}" if row.diagnostics else "")
        )
    # A non-pass row is reported, not converted into success. The caller
    # decides whether an unavailable environment is acceptable for its gate.
    return 0 if record.validated_concurrency_level else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
