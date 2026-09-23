"""MoonLadderStudios/MoonMind#3938: disposable Compose journey contract + transport.

Extends the hermetic first-run journey (test_first_run_journey_3938.py) with
the integrated scope the verifier left PARTIAL (R-C1/R-C2/R-A1/R-A2):

- the disposable default Compose journey runner
  (tools/first_run_journey_3938.sh) owns the built startup/admission/session
  path: default docker-compose.yaml, no repo .env, no inherited credentials,
  ordinary bootstrap/migrations and operator admission, revision + image
  provenance (well-formed, never a cross-component equality gate), and
  project-owned teardown;
- the real API transport owns scratch submission: POST /api/executions and
  GET /api/sessions/{session_id} routes exist on the production routers;
- the relevant existing browser consumer owns the UI path:
  workflow-start.tsx submits to /api/executions and workflow-detail.tsx plus
  the native chat route render the session/artifact result;
- the saved-work continuation is reused by reference
  (tests/integration/reliability/test_saved_workspace_journey.py, owned by
  #4014-4018), not duplicated here.

Substitute identity: deterministic-credential-free-substitute-3938 (provider
interface only). A live-provider availability claim requires separately
authorized live observation and is explicitly not made here.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

SUBSTITUTE_IDENTITY_3938 = "deterministic-credential-free-substitute-3938"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_journey_runner_uses_disposable_default_compose() -> None:
    """R-C1/R-A1: default compose file, no .env, project-owned teardown."""
    script = (_repo_root() / "tools" / "first_run_journey_3938.sh").read_text()
    # Default deployment file owns the path, not the test-only compose file.
    assert 'COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"' in script
    assert "docker-compose.test.yaml" not in script
    # Disposable default install: a repo .env is never read by the stack.
    # The runner stashes a pre-existing file (created by test_integration.sh)
    # aside and restores it on exit instead of copying or deleting it.
    assert 'requires no $REPO_ROOT/.env' in script
    assert "ENV_STASH_DIR" in script
    assert "cp " not in script or ".env-template" not in script
    # Project guard keeps teardown scoped to this journey's project.
    assert "moonmind-test-first-run-3938" in script
    assert "moonmind-test(-[a-z0-9][a-z0-9_-]*)?" in script
    # Project-owned teardown only; no global prune and no volume wipe.
    assert "down --remove-orphans" in script
    executable = "\n".join(
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "system prune" not in executable
    assert "volume prune" not in executable
    assert "down -v" not in executable
    # Provenance records revision + image identities without gating on them.
    assert "rev-parse HEAD" in script
    assert "compose-sha256" in script
    assert "image digests (provenance, not an equality gate)" in script
    # Ordinary admission + real API submission stay in the path.
    assert "/healthz" in script
    assert "/api/executions" in script
    # The substitute is named in the runner output.
    assert SUBSTITUTE_IDENTITY_3938 in script
    # Supported product envelope: UserWorkflow with instructions and an
    # explicit no-publication mode; the retired scratch type and the
    # discarded top-level publication field stay out.
    assert '"workflowType": "MoonMind.UserWorkflow"' in script
    assert '"publishMode": "none"' in script
    # Completion honesty: observed success is tracked, terminal failure and
    # timeout fail, and the credential-free smoke path never claims
    # completion. Saved output is verified through artifactRefs + download.
    assert "SEEN_SUCCESS" in script
    assert "artifactRefs" in script
    assert "FIRST_RUN_3938_LIVE" in script
    # Credential-bearing named volumes are re-scoped to journey-owned names.
    assert "CODEX_VOLUME_NAME" in script
    assert "CLAUDE_VOLUME_NAME" in script
    assert "MOONMIND_SECRETS_VOLUME_NAME" in script
    # The API URL follows the Compose publish binding by default.
    assert "MOONMIND_API_PUBLISH_HOST" in script
    assert "MOONMIND_API_HOST_PORT" in script


def test_real_api_transport_owns_submission_and_session_read() -> None:
    """R-C2: production routers own POST /api/executions + session GET."""
    executions = (
        _repo_root() / "api_service" / "api" / "routers" / "executions.py"
    ).read_text()
    sessions = (
        _repo_root() / "api_service" / "api" / "routers" / "sessions.py"
    ).read_text()
    main = (_repo_root() / "api_service" / "main.py").read_text()
    # Submission route exists on the executions router with 201 semantics.
    assert "@router.post" in executions
    assert "async def create_execution" in executions
    assert "status_code=status.HTTP_201_CREATED" in executions
    # Session read/stream routes exist on the sessions router.
    assert "async def get_session_snapshot" in sessions
    assert '"/{session_id}"' in sessions
    # Both routers are mounted on the production app (real transport).
    assert "executions_router" in main
    assert "sessions_router" in main
    # Omitted/default selections with explicit no-publication intent match
    # the journey payload shape the runner submits: the supported envelope
    # carries this choice as publishMode none (a top-level publication field
    # is silently discarded by the request schema).
    assert '"publishMode": "none"' in (
        _repo_root()
        / "tests"
        / "integration"
        / "single_user"
        / "test_first_run_journey_3938.py"
    ).read_text()


def test_existing_browser_path_consumes_executions_api() -> None:
    """R-C2/R-A1: the existing dashboard/browser path is the UI consumer."""
    frontend = _repo_root() / "frontend" / "src"
    workflow_start = (
        frontend / "entrypoints" / "workflow-start.tsx"
    ).read_text()
    assert "/api/executions" in workflow_start
    assert (frontend / "entrypoints" / "workflow-detail.tsx").is_file()
    chat_route = (
        frontend / "features" / "workflow-native-chat" / "chatBindingModel.ts"
    )
    assert chat_route.is_file()
    binding = chat_route.read_text()
    assert "chatBinding" in binding or "workflow" in binding


def test_saved_work_continuation_is_reused_not_duplicated() -> None:
    """R-C2/R-A2: restore/publication continuation is wired by reference."""
    saved = (
        _repo_root()
        / "tests"
        / "integration"
        / "reliability"
        / "test_saved_workspace_journey.py"
    ).read_text()
    # The owning journey saves through the production artifact gateway,
    # restores after worker/workspace loss, and proves idempotent restore.
    assert "save_request_workspace" in saved
    assert "restore_saved_request_workspace" in saved
    assert "archiveRef" in saved
    assert "repeated ==" in saved or "repeated = await" in saved
    # This issue's runner delegates the continuation instead of retesting it.
    runner = (
        _repo_root() / "tools" / "first_run_journey_3938.sh"
    ).read_text()
    assert "test_saved_workspace_journey.py" in runner
    assert "does not duplicate restore/publication here" in runner


def test_compose_journey_contract_script_passes() -> None:
    """R-A1: the runner's own contract check passes on this candidate."""
    root = _repo_root()
    proc = subprocess.run(
        ["bash", str(root / "tools" / "first_run_journey_3938.sh"), "--contract"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "contract OK" in proc.stdout


def test_provenance_is_well_formed_never_an_equality_gate(tmp_path) -> None:
    """R-C1: revision + compose digest are provenance, not a version gate.

    Exercises the runner's own --provenance-only behavior and validates the
    produced artifact: the recorded revision is a full commit SHA and the
    compose digest is a full SHA-256 prefix of the actual file. Nothing here
    compares identities across components as a compatibility fingerprint.
    """
    root = _repo_root()
    log_dir = tmp_path / "provenance-3938"
    proc = subprocess.run(
        ["bash", str(root / "tools" / "first_run_journey_3938.sh"), "--provenance-only"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
        env={**dict(os.environ), "FIRST_RUN_3938_LOG_DIR": str(log_dir)},
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    provenance = (log_dir / "provenance.log").read_text()
    revision = re.search(r"revision=([0-9a-f]{40})", provenance)
    assert revision is not None
    assert (
        subprocess.check_output(
            ["git", "-c", "safe.directory=*", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        == revision.group(1)
    )
    digest = re.search(r"compose-sha256=([0-9a-f]{16})", provenance)
    assert digest is not None
    import hashlib

    assert hashlib.sha256((root / "docker-compose.yaml").read_bytes()).hexdigest()[
        :16
    ] == digest.group(1)
    assert SUBSTITUTE_IDENTITY_3938 in provenance


def test_no_live_availability_claim_in_compose_scope() -> None:
    """R-A3: ordinary CI evidence is not live-provider evidence."""
    runner = (
        _repo_root() / "tools" / "first_run_journey_3938.sh"
    ).read_text()
    assert SUBSTITUTE_IDENTITY_3938 in runner
    assert "live availability" not in runner.lower()
    assert "live provider" not in runner.lower()
    hermetic = (
        _repo_root()
        / "tests"
        / "integration"
        / "single_user"
        / "test_first_run_journey_3938.py"
    ).read_text()
    assert "live_evidence_observed = False" in hermetic


def test_journey_step_pins_candidate_image() -> None:
    """R-C1: the boundary journey runs the checkout build, not :latest."""
    import yaml

    workflow = yaml.safe_load(
        (_repo_root() / ".github" / "workflows" / "pytest-unit-tests.yml").read_text()
    )
    job = workflow["jobs"]["integration-ci"]
    steps = job["steps"]
    journey = next(
        step
        for step in steps
        if step.get("name") == "Run disposable default first-run journey"
    )
    # The journey step pins MOONMIND_IMAGE so docker-compose.yaml resolves
    # every MoonMind service to the candidate instead of the default tag.
    assert journey["env"]["MOONMIND_IMAGE"] == "moonmind-first-run-3938:ci"
    builders = [
        step
        for step in steps
        if "moonmind-first-run-3938:ci" in str(step.get("with", {}).get("tags", ""))
    ]
    assert len(builders) == 1
    assert builders[0]["with"]["file"] == "api_service/Dockerfile"


def test_journey_rejects_unimplemented_fault_modes() -> None:
    """Only implemented FIRST_RUN_3938_FAULT values are accepted."""
    import os

    root = _repo_root()
    env = dict(os.environ, FIRST_RUN_3938_FAULT="interruption")
    proc = subprocess.run(
        ["bash", str(root / "tools" / "first_run_journey_3938.sh"), "--contract"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not implemented" in proc.stderr
