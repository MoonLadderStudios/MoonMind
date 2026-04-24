"""Sanitized Jira integration error types."""

from __future__ import annotations

class JiraToolError(RuntimeError):
    """Structured Jira tool error safe for logs and model-visible responses."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "jira_request_failed",
        status_code: int = 502,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.action = action

    def __str__(self) -> str:
        if self.action:
            return f"Jira tool error ({self.action}): {self.args[0]}"
        return f"Jira tool error: {self.args[0]}"

