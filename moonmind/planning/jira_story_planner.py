"""Skeleton for planning Jira stories from high level text."""

from __future__ import annotations

import logging
from typing import Any, Optional

from moonmind.config.settings import AppSettings


class JiraStoryPlanner:
    """Convert a plan description into Jira issues.

    Parameters
    ----------
    plan_text : str
        Text describing the overall plan or feature to implement.
    jira_project_key : str
        Key of the Jira project where issues should be created.
    dry_run : bool, optional
        If ``True``, no changes will be made in Jira. Defaults to ``True``.
    llm_model : Any, optional
        Language model used for text processing.
    logger : logging.Logger, optional
        Logger for debug and progress messages.
    **jira_kwargs : Any
        Additional arguments forwarded to the Jira client when implemented.
    """

    def __init__(
        self,
        plan_text: str,
        jira_project_key: str,
        dry_run: bool = True,
        llm_model: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
        **jira_kwargs: Any,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)

        if not plan_text:
            raise ValueError("plan_text is required")
        if not jira_project_key:
            raise ValueError("jira_project_key is required")

        self.plan_text = plan_text
        self.jira_project_key = jira_project_key
        self.dry_run = dry_run
        self.llm_model = llm_model
        self.jira_kwargs = jira_kwargs

        # Load Jira credentials from application settings
        settings = AppSettings()
        self.jira_url = settings.atlassian.atlassian_url
        self.jira_username = settings.atlassian.atlassian_username
        self.jira_api_key = settings.atlassian.atlassian_api_key

        # Placeholder for future Jira client
        self.jira_client = None

    def _build_prompt(self, plan_text: str) -> list:
        """Build LLM prompt messages from raw plan text.

        Parameters
        ----------
        plan_text : str
            The raw text describing the plan to convert into Jira stories.

        Returns
        -------
        list of Message
            A list containing the system and user messages ready for an LLM
            chat completion request.
        """
        from moonmind.schemas.chat_models import Message

        system_prompt = (
            "You are a Jira planning assistant. "
            "Return ONLY a JSON array of issues using the fields "
            "'summary', 'description', 'issue_type', 'story_points', and "
            "'labels'."
        )

        return [
            Message(role="system", content=system_prompt),
            Message(role="user", content=plan_text),
        ]
