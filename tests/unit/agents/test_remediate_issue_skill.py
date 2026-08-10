from pathlib import Path


_SKILL_PATH = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "skills"
    / "remediate-issue"
    / "SKILL.md"
)


def test_remediate_issue_skill_accepts_issue_brief_and_materialized_verifier_evidence(
) -> None:
    text = _SKILL_PATH.read_text(encoding="utf-8")

    assert "issue brief" in text
    assert "gateResultPath" in text
    assert "remainingWorkPath" in text
    assert "without creating a MoonSpec feature packet" in text
    assert "exercise that real boundary" in text
    assert "Do not create a pull request" in text
