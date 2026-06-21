from moonmind.workflows.executions.preset_goal_scheduler import (
    goal_from_payloads,
    schedule_preset_from_goal,
    workflow_is_already_authored,
)


def test_schedule_preset_from_goal_selects_jira_implement_for_issue_goal() -> None:
    schedule = schedule_preset_from_goal("Complete MM-747 from the roadmap.")

    assert schedule is not None
    assert schedule.slug == "jira-implement"
    assert schedule.version == "1.1.0"
    assert schedule.issue_key == "MM-747"
    assert schedule.inputs["jira_issue_key"] == "MM-747"


def test_schedule_preset_from_goal_accepts_lowercase_jira_issue_key() -> None:
    schedule = schedule_preset_from_goal("Complete mm-747 from the roadmap.")

    assert schedule is not None
    assert schedule.slug == "jira-implement"
    assert schedule.issue_key == "MM-747"
    assert schedule.inputs["jira_issue_key"] == "MM-747"


def test_schedule_preset_from_goal_selects_github_issue_implement_for_issue_url() -> None:
    schedule = schedule_preset_from_goal(
        "Implement https://github.com/MoonLadderStudios/MoonMind/issues/123"
    )

    assert schedule is not None
    assert schedule.slug == "github-issue-implement"
    assert schedule.version == "1.0.0"
    assert schedule.issue_key == "MoonLadderStudios/MoonMind#123"
    assert schedule.inputs["github_issue"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 123,
    }


def test_schedule_preset_from_goal_selects_breakdown_orchestrate_for_story_goal() -> None:
    schedule = schedule_preset_from_goal(
        "Break down docs/Design.md into Jira stories for project TOOL."
    )

    assert schedule is not None
    assert schedule.slug == "jira-breakdown-orchestrate"
    assert schedule.inputs["feature_request"].startswith("Break down docs/Design.md")
    assert schedule.inputs["jira_project_key"] == "TOOL"
    assert schedule.inputs["publish_mode"] == "pr_with_merge_automation"


def test_schedule_preset_from_goal_does_not_treat_runtime_names_as_code_keywords() -> None:
    schedule = schedule_preset_from_goal(
        "Orchestrate Jira issue MM-747 with claude_code."
    )

    assert schedule is not None
    assert schedule.slug == "jira-orchestrate"


def test_schedule_preset_from_goal_matches_split_as_a_word() -> None:
    schedule = schedule_preset_from_goal("Split docs/Design.md into Jira stories.")

    assert schedule is not None
    assert schedule.slug == "jira-breakdown-orchestrate"


def test_schedule_preset_from_goal_defaults_to_moonspec_orchestrate() -> None:
    schedule = schedule_preset_from_goal("Add a repository dropdown to Create.")

    assert schedule is not None
    assert schedule.slug == "moonspec-orchestrate"
    assert schedule.inputs["feature_request"] == "Add a repository dropdown to Create."


def test_goal_scheduler_skips_authored_tasks() -> None:
    assert workflow_is_already_authored({"steps": [{"title": "Already selected"}]})
    assert workflow_is_already_authored({"taskTemplate": {"slug": "jira-implement"}})
    assert workflow_is_already_authored({"tool": {"id": "jira-issue-updater"}})


def test_goal_from_payloads_prefers_task_goal() -> None:
    assert (
        goal_from_payloads(
            task_payload={"goal": "task goal"},
            input_payload={"goal": "input goal"},
            parameter_payload={"goal": "parameter goal"},
        )
        == "task goal"
    )
