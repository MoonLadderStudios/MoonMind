"""Sanitized GitLab integration error types."""

from __future__ import annotations


class GitLabToolError(RuntimeError):
    """Structured GitLab tool error safe for logs and model-visible responses."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "gitlab_request_failed",
        status_code: int = 502,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.action = action

    def __str__(self) -> str:
        if self.action:
            return f"GitLab tool error ({self.action}): {self.args[0]}"
        return f"GitLab tool error: {self.args[0]}"


class GitLabIdentityError(GitLabToolError):
    """Fail-closed endpoint/project/MR identity or redirect failure."""

    def __init__(self, message: str, *, action: str | None = None) -> None:
        super().__init__(
            message,
            code="gitlab_identity_rejected",
            status_code=403,
            action=action,
        )


class GitLabTokenExpiredError(GitLabToolError):
    """The admitted credential expired (401); distinct from denial (403)."""

    def __init__(self, message: str, *, action: str | None = None) -> None:
        super().__init__(
            message,
            code="gitlab_token_expired",
            status_code=401,
            action=action,
        )


class GitLabCapabilityUnavailable(GitLabToolError):
    """An unimplemented operation (inline review, merge, resolver) was requested."""

    def __init__(self, message: str, *, action: str | None = None) -> None:
        super().__init__(
            message,
            code="gitlab_capability_unavailable",
            status_code=501,
            action=action,
        )
