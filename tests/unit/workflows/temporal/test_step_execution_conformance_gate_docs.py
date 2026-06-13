from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
PRE_COMMIT_DOC = REPO_ROOT / "docs" / "Development" / "PreCommitWorkflow.md"
STEP_EXECUTION_DOC = (
    REPO_ROOT / "docs" / "Steps" / "StepExecutionsAndCheckpointing.md"
)

CONFORMANCE_COMMAND = "python -m moonmind.workflows.temporal.step_execution_conformance"
FOCUSED_PYTEST_COMMAND = (
    "pytest tests/unit/workflows/temporal/test_step_executions.py "
    "tests/unit/workflows/temporal/test_step_checkpoints.py "
    "tests/integration/workflows/temporal/test_step_execution_manifest_evidence.py -q"
)

FIXTURE_EXTENSION_TRIGGERS = (
    "checkpoint capture artifact",
    "checkpoint-backed Resume",
    "typed gate result",
    "context bundle or retrieval manifest",
    "provider-profile lease side effect",
    "Docker or skill-projection blocked environment",
)

DEGRADED_INPUT_CASES = (
    "old manifest rows",
    "old checkpoint rows",
    "old gate verdict strings",
    "legacy `stateCheckpointRef`-only rows",
    "future or unknown gate verdicts",
    "future or unknown checkpoint policy values",
)

FORBIDDEN_INLINE_EVIDENCE = (
    "raw stdout",
    "raw stderr",
    "raw diffs",
    "raw logs",
    "provider payloads",
    "credentials",
)

OUT_OF_SCOPE_PHASES = (
    "checkpoint substrate",
    "checkpoint-backed Resume",
    "workspace policy",
    "terminal side-effect",
    "typed gate",
    "context",
    "memory",
    "UI",
    "autonomous-loop",
    "final-doc",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _durable_guidance() -> str:
    return f"{_read(PRE_COMMIT_DOC)}\n{_read(STEP_EXECUTION_DOC)}"


def test_pre_commit_guidance_requires_step_execution_conformance_gate() -> None:
    guidance = _read(PRE_COMMIT_DOC)

    assert CONFORMANCE_COMMAND in guidance
    assert FOCUSED_PYTEST_COMMAND in guidance
    assert "MM-822+" in guidance
    assert "Step Execution PR" in guidance
    assert "merge readiness" in guidance
    assert "conformance suite was run" in guidance
    assert "no fixture update was needed" in guidance


def test_step_execution_guidance_documents_fixture_extension_triggers() -> None:
    guidance = _read(STEP_EXECUTION_DOC)

    for trigger in FIXTURE_EXTENSION_TRIGGERS:
        assert trigger in guidance


def test_step_execution_guidance_preserves_degraded_input_gate() -> None:
    guidance = _durable_guidance()

    for degraded_case in DEGRADED_INPUT_CASES:
        assert degraded_case in guidance

    assert "fail-closed" in guidance
    assert "rather than crashing replay" in guidance
    assert "silently passing" in guidance


def test_step_execution_guidance_requires_compact_ref_only_evidence() -> None:
    guidance = _durable_guidance()

    assert "artifact-backed" in guidance
    assert "ref-only" in guidance
    assert "bounded summaries" in guidance
    for forbidden in FORBIDDEN_INLINE_EVIDENCE:
        assert forbidden in guidance


def test_step_execution_guidance_keeps_phase_one_scope_boundary() -> None:
    guidance = _read(STEP_EXECUTION_DOC)

    assert "PR #2454" in guidance
    assert "manifest writer consolidation" in guidance
    assert "completed" in guidance
    assert "stale WP1" in guidance
    assert "Phase 1" in guidance
    assert "fixture extension triggers" in guidance
    for phase in OUT_OF_SCOPE_PHASES:
        assert phase in guidance
