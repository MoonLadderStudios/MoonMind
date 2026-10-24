"""The default release channel must exercise the image it distributes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def ancestors(jobs, job_name):
    required = jobs[job_name].get("needs", [])
    if isinstance(required, str):
        required = [required]
    return set(required).union(*(ancestors(jobs, name) for name in required))


def test_latest_waits_for_the_existing_required_suite_on_the_published_artifact():
    jobs = workflow("docker-publish.yml")["jobs"]
    writers = [
        name
        for name, job in jobs.items()
        if any(":latest" in step.get("run", "") for step in job.get("steps", []))
    ]
    qualification = {
        name: job
        for name, job in jobs.items()
        if job.get("uses") == "./.github/workflows/pytest-unit-tests.yml"
    }
    assert len(qualification) == 1, "Release bypasses the existing required CI owner"
    gate_name, gate = next(iter(qualification.items()))
    assert "assemble" in ancestors(jobs, gate_name)
    assert gate["with"]["candidate_image"] == "${{ needs.assemble.outputs.image_ref }}"
    assert writers
    for name in writers:
        assert gate_name in ancestors(
            jobs, name
        ), "latest can move before qualification"
        assert "always()" not in jobs[name].get("if", "")


def test_latest_also_requires_the_existing_migration_gate():
    jobs = workflow("docker-publish.yml")["jobs"]
    migration = {
        name
        for name, job in jobs.items()
        if job.get("uses") == "./.github/workflows/alembic-migration-gate.yml"
    }
    assert len(migration) == 1
    assert migration <= ancestors(jobs, "publish-latest")
    config = workflow("alembic-migration-gate.yml")
    events = config.get("on", config.get(True))
    assert "workflow_call" in events


def test_release_qualification_uses_one_immutable_manifest_after_build():
    jobs = workflow("docker-publish.yml")["jobs"]
    assemble = jobs["assemble"]
    assert "build" in ancestors(jobs, "assemble")
    assert "image_ref" in assemble["outputs"]
    assert not any(":latest" in step.get("run", "") for step in assemble["steps"])
    exported = "\n".join(step.get("run", "") for step in assemble["steps"])
    assert "image_ref=${IMAGE_NAME}@${DIGEST}" in exported


def test_required_suite_accepts_a_release_artifact_without_rebuilding_it():
    config = workflow("pytest-unit-tests.yml")
    events = config.get("on", config.get(True))  # PyYAML's YAML 1.1 booleans.
    assert events["workflow_call"]["inputs"]["candidate_image"]["default"] == ""
    for job_name in ("integration-ci", "omnigent-exact-artifact"):
        steps = config["jobs"][job_name]["steps"]
        app_builds = [
            step
            for step in steps
            if step.get("uses", "").startswith("docker/build-push-action@")
            and step.get("with", {}).get("tags")
            in ("moonmind-first-run-3938:ci", "moonmind-exact-artifact:candidate")
        ]
        assert len(app_builds) == 1
        assert "inputs.candidate_image == ''" in app_builds[0]["if"]
        pulls = [
            step
            for step in steps
            if 'docker pull "$CANDIDATE_IMAGE"' in step.get("run", "")
        ]
        assert len(pulls) == 1
        assert pulls[0]["env"]["CANDIDATE_IMAGE"] == "${{ inputs.candidate_image }}"
        assert "inputs.candidate_image != ''" in pulls[0]["if"]


def test_all_installed_journeys_use_the_supplied_release_image():
    steps = workflow("pytest-unit-tests.yml")["jobs"]["integration-ci"]["steps"]
    journeys = [
        step
        for step in steps
        if "./tools/first_run_journey_3938.sh" in step.get("run", "")
    ]
    assert len(journeys) == 3
    for step in journeys:
        assert step["env"]["MOONMIND_IMAGE"] == (
            "${{ inputs.candidate_image || 'moonmind-first-run-3938:ci' }}"
        )


def test_nonrequired_rotating_soak_does_not_become_a_release_gate():
    job = workflow("pytest-unit-tests.yml")["jobs"]["omnigent-fault-rotating-seeds"]
    assert "inputs.candidate_image == ''" in job["if"]


@pytest.mark.skipif(sys.platform == "win32", reason="Release writer runs on Linux")
@pytest.mark.parametrize(
    ("ref", "main_head", "published"),
    [
        ("refs/heads/main", "candidate-commit", True),
        ("refs/heads/main", "newer-commit", False),
        ("refs/heads/feature", "candidate-commit", False),
    ],
)
def test_latest_writer_retags_the_qualified_manifest_and_preserves_order(
    tmp_path, ref, main_head, published
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "gh": 'printf "%s\\n" "$MAIN_HEAD"',
        "docker": 'printf "%s\\n" "$*" >> "$DOCKER_CALLS"',
    }.items():
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\n" + body + "\n")
        executable.chmod(0o755)
    calls = tmp_path / "docker-calls"
    image = "ghcr.io/example/moonmind"
    candidate = image + "@sha256:" + "a" * 64
    job = workflow("docker-publish.yml")["jobs"]["publish-latest"]
    writer = next(step for step in job["steps"] if "run" in step)
    result = subprocess.run(
        ["bash", "-c", writer["run"]],
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "MAIN_HEAD": main_head,
            "GITHUB_REF": ref,
            "GITHUB_SHA": "candidate-commit",
            "GITHUB_REPOSITORY": "example/moonmind",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "IMAGE_NAME": image,
            "CANDIDATE_IMAGE": candidate,
            "DOCKER_CALLS": str(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if published:
        assert calls.read_text().splitlines() == [
            f"buildx imagetools create -t {image}:latest {candidate}"
        ]
    else:
        assert not calls.exists()
