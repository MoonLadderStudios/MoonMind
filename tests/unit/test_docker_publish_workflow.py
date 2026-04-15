from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"


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
