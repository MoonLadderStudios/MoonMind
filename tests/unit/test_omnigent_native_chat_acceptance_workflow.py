from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/omnigent-native-chat-acceptance.yml")


def test_native_chat_gate_downloads_prior_evidence_and_binds_candidate_commit() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    download = next(step for step in steps if step["name"] == "Download complete protected evidence")
    assert download["with"]["run-id"] == "${{ inputs.evidence_run_id }}"
    build = next(step for step in steps if step["name"] == "Build fail-closed native Chat report")
    assert "build_omnigent_native_chat_acceptance.py" in build["run"]
    assert '--expected-commit "${{ github.sha }}"' in build["run"]
    assert "--allow-partial" not in build["run"]


def test_issue_is_linked_only_after_passing_report_is_uploaded() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = [step["name"] for step in workflow["jobs"]["publish"]["steps"]]
    assert names.index("Upload passing report") < names.index("Link passing report from issue 3642")
