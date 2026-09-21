"""The Git author/committer identity MoonMind prepares repositories with.

Publication and the sandbox workspace an agent commits in resolve their
identity here, so a repository MoonMind materialized authors commits the
same way wherever the commit is created. The deployment already determines
this value, so an undeclared identity resolves to a documented default
instead of blocking a commit-capable run.
"""

from __future__ import annotations

DEFAULT_GIT_USER_NAME = "MoonMind Worker"
DEFAULT_GIT_USER_EMAIL = "moonmind-worker@users.noreply.github.com"


def resolve_git_identity() -> tuple[str, str]:
    """Return the configured commit identity, or the documented default."""

    from moonmind.config.settings import settings

    name = str(settings.workflow.git_user_name or "").strip() or DEFAULT_GIT_USER_NAME
    email = (
        str(settings.workflow.git_user_email or "").strip() or DEFAULT_GIT_USER_EMAIL
    )
    return name, email
