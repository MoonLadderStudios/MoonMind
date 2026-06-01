from moonmind.workflows.tasks.preset_goal_scheduler import (
    goal_from_payloads,
    schedule_preset_from_goal,
    task_is_already_authored,
)


def test_schedule_preset_from_goal_selects_jira_implement_for_issue_goal() -> None:
    schedule = schedule_preset_from_goal("Complete MM-747 from the roadmap.")

    assert schedule is not None
    assert schedule.slug == "jira-implement"
    assert schedule.version == "1.0.0"
    assert schedule.issue_key == "MM-747"
    assert schedule.inputs["jira_issue_key"] == "MM-747"


def test_schedule_preset_from_goal_selects_breakdown_orchestrate_for_story_goal() -> None:
    schedule = schedule_preset_from_goal(
        "Break down docs/Design.md into Jira stories for project TOOL."
    )

    assert schedule is not None
    assert schedule.slug == "jira-breakdown-orchestrate"
    assert schedule.inputs["feature_request"].startswith("Break down docs/Design.md")
    assert schedule.inputs["jira_project_key"] == "TOOL"
    assert schedule.inputs["publish_mode"] == "pr_with_merge_automation"


def test_schedule_preset_from_goal_defaults_to_moonspec_orchestrate() -> None:
    schedule = schedule_preset_from_goal("Add a repository dropdown to Create.")

    assert schedule is not None
    assert schedule.slug == "moonspec-orchestrate"
    assert schedule.inputs["feature_request"] == "Add a repository dropdown to Create."


def test_goal_scheduler_skips_authored_tasks() -> None:
    assert task_is_already_authored({"steps": [{"title": "Already selected"}]})
    assert task_is_already_authored({"taskTemplate": {"slug": "jira-implement"}})
    assert task_is_already_authored({"tool": {"id": "jira-issue-updater"}})


def test_goal_from_payloads_prefers_task_goal() -> None:
    assert (
        goal_from_payloads(
            task_payload={"goal": "task goal"},
            input_payload={"goal": "input goal"},
            parameter_payload={"goal": "parameter goal"},
        )
        == "task goal"
    )
