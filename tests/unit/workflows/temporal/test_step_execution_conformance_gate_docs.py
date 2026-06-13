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

DEGRADED_INPUT_CASE_TERMS = (
    ("old", "manifest", "rows"),
    ("old", "checkpoint", "rows"),
    ("old", "gate verdict", "strings"),
    ("legacy", "stateCheckpointRef", "rows"),
    ("future or unknown", "gate verdicts"),
    ("future or unknown", "checkpoint policy values"),
)

FORBIDDEN_INLINE_EVIDENCE_TERMS = (
    ("raw", "stdout"),
    ("raw", "stderr"),
    ("raw", "diffs"),
    ("raw", "logs"),
    ("provider", "payloads"),
    "credentials",
)

OUT_OF_SCOPE_PHASE_TERMS = (
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


def _assert_terms_present(guidance: str, required: str | tuple[str, ...]) -> None:
    terms = (required,) if isinstance(required, str) else required
    for term in terms:
        assert term in guidance


def test_pre_commit_guidance_requires_step_execution_conformance_gate() -> None:
    guidance = _read(PRE_COMMIT_DOC)

    assert CONFORMANCE_COMMAND in guidance
    assert FOCUSED_PYTEST_COMMAND in guidance
    for required in (
        ("MM-822+", "Step Execution PR"),
        "merge readiness",
        ("conformance suite", "run"),
        ("fixture update", "needed"),
    ):
        _assert_terms_present(guidance, required)


def test_step_execution_guidance_documents_fixture_extension_triggers() -> None:
    guidance = _read(STEP_EXECUTION_DOC)

    for trigger in FIXTURE_EXTENSION_TRIGGERS:
        assert trigger in guidance


def test_step_execution_guidance_preserves_degraded_input_gate() -> None:
    guidance = _durable_guidance()

    for degraded_case in DEGRADED_INPUT_CASE_TERMS:
        _assert_terms_present(guidance, degraded_case)

    for required in (
        "fail-closed",
        ("crashing", "replay"),
        ("silently", "passing"),
    ):
        _assert_terms_present(guidance, required)


def test_step_execution_guidance_requires_compact_ref_only_evidence() -> None:
    guidance = _durable_guidance()

    for required in ("artifact-backed", "ref-only", ("bounded", "summaries")):
        _assert_terms_present(guidance, required)
    for forbidden in FORBIDDEN_INLINE_EVIDENCE_TERMS:
        _assert_terms_present(guidance, forbidden)


def test_step_execution_guidance_keeps_phase_one_scope_boundary() -> None:
    guidance = _read(STEP_EXECUTION_DOC)

    assert "PR #2454" in guidance
    assert "manifest writer consolidation" in guidance
    assert "completed" in guidance
    assert "stale WP1" in guidance
    assert "Phase 1" in guidance
    assert "fixture extension triggers" in guidance
    for phase in OUT_OF_SCOPE_PHASE_TERMS:
        _assert_terms_present(guidance, phase)
