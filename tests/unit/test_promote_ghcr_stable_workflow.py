from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "promote-ghcr-stable.yml"


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _workflow_on(workflow: dict) -> dict:
    # PyYAML parses the bare ``on:`` key as the boolean ``True``.
    return workflow.get("on") or workflow.get(True)


def _promote_steps() -> list[dict]:
    return _load_workflow()["jobs"]["promote"]["steps"]


def _promote_step(name: str) -> dict:
    step = next((s for s in _promote_steps() if s.get("name") == name), None)
    assert step is not None, f"Step {name!r} not found in promote job"
    return step


def test_promote_workflow_is_manual_dispatch_only_with_typed_inputs() -> None:
    # Promotion is a deliberate human action, not an automatic build trigger.
    workflow = _load_workflow()
    triggers = _workflow_on(workflow)

    assert set(triggers.keys()) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]

    image = inputs["image"]
    assert image["type"] == "choice"
    assert image["required"] is True
    assert image["options"] == ["app", "pentestgpt-runner"]

    source_tag = inputs["source_tag"]
    assert source_tag["type"] == "string"
    assert source_tag["required"] is True

    channel = inputs["channel"]
    assert channel["type"] == "choice"
    assert channel["required"] is True
    assert channel["default"] == "stable"
    assert channel["options"] == ["stable"]

    expected_digest = inputs["expected_digest"]
    assert expected_digest["type"] == "string"
    assert expected_digest["required"] is False

    codex_result_path = inputs["codex_conformance_result_path"]
    assert codex_result_path["type"] == "string"
    assert codex_result_path["required"] is False

    codex_artifact_name = inputs["codex_conformance_artifact_name"]
    assert codex_artifact_name["type"] == "string"
    assert codex_artifact_name["required"] is False

    codex_run_id = inputs["codex_conformance_run_id"]
    assert codex_run_id["type"] == "string"
    assert codex_run_id["required"] is False


def test_promote_workflow_uses_minimal_permissions_and_serial_concurrency() -> None:
    workflow = _load_workflow()

    assert workflow["permissions"] == {
        "actions": "read",
        "contents": "read",
        "packages": "write",
    }
    assert workflow["concurrency"] == {
        "group": "promote-ghcr-${{ inputs.image }}-${{ inputs.channel }}",
        "cancel-in-progress": False,
    }


def test_promote_job_is_gated_by_required_reviewer_environment() -> None:
    # The ghcr-stable environment provides the required-reviewer guardrail so a
    # stable move cannot happen by accident.
    promote_job = _load_workflow()["jobs"]["promote"]
    assert promote_job["environment"] == "ghcr-stable"
    assert promote_job["runs-on"] == "ubuntu-latest"


def test_promote_resolves_image_name_for_both_targets() -> None:
    run = _promote_step("Resolve image name")["run"]
    assert "tr '[:upper:]' '[:lower:]'" in run
    assert 'image="ghcr.io/${owner}/${repo}"' in run
    assert 'image="ghcr.io/${owner}/${repo}-pentestgpt"' in run
    # An unknown image selection must fail rather than silently promote.
    assert "Unknown image" in run
    assert "exit 64" in run


def test_promote_resolves_source_digest_with_optional_guard() -> None:
    step = _promote_step("Resolve source digest")
    run = step["run"]
    assert 'docker buildx imagetools inspect "${source_ref}"' in run
    assert "--format '{{.Manifest.Digest}}'" in run
    # Free-form dispatch inputs are routed through env vars so their values are
    # never re-parsed as shell during ${{ }} expansion (script-injection guard).
    assert step["env"]["SOURCE_TAG"] == "${{ inputs.source_tag }}"
    assert step["env"]["EXPECTED_DIGEST"] == "${{ inputs.expected_digest }}"
    assert "${{ inputs.source_tag }}" not in run
    assert "${{ inputs.expected_digest }}" not in run
    assert 'source_ref="${image}:${SOURCE_TAG}"' in run
    # Optional expected-digest guard: only enforced when provided, fails on drift.
    assert 'expected="${EXPECTED_DIGEST}"' in run
    assert "Digest mismatch" in run
    assert 'echo "digest=${digest}" >> "$GITHUB_OUTPUT"' in run


def test_app_promotion_requires_codex_conformance_result_for_exact_digest() -> None:
    install = _promote_step("Install promotion gate dependencies")
    assert install["if"] == "inputs.image == 'app'"
    assert "pydantic" in install["run"]

    require_run_id = _promote_step("Require Codex conformance run id for artifact download")
    assert require_run_id["if"].startswith("inputs.image == 'app'")
    assert "codex_conformance_run_id is required" in require_run_id["run"]

    download_prior = _promote_step("Download prior-run Codex conformance artifact")
    assert download_prior["with"]["run-id"] == "${{ inputs.codex_conformance_run_id }}"
    assert download_prior["with"]["github-token"] == "${{ secrets.GITHUB_TOKEN }}"

    step = _promote_step("Validate Codex conformance result")
    assert step["if"] == "inputs.image == 'app'"
    assert step["env"]["RESULT_PATH"] == "${{ inputs.codex_conformance_result_path }}"
    run = step["run"]
    assert "find /tmp/codex-conformance -name canary-result.json" in run
    assert "codex_conformance_result_path or codex_conformance_artifact_name" in run
    assert "Codex conformance result not found" in run
    assert "python -m moonmind.codex_conformance.canary check" in run
    assert '--candidate-digest "${{ steps.source.outputs.digest }}"' in run
    assert "--max-age-hours 72" in run


def test_promote_retags_existing_manifest_without_rebuilding() -> None:
    # Promotion must be a registry-side retag of the resolved digest, never a
    # rebuild; the channel tag and an audit marker are created together.
    run = _promote_step("Promote manifest")["run"]
    assert "docker buildx imagetools create" in run
    assert '-t "${image}:${channel}"' in run
    assert '-t "${image}:${channel}-${source_tag}"' in run
    assert '"${image}@${digest}"' in run
    assert "build-push-action" not in run
    # `docker buildx ...` is allowed; a plain `docker build ` rebuild is not.
    assert "docker build " not in run


def test_promote_verifies_and_summarizes_result() -> None:
    run = _promote_step("Verify promoted image and write summary")["run"]
    assert 'docker buildx imagetools inspect "${image}:${channel}"' in run
    assert "GITHUB_STEP_SUMMARY" in run
    assert "Digest-pinned stable reference" in run
    assert "${image}:${channel}@${promoted_digest}" in run
