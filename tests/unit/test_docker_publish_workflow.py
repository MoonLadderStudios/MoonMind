from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
DOCKERFILE_PATH = REPO_ROOT / "api_service" / "Dockerfile"
CLI_TOOLING_INSTALLER_PATH = (
    REPO_ROOT / "api_service" / "docker" / "install_cli_tooling.sh"
)

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


def test_docker_publish_does_not_require_submodules() -> None:
    # MoonSpec skills are vendored real files under .agents/skills, so the
    # image build must not depend on submodule checkout state.
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
    assert "submodules" not in checkout.get("with", {})


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


def test_docker_publish_frontend_checks_do_not_duplicate_production_build() -> None:
    workflow = _load_workflow()
    build_steps = workflow["jobs"]["build"]["steps"]

    frontend_step = next(
        (
            step
            for step in build_steps
            if step.get("name") == "Check frontend and generated contracts"
        ),
        None,
    )
    assert frontend_step, "Frontend and contracts check step not found"

    run = frontend_step["run"]
    assert "npm ci" in run
    assert "npm run ui:typecheck" in run
    assert "npm run ui:lint" in run
    assert "npm run ui:test" in run
    assert "npm run contracts:check" in run
    assert "npm run frontend:ci" not in run
    assert "npm run ui:build" not in run
    assert "npm run ui:build:check" not in run


def test_dockerfile_uses_buildkit_package_cache_mounts() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "RUN --mount=type=cache,target=/root/.npm" in dockerfile
    assert "RUN --mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "--mount=type=cache,target=/var/cache/apt,sharing=locked" in dockerfile
    assert "--mount=type=cache,target=/var/lib/apt/lists,sharing=locked" in dockerfile
    assert 'Binary::apt::APT::Keep-Downloaded-Packages "true";' in dockerfile
    assert (
        dockerfile.count('Binary::apt::APT::Keep-Downloaded-Packages "true";')
        == 3
    )


def test_docker_publish_persists_buildkit_cache_mounts() -> None:
    workflow = _load_workflow()
    build_steps = workflow["jobs"]["build"]["steps"]

    setup_buildx = next(
        (
            step
            for step in build_steps
            if step.get("uses", "").startswith("docker/setup-buildx-action@")
        ),
        None,
    )
    assert setup_buildx is not None, "Docker Buildx setup step not found"
    assert setup_buildx["id"] == "setup-buildx"

    cache_step = next(
        (
            step
            for step in build_steps
            if step.get("name") == "Cache BuildKit mount directories"
        ),
        None,
    )
    assert cache_step is not None, "BuildKit cache mount cache step not found"
    assert cache_step["uses"].startswith("actions/cache@")
    assert cache_step["id"] == "buildkit-cache"
    cache_with = cache_step["with"]
    assert cache_with["path"] == "${{ runner.temp }}/buildkit-cache-mounts"
    assert "api_service/Dockerfile" in cache_with["key"]
    assert "package-lock.json" in cache_with["key"]
    assert "poetry.lock" in cache_with["key"]
    assert "steps.meta.outputs.pair" in cache_with["key"]

    restore_step = next(
        (
            step
            for step in build_steps
            if step.get("name") == "Restore BuildKit cache mounts"
        ),
        None,
    )
    assert restore_step is not None, "BuildKit cache mount restore step not found"
    assert restore_step["uses"] == "reproducible-containers/buildkit-cache-dance@v3"
    restore_with = restore_step["with"]
    assert restore_with["builder"] == "${{ steps.setup-buildx.outputs.name }}"
    assert restore_with["cache-dir"] == "${{ runner.temp }}/buildkit-cache-mounts"
    assert restore_with["dockerfile"] == "api_service/Dockerfile"
    assert restore_with["skip-extraction"] == "${{ steps.buildkit-cache.outputs.cache-hit }}"


def test_runtime_project_install_precedes_non_package_asset_copies() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    moonmind_copy_index = dockerfile.index("COPY moonmind /app/moonmind/")
    project_install_index = dockerfile.index(
        "pip install --disable-pip-version-check --no-deps ."
    )

    assert moonmind_copy_index < project_install_index
    for copied_path in (
        "COPY api_service /app/api_service/",
        "COPY config /app/config/",
        "COPY docs/ReleaseNotes /app/docs/ReleaseNotes/",
        "COPY .agents /app/.agents/",
    ):
        assert project_install_index < dockerfile.index(copied_path)


def test_cli_tooling_installer_keeps_npm_cache_by_default() -> None:
    installer = CLI_TOOLING_INSTALLER_PATH.read_text(encoding="utf-8")

    assert "${CLEAN_NPM_CACHE:-false}" in installer
    assert "npm cache clean --force || true" in installer
    assert installer.index("${CLEAN_NPM_CACHE:-false}") < installer.index(
        "npm cache clean --force || true"
    )
