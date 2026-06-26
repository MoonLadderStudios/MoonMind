import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
TEMPLATES_DIR = REPO_ROOT / ".specify" / "templates"


def test_mm933_specify_records_docs_native_source_packet() -> None:
    text = (SKILLS_DIR / "moonspec-specify" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    template = (TEMPLATES_DIR / "spec-template.md").read_text(encoding="utf-8")

    for required in (
        "## Source Packet",
        "**Artifact Role**",
        "**Source Document**",
        "**Document Class**",
        "**Viewpoint**",
        "**Owning Surface**",
        "**Stable Claim IDs**",
        "**Source Issue Traceability**",
    ):
        assert required in text
        assert required in template

    assert "temporary adapter" in text
    assert "CLAIM-001" in text
    assert "MM-933" in text
    assert "MM-927" in text


def test_mm933_plan_preserves_source_packet_for_task_generation() -> None:
    text = (SKILLS_DIR / "moonspec-plan" / "SKILL.md").read_text(encoding="utf-8")

    assert "source document path" in text
    assert "document class" in text
    assert "viewpoint" in text
    assert "owning surface" in text
    assert "stable claim IDs" in text
    assert "temporary-adapter role" in text
    assert "MM-933" in text
    assert "MM-927" in text
    assert "No stable source claim applies: <reason>" in text


def test_mm933_tasks_require_claim_mapping_or_explicit_explanation() -> None:
    text = (SKILLS_DIR / "moonspec-tasks" / "SKILL.md").read_text(encoding="utf-8")
    template = (TEMPLATES_DIR / "tasks-template.md").read_text(encoding="utf-8")

    for required in (
        "source document path",
        "viewpoint",
        "owning surface",
        "stable claim IDs",
        "temporary-adapter role",
        "MM-933",
        "MM-927",
        "Every task must map to stable claim IDs",
        "No source claim applies: <reason>",
        "moonspec-doc-reconcile",
    ):
        assert required in text

    assert "Each task MUST reference the relevant stable claim IDs" in template
    assert "No source claim applies: <reason>" in template
    assert "Source Packet" in template
    assert "moonspec-doc-reconcile" in template


def test_mm933_task_template_examples_map_implementation_tasks_to_claims() -> None:
    template = (TEMPLATES_DIR / "tasks-template.md").read_text(encoding="utf-8")

    assert (
        "### Implementation" in template
    ), "tasks-template.md is missing '### Implementation' section"
    assert (
        "**Checkpoint**" in template
    ), "tasks-template.md is missing '**Checkpoint**' marker"
    implementation_section = template.split("### Implementation", 1)[1].split(
        "**Checkpoint**", 1
    )[0]
    task_lines = [
        line for line in implementation_section.splitlines() if line.startswith("- [ ]")
    ]

    assert task_lines
    for line in task_lines:
        has_claim_id = re.search(r"\b(?:CLAIM|DESIGN-REQ|DOC-REQ)-\d{3}\b", line)
        has_explanation = "No source claim applies:" in line
        assert has_claim_id or has_explanation, line
