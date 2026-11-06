from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
DOCKERFILE_PATH = REPO_ROOT / "api_service" / "Dockerfile"


def test_application_image_packages_an_independently_executable_controller(tmp_path):
    import shlex
    import shutil
    import subprocess
    import sys

    # Materialize the relevant image-owned COPY rather than importing the
    # checkout's controller by accident. Run without application dependencies.
    for line in DOCKERFILE_PATH.read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line)
        if (
            len(words) == 3
            and words[0] == "COPY"
            and words[2].rstrip("/") == "/app/deploy/controller"
        ):
            shutil.copytree(REPO_ROOT / words[1], tmp_path / "app/deploy/controller")
    controller = tmp_path / "app/deploy/controller"
    for script in ("bootstrap.py", "server.py"):
        result = subprocess.run(
            [sys.executable, "-S", str(controller / script), "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import server; assert 'active-journal-transition' in server.CONTROLLER_CAPABILITIES",
        ],
        cwd=controller,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_docker_publish_initializes_only_required_omnigent_submodule() -> None:
    # MoonSpec skills are vendored real files under .agents/skills, while the
    # runtime image embeds verifier sources from the pinned Omnigent revision.
    # Initialize that one required source without cloning unrelated submodules.
    workflow = _load_workflow()

    build_steps = workflow["jobs"]["build"]["steps"]
    checkout = next(
        (
            step
            for step in build_steps
            if step.get("uses", "").startswith("actions/checkout@")
        ),
        None,
    )

    assert checkout is not None, "Checkout step not found"
    assert "submodules" not in checkout.get("with", {})

    initialize = next(
        (
            step
            for step in build_steps
            if step.get("name") == "Initialize pinned Omnigent verifier source"
        ),
        None,
    )
    assert initialize is not None, "Pinned Omnigent initialization step not found"
    assert initialize["run"] == "git submodule update --init --depth 1 -- omnigent"


def test_app_image_ships_vendored_skills_without_moonspec_submodule() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY .agents /app/.agents/" in dockerfile
    assert "moonspec" not in dockerfile


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

    merge_job = next(job for job in workflow["jobs"].values() if "image_ref" in job.get("outputs", {}))
    assert {"metadata", "build"} <= set(merge_job.get("needs", []))

    merge_run_steps = [step["run"] for step in merge_job["steps"] if "run" in step]
    assert any(
        "${IMAGE_NAME}:" in run and "${{ needs.metadata.outputs.version_tag }}" in run
        for run in merge_run_steps
    )
    assert all("date +" not in run and "VERSION_TAG=" not in run for run in merge_run_steps)


def test_docker_publish_writes_build_summary_for_promotion() -> None:
    # The summary lets an operator copy the version tag and digest straight into
    # the Release / Promote Stable workflow.
    workflow = _load_workflow()
    merge_steps = next(job["steps"] for job in workflow["jobs"].values() if "image_ref" in job.get("outputs", {}))

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
    assert 'docker buildx imagetools inspect "${IMAGE_NAME}:${VERSION}"' in run
    assert "{{.Manifest.Digest}}" in run
    assert "${IMAGE_NAME}:${VERSION}@${DIGEST}" in run
    assert "Release / Promote Stable" in run
    assert "VERSION=\"${{ needs.metadata.outputs.version_tag }}\"" in run


def test_docker_publish_source_owner_checks_frontend_while_image_builds():
    workflow = _load_workflow()
    source = workflow["jobs"]["source-checks"]
    assert source["with"]["verification_phase"] == "source"
    assert not source.get("needs")
    assert not any(
        "npm run ui:test" in step.get("run", "")
        for step in workflow["jobs"]["build"]["steps"]
    )
    tests = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/pytest-unit-tests.yml").read_text()
    )
    assert any(
        "npm run frontend:ci" in step.get("run", "")
        for step in tests["jobs"]["frontend-static"]["steps"]
    )
    assert "run-generated-contracts" in tests["jobs"]


def test_missing_source_handoff_fails_before_artifact_download(tmp_path):
    import os
    import subprocess

    steps = _load_workflow()["jobs"]["verify-source-receipt"]["steps"]
    guard = next(
        step for step in steps
        if step.get("name") == "Require the selected source handoff before downloading"
    )
    download = next(
        step for step in steps
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert steps.index(guard) < steps.index(download)
    assert guard["env"]["SOURCE_ARTIFACT"] == download["with"]["name"]
    artifact, digest = "source-verification-123-1", "sha256:" + "a" * 64
    for handoff, accepted in [
        ({"SOURCE_ARTIFACT": "", "SOURCE_DIGEST": digest}, False),
        ({"SOURCE_ARTIFACT": artifact, "SOURCE_DIGEST": ""}, False),
        ({"SOURCE_ARTIFACT": artifact, "SOURCE_DIGEST": digest}, True),
    ]:
        result = subprocess.run(
            ["bash", "-eu", "-c", guard["run"]],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"], **handoff},
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) == accepted, result.stdout + result.stderr


def test_dockerfile_uses_buildkit_package_cache_mounts() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "RUN --mount=type=cache,target=/root/.npm" in dockerfile
    assert "RUN --mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "--mount=type=cache,target=/var/cache/apt,sharing=locked" in dockerfile
    assert "--mount=type=cache,target=/var/lib/apt/lists,sharing=locked" in dockerfile
    assert 'Binary::apt::APT::Keep-Downloaded-Packages "true";' in dockerfile


def test_docker_publish_reuses_layers_without_exporting_cache_mount_contents():
    steps = _load_workflow()["jobs"]["build"]["steps"]
    assert not any("cache-dance" in step.get("uses", "") for step in steps)
    build = next(
        step
        for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build["with"]["cache-from"].startswith("type=gha,scope=build-")
    assert build["with"]["cache-to"].startswith(build["with"]["cache-from"])
    assert build["with"]["outputs"].endswith("push=true")


def test_runtime_project_install_precedes_non_package_asset_copies() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    moonmind_copy_index = dockerfile.index("COPY moonmind /app/moonmind/")
    resolver_core_copy_index = dockerfile.index(
        "COPY pr_resolver_core /app/pr_resolver_core/"
    )
    project_install_index = dockerfile.index(
        "pip install --disable-pip-version-check --no-deps ."
    )

    assert moonmind_copy_index < project_install_index
    assert resolver_core_copy_index < project_install_index
    for copied_path in (
        "COPY api_service /app/api_service/",
        "COPY config /app/config/",
        "COPY .agents /app/.agents/",
    ):
        assert project_install_index < dockerfile.index(copied_path)
