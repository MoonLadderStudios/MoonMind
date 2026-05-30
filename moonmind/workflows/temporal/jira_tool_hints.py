"""Managed-runtime prompt hints for MoonMind trusted Jira tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.workflows.agent_skills.selection import selected_agent_skill
from moonmind.workflows.temporal.jira_agent_skills import JIRA_AGENT_SKILLS


def append_selected_jira_tool_hint(
    instructions: str,
    *,
    parameters: Mapping[str, Any] | None,
) -> str:
    """Append trusted Jira tool instructions for Jira-oriented agent skills."""

    params = parameters if isinstance(parameters, Mapping) else {}
    selected_skill = selected_agent_skill(params)
    if selected_skill not in JIRA_AGENT_SKILLS:
        return instructions
    if "MoonMind trusted Jira tools" in instructions:
        return instructions
    tool_lines = [
        "- Use the internal MoonMind API from the managed session via "
        "`$MOONMIND_URL` for Jira operations; do not look for raw Jira "
        "credentials in the shell.",
        "- List available tools with `GET $MOONMIND_URL/mcp/tools`.",
        "- Invoke Jira tools with `POST $MOONMIND_URL/mcp/tools/call`.",
    ]
    if selected_skill == "jira-issue-creator":
        story_breakdown_path = str(params.get("storyBreakdownPath") or "").strip()
        if story_breakdown_path:
            tool_lines.insert(
                0,
                f"- Read MoonSpec story candidates from `{story_breakdown_path}`.",
            )
        tool_lines.extend(
            [
                "- Example create-metadata call: "
                '`{"tool":"jira.list_create_issue_types",'
                '"arguments":{"projectKey":"<PROJECT_KEY>"}}`.',
                "- Resolve the Story issue type through "
                "`jira.list_create_issue_types` and create issues through "
                "`jira.create_issue`.",
                "- Treat the task as blocked if Jira tool calls are unavailable "
                "or no Jira issue key is returned.",
            ]
        )
    elif selected_skill == "jira-issue-updater":
        tool_lines.extend(
            [
                "- Fetch the Jira issue through `jira.get_issue` when current "
                + "fields or status are needed.",
                "- For status changes, call `jira.get_transitions`, match the "
                + "target status against transition names or target statuses, "
                + "then call `jira.transition_issue` with Jira's returned ID.",
                "- Example transitions call: "
                + '`{"tool":"jira.get_transitions",'
                + '"arguments":{"issueKey":"<ISSUE_KEY>",'
                + '"expandFields":true}}`.',
                "- Example transition call: "
                + '`{"tool":"jira.transition_issue",'
                + '"arguments":{"issueKey":"<ISSUE_KEY>",'
                + '"transitionId":"<TRANSITION_ID>"}}`.',
                "- Treat the task as blocked if trusted Jira tool calls are "
                + "unavailable, the transition is denied, or no matching "
                + "transition exists.",
            ]
        )
    elif selected_skill == "jira-pr-verify":
        tool_lines.extend(
            [
                "- Fetch the Jira issue body through `jira.get_issue` before "
                "verifying PR coverage.",
                "- Example issue fetch call: "
                + '`{"tool":"jira.get_issue",'
                + '"arguments":{"issueKey":"<ISSUE_KEY>"}}`.',
                "- Treat the task as blocked if trusted Jira tool calls are "
                "unavailable or the issue fetch is denied.",
            ]
        )
    else:
        tool_lines.extend(
            [
                "- Fetch the Jira issue body through `jira.get_issue` before "
                "verifying branch coverage.",
                "- Example issue fetch call: "
                + '`{"tool":"jira.get_issue",'
                + '"arguments":{"issueKey":"<ISSUE_KEY>"}}`.',
                "- Inspect the current branch against its base, classify the "
                "result as PASS, PARTIAL, FAIL, or BLOCKED, and summarize "
                "the branch evidence without pasting long private Jira text.",
                "- Post the verification result through `jira.add_comment` "
                "after scanning the comment body for secrets.",
                "- Example comment call: "
                + '`{"tool":"jira.add_comment",'
                + '"arguments":{"issueKey":"<ISSUE_KEY>",'
                + '"body":"<COMMENT_TEXT>"}}`.',
                "- Treat the task as blocked if trusted Jira tool calls are "
                "unavailable, the issue fetch is denied, or comment posting "
                "fails.",
            ]
        )
    return (
        instructions.rstrip()
        + "\n\nMoonMind trusted Jira tools:\n"
        + "\n".join(tool_lines)
    )
