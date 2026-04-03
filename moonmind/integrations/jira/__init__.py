"""Trusted Jira integration primitives for managed-agent tools."""

from moonmind.integrations.jira.auth import (
    ResolvedJiraConnection,
    resolve_jira_connection,
)
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.tool import JiraToolService

__all__ = [
    "JiraClient",
    "JiraToolError",
    "JiraToolService",
    "ResolvedJiraConnection",
    "resolve_jira_connection",
]
