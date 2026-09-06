"""The scheduled concurrency-qualification jobs supply what their layers need.

Source issue: MoonLadderStudios/MoonMind#3885.

A layer whose job passes only part of its owning tests' environment is a
documented invocation that can never produce a row: the runner admits the
layer, the owning test fails on its own configuration, and the record carries a
``failed`` concurrency row for what was really a missing variable. These
assertions bind each job's env block to the contract its layer reads, so the
workflow and the owning tests cannot drift apart again.

They also bind the record this workflow publishes to the cross-layer level it
has to support: a validated level needs a passing row in every required layer
under one measured substrate identity, so both required layers are recorded by
one job on one machine (MoonLadderStudios/MoonMind#3885).
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

from moonmind.omnigent.concurrency_qualification import (
    HERMETIC_LEVELS,
    PROTECTED_LIVE_ADMISSION_ENV,
    PROTECTED_LIVE_PROVIDER_PROFILE_ENV,
    PROTECTED_LIVE_REQUIRED_ENV,
    REQUIRED_QUALIFICATION_LAYERS,
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


def _flag_value(script: str, flag: str) -> str:
    """Return the argument ``flag`` carries in one runner invocation.

    Parsed the way the shell parses it: a ``${{ vars.X }}`` expansion contains
    spaces, so splitting on whitespace would compare two invocations on the
    same meaningless prefix and pass whatever they actually pass.
    """

    tokens = shlex.split(script)
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
    return ""


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
        ("exact-docker", "Upload the required-layer concurrency records"),
        ("protected-live", "Upload the protected-live concurrency record"),
    ):
        step = _run_step(job, name)
        assert step["if"] == "always()"
        assert step["with"]["if-no-files-found"] == "error"


def test_every_required_layer_is_recorded_by_one_machine() -> None:
    """The cross-layer level is only reachable when both records merge.

    ``validated_concurrency_level`` needs a passing row in every layer of
    ``REQUIRED_QUALIFICATION_LAYERS`` at the same level, and
    ``load_concurrency_records`` refuses to merge records whose substrate
    identity differs. The resource class in that identity is *measured* on the
    runner, so records earned on two different machines can never merge: a
    hermetic record produced by pull-request CI would leave the published index
    carrying an exact-image record that validates nothing. Both required layers
    are therefore recorded by steps of one job, on one machine, in one run.

    Derived from the layer set rather than a hard-coded pair, so adding a
    required layer without giving it a recording step fails here.
    """

    job = _workflow()["jobs"]["exact-docker"]
    recorded = {
        layer
        for layer in REQUIRED_QUALIFICATION_LAYERS
        for step in job["steps"]
        if f"--layer {layer.value}" in str(step.get("run", ""))
    }

    assert recorded == set(REQUIRED_QUALIFICATION_LAYERS)
    # One machine, so one measured resource class and one identity.
    assert "self-hosted" in job["runs-on"]


def test_the_hermetic_rows_are_recorded_under_the_same_identity() -> None:
    """A record filed under a different identity would not merge, or would raise."""

    exact = _run_step("exact-docker", "Run the exact-image concurrency rows")
    hermetic = _run_step("exact-docker", "Run the hermetic concurrency rows")

    for flag in (
        "--support-combination-key",
        "--moonmind-commit",
        "--worker-build-ref",
        "--worker-topology-ref",
        "--resource-class",
    ):
        exact_value = _flag_value(exact["run"], flag)
        assert exact_value, f"the exact-image step never passes {flag}"
        assert _flag_value(hermetic["run"], flag) == exact_value

    assert "--layer hermetic" in hermetic["run"]
    # The full hermetic ladder, derived from the constant rather than restated,
    # so the cross-layer level is never capped below what the exact-image layer
    # can reach by a workflow that fell behind the levels.
    assert _flag_value(hermetic["run"], "--levels") == ",".join(
        str(level) for level in HERMETIC_LEVELS
    )
    # This layer's owners also decide their invariant against real constraints.
    assert layer_requires_postgres(
        ConcurrencyQualificationLayer.hermetic, root=REPO_ROOT
    )
    assert hermetic["env"]["MOONMIND_TEST_POSTGRES_URL"] == (
        exact["env"]["MOONMIND_TEST_POSTGRES_URL"]
    )
    # Separate evidence trees: one layer's samples are not the other's.
    assert _flag_value(hermetic["run"], "--evidence-dir") != _flag_value(
        exact["run"], "--evidence-dir"
    )
    assert _flag_value(hermetic["run"], "--output") != _flag_value(
        exact["run"], "--output"
    )


def test_the_hermetic_record_survives_a_failed_exact_image_gate() -> None:
    """Evidence that was earned is never withheld because a sibling failed."""

    hermetic = _run_step("exact-docker", "Run the hermetic concurrency rows")
    upload = _run_step("exact-docker", "Upload the required-layer concurrency records")

    assert "cancelled()" in str(hermetic["if"])
    # Both records are staged under the uploaded path, so the publisher's
    # download sees the whole record for this run.
    assert upload["with"]["path"] == "artifacts/omnigent-concurrency"
    for step in (hermetic, _run_step("exact-docker", "Run the exact-image concurrency rows")):
        assert _flag_value(step["run"], "--output").startswith(
            upload["with"]["path"] + "/"
        )
