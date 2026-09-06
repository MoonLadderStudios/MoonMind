"""The scheduled concurrency-qualification jobs supply what their layers need.

Source issue: MoonLadderStudios/MoonMind#3885.

A layer whose job passes only part of its owning tests' environment is a
documented invocation that can never produce a row: the runner admits the
layer, the owning test fails on its own configuration, and the record carries a
``failed`` concurrency row for what was really a missing variable. These
assertions bind each job's env block to the contract its layer reads, so the
workflow and the owning tests cannot drift apart again.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from moonmind.omnigent.concurrency_qualification import (
    PROTECTED_LIVE_ADMISSION_ENV,
    PROTECTED_LIVE_PROVIDER_PROFILE_ENV,
    PROTECTED_LIVE_REQUIRED_ENV,
    ConcurrencyQualificationLayer,
    layer_requires_postgres,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/omnigent-concurrency-qualification.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _run_step(job: str, name: str) -> dict:
    return next(
        step
        for step in _workflow()["jobs"][job]["steps"]
        if step.get("name") == name
    )


def test_the_protected_live_job_supplies_every_value_its_owner_requires() -> None:
    """The owning test hard-requires these and fails rather than skips.

    A GitHub ``environment:`` cannot inject them into the test process, so the
    job has to pass each one explicitly.
    """

    step = _run_step("protected-live", "Run the bounded protected-live concurrency rows")

    for name in PROTECTED_LIVE_REQUIRED_ENV:
        assert name in step["env"], f"the protected-live job never passes {name}"
        assert str(step["env"][name]).strip(), f"{name} is wired to nothing"
    assert step["env"][PROTECTED_LIVE_ADMISSION_ENV] == "1"
    assert step["env"][PROTECTED_LIVE_PROVIDER_PROFILE_ENV] == (
        "${{ vars.MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID }}"
    )
    # The route stays the credentialless one: the token is the server's, not a
    # paid provider key substituted to make a level pass.
    assert step["env"]["OMNIGENT_API_TOKEN"] == "${{ secrets.OMNIGENT_API_TOKEN }}"
    assert step["env"]["OMNIGENT_ENABLED"] == "${{ vars.OMNIGENT_ENABLED }}"
    assert step["env"]["OMNIGENT_DEFAULT_AGENT_NAME"] == (
        "${{ vars.OMNIGENT_DEFAULT_AGENT_NAME }}"
    )


def test_the_protected_live_job_runs_only_the_protected_live_layer() -> None:
    """Opt-in dispatch only, and answerable for its own rows."""

    job = _workflow()["jobs"]["protected-live"]
    step = _run_step("protected-live", "Run the bounded protected-live concurrency rows")

    assert job["environment"] == "omnigent-provider-verification"
    assert "inputs.protected_live == true" in job["if"]
    assert "--layer protected_live" in step["run"]


def test_the_exact_image_job_supplies_the_cluster_its_owners_require() -> None:
    """Four of this layer's owners decide their invariant against PostgreSQL.

    Their fixture fails closed without a cluster, so a job that provides none
    can never produce a row — the runner would honestly record `unavailable`,
    and the layer is a required one.
    """

    assert layer_requires_postgres(
        ConcurrencyQualificationLayer.exact_docker, root=REPO_ROOT
    )

    job = _workflow()["jobs"]["exact-docker"]
    service = job["services"]["postgres"]
    step = _run_step("exact-docker", "Run the exact-image concurrency rows")

    assert "@sha256:" in service["image"], "the service image must be immutable"
    assert "5432:5432" in service["ports"]
    assert step["env"]["MOONMIND_TEST_POSTGRES_URL"] == (
        f"postgresql://{service['env']['POSTGRES_USER']}:"
        f"{service['env']['POSTGRES_PASSWORD']}@127.0.0.1:5432/"
        f"{service['env']['POSTGRES_DB']}"
    )


def test_the_exact_image_job_pins_the_images_and_the_identity() -> None:
    """The layer's own environment, and the identity its rows are filed under."""

    step = _run_step("exact-docker", "Run the exact-image concurrency rows")

    assert step["env"]["MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE"] == (
        "${{ steps.images.outputs.host }}"
    )
    assert step["env"]["MOONMIND_OMNIGENT_HOST_SERVER_URL"] == (
        "${{ vars.OMNIGENT_SERVER_URL }}"
    )
    assert "--layer exact_docker" in step["run"]
    assert "--levels 2,4,8" in step["run"]
    for flag, variable in (
        ("--support-combination-key", "vars.OMNIGENT_CONCURRENCY_SUPPORT_KEY"),
        ("--worker-build-ref", "vars.OMNIGENT_WORKER_BUILD_REF"),
        ("--worker-topology-ref", "vars.OMNIGENT_WORKER_TOPOLOGY_REF"),
        ("--resource-class", "vars.OMNIGENT_CONCURRENCY_RESOURCE_CLASS"),
    ):
        assert f'{flag} "${{{{ {variable} }}}}"' in step["run"]


def test_every_record_is_uploaded_even_when_the_gate_failed() -> None:
    """The record is evidence and survives the exit code."""

    for job, name in (
        ("exact-docker", "Upload the exact-image concurrency record"),
        ("protected-live", "Upload the protected-live concurrency record"),
    ):
        step = _run_step(job, name)
        assert step["if"] == "always()"
        assert step["with"]["if-no-files-found"] == "error"
