"""MM-891: coverage for the code-improvement-proposal agent skill bundle."""

from pathlib import Path


def _skill_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "code-improvement-proposal"
        / "SKILL.md"
    )


def test_code_improvement_proposal_skill_bundle_exists_and_is_named() -> None:
    skill_path = _skill_path()
    assert skill_path.is_file()

    text = skill_path.read_text(encoding="utf-8")

    # Frontmatter name must match the bundle directory / skill id used by the worker.
    assert "name: code-improvement-proposal" in text
    assert text.lstrip().startswith("---")


def test_code_improvement_proposal_skill_declares_proposal_contract() -> None:
    text = _skill_path().read_text(encoding="utf-8")

    # Shares the post-run proposal input/output contract with its sibling hooks.
    assert "inputs.proposalOutputPath" in text
    assert "workflowCreateRequest" in text

    # Focused on code-level improvements, distinct from fix/continuation proposals.
    assert "code_improvement" in text
    assert "fix-proposal" in text
    assert "continuation-proposal" in text

    # Must keep the no-mutation / no-publish constraints of proposal skills.
    assert "Do not modify repository source files." in text
    assert "Do not commit or push." in text
