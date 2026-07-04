from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
DOCKERFILE_PATH = REPO_ROOT / "api_service" / "Dockerfile"

def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

def test_docker_publish_generates_version_before_platform_builds() -> None:
    workflow = _load_workflow()

    metadata_job = workflow["jobs"]["metadata"]
    assert (
        metadata_job.get("outputs", {}).get("version_tag")
        == "${{ steps.meta.outputs.version_tag }}"
    )

    metadata_run = next(
        (
            step["run"]
            for step in metadata_job.get("steps", [])
            if step.get("name") == "Generate image metadata" and "run" in step
        ),
        None,
    )
    assert metadata_run, "Step 'Generate image metadata' with 'run' command not found"
    assert "date -u +'%Y%m%d'" in metadata_run
    assert "github.run_number" in metadata_run
    assert "version_tag=${VERSION_TAG}" in metadata_run

    build_job = workflow["jobs"]["build"]
    assert build_job["needs"] == "metadata"


def test_docker_publish_checks_out_moonspec_submodule() -> None:
    workflow = _load_workflow()

    checkout = next(
        (
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ),
        None,
    )

    assert checkout is not None, "Checkout step not found"
    assert checkout["with"]["submodules"] == "recursive"


def test_app_image_build_validates_moonspec_bundle_content() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    copy_index = dockerfile.index("COPY moonspec /app/moonspec/")
    guard_index = dockerfile.index("/app/moonspec/bundle/moonspec.bundle.yaml")

    assert copy_index < guard_index
    assert "/app/moonspec/bundle/skills/moonspec-verify/SKILL.md" in dockerfile
    assert "/app/.agents/skills/moonspec-verify/SKILL.md" in dockerfile


def test_docker_publish_passes_manifest_tag_into_image_build_metadata() -> None:
    workflow = _load_workflow()

    build_steps = workflow["jobs"]["build"]["steps"]
    build_push_step = next(
        (step for step in build_steps if step.get("name") == "Build and push by digest"),
        None,
    )
    assert (
        build_push_step and "with" in build_push_step
    ), "Step 'Build and push by digest' not found or missing 'with' key"
    build_args = build_push_step["with"].get("build-args", "")
    assert "MOONMIND_BUILD_ID=${{ needs.metadata.outputs.version_tag }}" in build_args

    merge_job = workflow["jobs"]["merge"]
    assert set(merge_job.get("needs", [])) == {"metadata", "build"}

    merge_run_steps = [step["run"] for step in merge_job["steps"] if "run" in step]
    assert any(
        "${IMAGE_NAME}:" in run and "${{ needs.metadata.outputs.version_tag }}" in run
        for run in merge_run_steps
    )
    assert all("date +" not in run and "VERSION_TAG=" not in run for run in merge_run_steps)


def test_docker_publish_writes_build_summary_for_promotion() -> None:
    # The app build mirrors the PentestGPT runner summary so an operator can copy
    # the version tag / digest straight into the Promote GHCR image to stable
    # workflow. `latest` stays the automatic current-build channel.
    workflow = _load_workflow()
    merge_steps = workflow["jobs"]["merge"]["steps"]

    summary_step = next(
        (
            step
            for step in merge_steps
            if "run" in step and "GITHUB_STEP_SUMMARY" in step["run"]
        ),
        None,
    )
    assert summary_step, "merge job must write a build summary to GITHUB_STEP_SUMMARY"

    run = summary_step["run"]
    assert 'docker buildx imagetools inspect "${IMAGE_NAME}:latest"' in run
    assert "{{.Manifest.Digest}}" in run
    assert "${IMAGE_NAME}:${VERSION}@${DIGEST}" in run
    assert "Promote GHCR image to stable" in run
    assert "VERSION=\"${{ needs.metadata.outputs.version_tag }}\"" in run


def test_docker_publish_no_longer_builds_pentestgpt_runner() -> None:
    # MM-867: PentestGPT runner publishing lives in pentestgpt-runner.yml so
    # runner-only changes do not publish the app image.
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    metadata_outputs = jobs["metadata"].get("outputs", {})

    assert "PENTEST_RUNNER_VULN_SEVERITY_THRESHOLD" not in workflow.get("env", {})
    assert "pentest_image_name" not in metadata_outputs
    assert "build-pentestgpt" not in jobs
    assert "merge-pentestgpt" not in jobs
