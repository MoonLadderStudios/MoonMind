"""Exact-image N-way concurrency rows for the generic Omnigent plane.

Source issue: MoonLadderStudios/MoonMind#3885 (exact-Docker layer).

The exact-Docker layer runs the built MoonMind, Omnigent server, and host
artifacts under a real Docker daemon at ``N = 2, 4, 8``. On a runner without
that environment the layer cannot produce evidence — and the point of this
module is that *not producing evidence is itself a tested behaviour*.

Two properties are asserted here, and the second one holds on every runner:

* When the exact images and daemon are present, the row is executed and its
  observed overlap is published.
* When they are absent, the layer emits an ``unavailable`` row rather than a
  silent skip, that row never qualifies, and the record's validated
  concurrency level stays at whatever was genuinely observed — ``0`` when the
  required layer produced nothing.

The second property is why a missing environment can never be mistaken for a
pass. It runs in required CI precisely because that is the mistake the program
exists to prevent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.concurrency_qualification import (
    EXACT_DOCKER_LEVELS,
    ConcurrencyQualificationLayer,
    ConcurrencyQualificationRecord,
    ConcurrencyRowStatus,
    ConcurrencySupportIdentity,
    MachineResourceClass,
)
from tools.run_omnigent_concurrency_qualification import build_rows

pytestmark = [pytest.mark.integration]

SUPPORT_KEY = "omnigent-support:sha256:" + "a" * 64


def _runner_args(evidence_dir: str) -> argparse.Namespace:
    return argparse.Namespace(
        evidence_dir=evidence_dir,
        resource_class="ci-standard-4x8@1",
        cpu_cores=4,
        memory_gib=8,
    )


def _identity() -> ConcurrencySupportIdentity:
    return ConcurrencySupportIdentity(
        supportCombinationKey=SUPPORT_KEY,
        moonmindCommit="0" * 40,
        workerBuildRef="moonmind-worker@test",
        providerCapacityPolicyVersion="omnigent-provider-capacity@1",
        hostCapacityPolicyVersion="omnigent-host-capacity@1",
        transportPoolPolicyVersion="omnigent-transport-pool@1",
        workerTopologyRef="single-replica@1",
        resourceClass=MachineResourceClass(
            resource_class_ref="ci-standard-4x8@1", cpu_cores=4, memory_gib=8
        ),
    )


def _exact_docker_environment_reason() -> str:
    """Return why the exact-image layer cannot run here, or ``""``."""

    if not os.getenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", "").strip():
        return "no digest-pinned exact host image is configured"
    if shutil.which("docker") is None:
        return "the docker client is not installed on this runner"
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the docker daemon is unreachable: {exc}"
    if completed.returncode != 0:
        return "the docker daemon did not report a server version"
    return ""


@pytest.mark.integration_ci
def test_an_absent_exact_image_environment_records_unavailable_rows(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runner without the exact images publishes rows, not silence."""

    monkeypatch.delenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", raising=False)

    rows = build_rows(
        _runner_args(str(tmp_path)),
        ConcurrencyQualificationLayer.exact_docker,
        EXACT_DOCKER_LEVELS,
    )

    assert [row.level for row in rows] == list(EXACT_DOCKER_LEVELS)
    for row in rows:
        assert row.status is ConcurrencyRowStatus.unavailable
        assert not row.qualifies
        assert row.overlap is None
        assert row.diagnostics, "an unavailable row must name what was missing"


@pytest.mark.integration_ci
def test_an_unavailable_required_layer_never_raises_the_validated_level(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermetic passes alone cannot qualify a level the images never ran.

    This is the exact confusion the issue forbids: a green hermetic suite plus
    a skipped exact-image row reads like a passing matrix unless the record
    refuses to count the missing layer.
    """

    monkeypatch.delenv("MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE", raising=False)

    exact_rows = build_rows(
        _runner_args(str(tmp_path)),
        ConcurrencyQualificationLayer.exact_docker,
        EXACT_DOCKER_LEVELS,
    )
    record = ConcurrencyQualificationRecord(
        identity=_identity(),
        generatedAt=datetime.now(UTC),
        rows=tuple(exact_rows),
    )

    assert record.validated_concurrency_level == 0
    assert record.advertised_concurrency_level(operator_ceiling=8) == 0
    assert len(record.unqualified_rows) == len(EXACT_DOCKER_LEVELS)


@pytest.mark.parametrize("level", EXACT_DOCKER_LEVELS)
def test_exact_images_run_the_required_concurrency_level(level: int, tmp_path) -> None:
    """Execute the exact-image row when the deployment-owned environment exists."""

    reason = _exact_docker_environment_reason()
    if reason:
        # The row was already recorded as ``unavailable`` by the two required
        # tests above, so skipping the execution here loses no evidence: the
        # record still shows that N=`level` was never observed on real images.
        pytest.skip(f"exact-image concurrency layer unavailable: {reason}")

    rows = build_rows(
        _runner_args(str(tmp_path)),
        ConcurrencyQualificationLayer.exact_docker,
        (level,),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.status is ConcurrencyRowStatus.passed, row.diagnostics
    assert row.overlap is not None
    assert row.overlap.observed_peak == level
    assert row.resource_class is not None
