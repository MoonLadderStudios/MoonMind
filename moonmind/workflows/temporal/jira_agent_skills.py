"""Shared Jira-backed agent skill identifiers for Temporal runtime paths."""

JIRA_AGENT_SKILLS = frozenset(
    {"jira-issue-creator", "jira-issue-updater", "jira-pr-verify", "jira-verify"}
)

JIRA_BACKED_AGENT_SKILLS = frozenset(
    {
        "jira-orchestrate",
        *JIRA_AGENT_SKILLS,
    }
)

__all__ = ["JIRA_AGENT_SKILLS", "JIRA_BACKED_AGENT_SKILLS"]
