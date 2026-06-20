from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"

def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _pentest_steps() -> list[dict]:
    return _load_workflow()["jobs"]["build-pentestgpt"]["steps"]


def _pentest_step(name: str) -> dict:
    step = next((s for s in _pentest_steps() if s.get("name") == name), None)
    assert step is not None, f"Step {name!r} not found in build-pentestgpt"
    return step

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


def test_runner_publish_validates_provenance_labels() -> None:
    # MM-839: CI must fail when the runner image is missing provenance labels.
    step = _pentest_step("Validate runner provenance labels")
    assert "linux/amd64" in step.get("if", "")
    run = step["run"]
    for label in (
        "org.opencontainers.image.source",
        "org.opencontainers.image.version",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.title",
    ):
        assert label in run, f"provenance label {label} not validated"
    # The check must fail the build (not merely warn) when a label is missing.
    assert "exit 1" in run


def test_runner_publish_scans_image_with_configured_vulnerability_threshold() -> None:
    # MM-839: CI must fail on configured vulnerability-threshold breaches.
    workflow = _load_workflow()
    assert "PENTEST_RUNNER_VULN_SEVERITY_THRESHOLD" in workflow["env"]

    step = _pentest_step("Scan runner image for vulnerabilities")
    assert "linux/amd64" in step.get("if", "")
    assert str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
    with_inputs = step["with"]
    # exit-code 1 fails the build on a threshold breach.
    assert str(with_inputs.get("exit-code")) == "1"
    assert "PENTEST_RUNNER_VULN_SEVERITY_THRESHOLD" in str(with_inputs.get("severity"))
    assert with_inputs.get("image-ref")


def test_runner_publish_validates_selftest_report_output() -> None:
    # MM-839: CI must fail on invalid self-test report-output content, not just
    # on the runner exit code.
    step = _pentest_step("Validate runner self-test report output")
    assert "linux/amd64" in step.get("if", "")
    run = step["run"]
    assert "--self-test" in run
    assert '"self_test"' in run
    for token in ("report.primary", "report.structured", "no_canary_leak", "redaction_ok"):
        assert token in run, f"self-test validation does not check {token}"
    assert "exit 1" in run or "SystemExit(1)" in run


def test_runner_selftest_report_is_passed_without_clobbering_stdin() -> None:
    # MM-839 regression: a heredoc is the program source on stdin for python, so
    # piping the report into `python3 - <<'PY'` discards it and json.loads sees an
    # empty string. The report must be passed via the environment instead.
    step = _pentest_step("Validate runner self-test report output")
    run = step["run"]
    assert "REPORT_LINE=" in run, "report JSON must be passed via the environment"
    assert 'os.environ["REPORT_LINE"]' in run
    assert "sys.stdin.read()" not in run, "report must not be read from clobbered stdin"
    assert "python3 - <<" not in run, "do not feed the program on stdin while piping data"
