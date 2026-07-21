from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/omnigent-embedded-acceptance.yml")


def test_embedded_acceptance_workflow_uses_prior_durable_matrix_and_fail_closed_builder() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    download = next(step for step in steps if step["name"] == "Download complete matrix evidence")
    assert download["with"]["run-id"] == "${{ inputs.evidence_run_id }}"
    build = next(step for step in steps if step["name"] == "Build protected acceptance report")
    assert "build_omnigent_embedded_acceptance.py" in build["run"]
    assert "--allow-partial" not in build["run"]


def test_issue_link_can_only_run_after_report_upload() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = [step["name"] for step in workflow["jobs"]["publish"]["steps"]]
    assert names.index("Upload passing report") < names.index("Link passing report from issue 3425")
