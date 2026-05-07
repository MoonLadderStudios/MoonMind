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
