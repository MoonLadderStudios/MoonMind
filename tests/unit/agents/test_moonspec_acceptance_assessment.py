from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
BUNDLE_DIR = REPO_ROOT / "moonspec" / "bundle"
PRESET_PATH = REPO_ROOT / "api_service" / "data" / "presets" / "moonspec-orchestrate.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mm1135_specify_emits_source_acceptance_matrix() -> None:
    text = _read(SKILLS_DIR / "moonspec-specify" / "SKILL.md")

    assert "## Source Acceptance Matrix" in text
    assert "artifacts/moonspec/source-acceptance.json" in text
    assert '"schemaVersion": "v1"' in text
    assert "SRC-AC-*" in text
    assert "SRC-SURFACE-*" in text
    assert "SRC-NEG-*" in text
    assert "SRC-TEST-*" in text
    assert "repoVerifiable: false" in text
    assert "statusHint" in text


def test_mm1135_assess_skill_defines_verdicts_statuses_and_backlog() -> None:
    text = _read(SKILLS_DIR / "moonspec-assess" / "SKILL.md")
    agent = _read(SKILLS_DIR / "moonspec-assess" / "agents" / "openai.yaml")

    assert "name: moonspec-assess" in text
    assert "artifacts/moonspec/source-acceptance.json" in text
    assert "artifacts/moonspec/acceptance-assessment.json" in text
    for verdict in (
        "FULLY_IMPLEMENTED",
        "PARTIALLY_IMPLEMENTED",
        "NOT_IMPLEMENTED",
        "BLOCKED",
        "NO_DETERMINATION",
    ):
        assert verdict in text
    for status in (
        "VERIFIED",
        "PARTIAL",
        "MISSING",
        "CONFLICT",
        "UNVERIFIED",
        "OUT_OF_SCOPE",
    ):
        assert status in text
    assert "boundedBacklog" in text
    assert "Do not choose `FULLY_IMPLEMENTED`" in text
    assert "MoonSpec Assess" in agent


def test_mm1135_orchestrate_preset_runs_assessment_without_new_inputs() -> None:
    preset = yaml.safe_load(_read(PRESET_PATH))

    input_names = {item["name"] for item in preset["inputs"]}
    assert input_names == {
        "feature_request",
        "source_design_path",
        "constraints",
        "run_verify",
    }

    skill_ids = [step["skill"]["id"] for step in preset["steps"]]
    assert "moonspec-assess" in skill_ids
    assert skill_ids.index("moonspec-specify") < skill_ids.index("moonspec-assess")
    assert skill_ids.index("moonspec-assess") < skill_ids.index("moonspec-plan")

    assess_step = next(
        (step for step in preset["steps"] if step["skill"]["id"] == "moonspec-assess"),
        None,
    )
    assert (
        assess_step is not None
    ), "Step with skill ID 'moonspec-assess' not found in preset steps"
    assert "source-acceptance.json" in assess_step["instructions"]
    assert "acceptance-assessment.json" in assess_step["instructions"]
    assert "bounded backlog" in assess_step["instructions"]


def test_mm1135_downstream_skills_consume_assessment_artifacts() -> None:
    expected = {
        "moonspec-orchestrate": [
            "moonspec-assess",
            "The original source acceptance matrix",
            "A single story may span multiple implementation surfaces",
        ],
        "moonspec-plan": [
            "artifacts/moonspec/source-acceptance.json",
            "artifacts/moonspec/acceptance-assessment.json",
            "Treat `VERIFIED` source acceptance rows as regression constraints",
        ],
        "moonspec-tasks": [
            "## Source Acceptance Coverage",
            "Every missing, partial, conflict, or required-unverified row",
            "Each negative constraint row",
        ],
        "moonspec-implement": [
            "boundedBacklog",
            "authoritative implementation backlog",
            "Do not add unrelated improvements unless they are required by a source acceptance row",
        ],
        "moonspec-verify": [
            "## Source Acceptance Matrix Verification",
            "Do not choose `FULLY_IMPLEMENTED`",
            "every repo-verifiable source row",
        ],
    }

    for skill, snippets in expected.items():
        text = _read(SKILLS_DIR / skill / "SKILL.md")
        for snippet in snippets:
            assert snippet in text, f"Snippet '{snippet}' not found in {skill}/SKILL.md"


def test_mm1135_bundle_manifest_exports_assessment_assets() -> None:
    manifest = yaml.safe_load(_read(BUNDLE_DIR / "moonspec.bundle.yaml"))

    skill_ids = {item["id"] for item in manifest["exports"]["skills"]}
    command_ids = {item["id"] for item in manifest["exports"]["commands"]}
    doc_ids = {item["id"] for item in manifest["exports"]["docs"]}

    assert "moonspec-assess" in skill_ids
    assert "moonspec.assess" in command_ids
    assert "acceptance-assessment" in doc_ids


def test_mm1135_docs_describe_provider_neutral_protocol() -> None:
    text = _read(BUNDLE_DIR / "docs" / "MoonSpecAcceptanceAssessment.md")

    assert "artifacts/moonspec/source-acceptance.json" in text
    assert "artifacts/moonspec/acceptance-assessment.json" in text
    assert "External systems can provide a matrix" in text
    assert "Jira, GitHub, and MoonMind adapters" in text
    assert "must not become required coupling" in text
    assert "Vertical Multi-Surface Story" in text
    assert "Negative Constraints" in text
