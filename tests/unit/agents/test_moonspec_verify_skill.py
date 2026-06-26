from pathlib import Path


def test_moonspec_verify_skill_includes_projection_contamination_preflight() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Workspace Projection Preflight" in text
    assert "test ! -L .agents/skills" in text
    assert "test ! -L .gemini/skills" in text
    assert "git status --porcelain -- .agents/skills .gemini/skills skills_active" in text
    assert "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION" in text
    assert "verdict `BLOCKED`" in text
    assert "`recoverableInCurrentRuntime: false`" in text
    assert "`recommendedNextAction: blocked`" in text
    assert "stop with verdict `ADDITIONAL_WORK_NEEDED`" not in text


def test_moonspec_verify_skill_reports_canonical_claim_coverage() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Canonical Claim Coverage" in text
    assert "Code Evidence" in text
    assert "Test Evidence" in text
    assert "Artifact Evidence" in text
    assert "Implementation Status" in text
    assert "Verification Status" in text
    assert "Drift Status" in text
    assert "Gap Reason" in text
    assert "Implementation gaps mean" in text
    assert "Verification gaps mean" in text
    assert "Doc drift means" in text
    assert "Doc drift alone does not block `FULLY_IMPLEMENTED`" in text
    assert "Use durable evidence references" in text
    assert "structured drift in Source Document Drift" in text
