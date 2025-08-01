"""Skeleton for planning Jira stories from high level text."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from moonmind.config.settings import AppSettings
from moonmind.factories.google_factory import get_google_model


class JiraStoryPlannerError(Exception):
    """Raised for errors during LLM planning or JSON parsing."""


class StoryDraft(BaseModel):
    summary: str = Field(..., description="Short summary of the story")
    description: str = Field(..., description="Detailed description")
    issue_type: str = Field(..., description="Jira issue type")
    story_points: Optional[int] = Field(None, description="Story points estimate")
    labels: List[str] = Field(default_factory=list, description="Labels to apply")
    key: Optional[str] = Field(None, description="Created Jira issue key")


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
    include_story_points : bool, optional
        If ``False``, the planner will not attempt to assign story point values
        to created issues. Defaults to ``True``.
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
        include_story_points: bool = True,
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
        self.include_story_points = include_story_points
        self.jira_kwargs = jira_kwargs

        # Load Jira credentials from application settings
        settings = AppSettings()
        self.jira_url = settings.atlassian.atlassian_url
        self.jira_username = settings.atlassian.atlassian_username
        self.jira_api_key = settings.atlassian.atlassian_api_key

        if not self.dry_run and (
            not self.jira_url or not self.jira_username or not self.jira_api_key
        ):
            raise JiraStoryPlannerError(
                "ATLASSIAN_API_KEY, ATLASSIAN_USERNAME and ATLASSIAN_URL must be configured"
            )

        # Placeholder for future Jira client
        self.jira_client = None
        # Last token usage info returned by the LLM, if available
        self.last_token_usage: Optional[Any] = None

    def _build_prompt(self, plan_text: str) -> list:
        """Build LLM prompt messages from raw plan text.

        Parameters
        ----------
        plan_text : str
            The raw text describing the plan to convert into Jira stories.
            If the plan already lists individual stories, those entries should
            be treated as the issues to create with any available context
            appended.

        Returns
        -------
        list of Message
            A list containing the system and user messages ready for an LLM
            chat completion request.
        """
        from moonmind.schemas.chat_models import Message

        fields = ["summary", "description", "issue_type"]
        if self.include_story_points:
            fields.append("story_points")
        fields.append("labels")

        if self.include_story_points:
            field_list = ", ".join(f"'{f}'" for f in fields[:-1])
            field_list += f", and '{fields[-1]}'"
        else:
            field_list = ", ".join(f"'{f}'" for f in fields)

        system_prompt = (
            "You are a Jira planning assistant. "
            "If the plan already includes specific stories, use those as the "
            "issues to create and simply add any provided context to each. "
            "Return ONLY a JSON array of issues using the fields "
            f"{field_list}."
        )

        return [
            Message(role="system", content=system_prompt),
            Message(role="user", content=plan_text),
        ]

    def _call_llm(self, prompt: list) -> List[StoryDraft]:
        """Send prompt to the LLM and parse the JSON response.

        Parameters
        ----------
        prompt : list
            List of chat messages generated by ``_build_prompt``.

        Returns
        -------
        List[StoryDraft]
            Validated list of story drafts returned by the LLM.

        Raises
        ------
        JiraStoryPlannerError
            If the LLM call fails or the response is not valid JSON.
        """
        model = self.llm_model
        if model is None:
            try:
                model = get_google_model()
            except (
                ImportError,
                ValueError,
            ) as e:  # pragma: no cover - expected failure types
                self.logger.exception("Failed to initialize LLM model: %s", e)
                raise JiraStoryPlannerError(
                    f"Failed to initialize LLM model: {e}"
                ) from e

        try:
            gemini_prompt = []
            for msg in prompt:
                role = msg.role
                if role == "assistant":
                    role = "model"
                elif role not in {"user", "model"}:
                    role = "user"
                gemini_prompt.append({"role": role, "parts": [msg.content]})

            response = model.generate_content(gemini_prompt)
            # Capture token usage information if available on the response
            self.last_token_usage = getattr(response, "usage", None)
        except Exception as e:
            self.logger.exception("LLM generation error: %s", e)
            raise JiraStoryPlannerError(f"LLM generation error: {e}") from e

        response_text: Optional[str] = None
        try:
            if hasattr(response, "candidates") and response.candidates:
                first_candidate = response.candidates[0]
                if (
                    getattr(first_candidate, "content", None)
                    and first_candidate.content.parts
                ):
                    text_parts = [
                        part.text
                        for part in first_candidate.content.parts
                        if hasattr(part, "text") and part.text
                    ]
                    if text_parts:
                        response_text = "".join(text_parts)

            if not response_text and hasattr(response, "text"):
                response_text = response.text
        except Exception as e:  # pragma: no cover - unexpected failure structure
            self.logger.exception("Error extracting text from LLM response: %s", e)
            raise JiraStoryPlannerError(f"Invalid LLM response format: {e}") from e

        if not response_text:
            raise JiraStoryPlannerError("LLM returned no text content")

        try:
            parsed = self._extract_json(response_text)
        except Exception as e:
            self.logger.exception("Invalid JSON from LLM: %s", e)
            raise JiraStoryPlannerError(f"Invalid JSON from LLM: {e}") from e

        try:
            return [StoryDraft.model_validate(item) for item in parsed]
        except Exception as e:
            self.logger.exception("Story validation failed: %s", e)
            raise JiraStoryPlannerError(f"Story validation failed: {e}") from e

    def _extract_json(self, text: str) -> Any:
        """Extract JSON data from a text response.

        Parameters
        ----------
        text : str
            Raw text returned from the LLM which should contain JSON.

        Returns
        -------
        Any
            Parsed JSON object.

        Raises
        ------
        json.JSONDecodeError
            If no valid JSON could be parsed.
        """
        try:
            return json.loads(text)
        except Exception:
            pass

        import re

        code_match = re.search(
            r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text
        )
        if code_match:
            return json.loads(code_match.group(1))

        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            return json.loads(array_match.group(0))

        raise json.JSONDecodeError("Invalid JSON", text, 0)

    def _get_jira_client(self, **overrides: Any):
        """Initialize and authenticate a Jira client.

        Parameters
        ----------
        **overrides : Any
            Optional keyword arguments that override default client configuration.

        Returns
        -------
        atlassian.Jira
            Authenticated Jira client instance.

        Raises
        ------
        JiraStoryPlannerError
            If authentication fails.
        """
        from atlassian import Jira

        if not self.jira_url or not self.jira_username or not self.jira_api_key:
            raise JiraStoryPlannerError(
                "ATLASSIAN_API_KEY, ATLASSIAN_USERNAME and ATLASSIAN_URL must be configured"
            )

        config = {
            "url": self.jira_url,
            "username": self.jira_username,
            "password": self.jira_api_key,
            "cloud": True,
            "backoff_and_retry": True,
            "max_backoff_seconds": 16,
            "max_backoff_retries": 3,
        }
        config.update(overrides)

        try:
            client = Jira(**config)
            client.myself()  # Trigger authentication
        except Exception:  # pragma: no cover - network/credential errors
            # Avoid logging credentials from the exception message.
            self.logger.exception("Jira authentication failed")
            raise JiraStoryPlannerError(
                f"Failed to authenticate with Jira at {self.jira_url}"
            ) from None

        return client

    def _resolve_story_points_field(self, jira_client: Any) -> str:
        """Return the custom field id used for story points.

        Parameters
        ----------
        jira_client : Any
            Authenticated Jira client.

        Returns
        -------
        str
            The custom field id for story points.

        Raises
        ------
        JiraStoryPlannerError
            If the field cannot be located.
        """

        if getattr(self, "_story_points_field_id", None):
            return self._story_points_field_id  # type: ignore[attr-defined]

        try:
            fields = jira_client.get_all_fields()
        except Exception as e:  # pragma: no cover - network errors
            self.logger.exception("Failed to retrieve Jira fields: %s", e)
            raise JiraStoryPlannerError(f"Failed to retrieve Jira fields: {e}") from e

        for field in fields:
            name = str(field.get("name", "")).lower()
            field_type = field.get("schema", {}).get("type")
            if name == "story points" and field_type == "number":
                field_id = field.get("id")
                if field_id:
                    self._story_points_field_id = field_id
                    return field_id

        raise JiraStoryPlannerError("Story points field not found")

    def _create_issues(self, drafts: List[StoryDraft]) -> List[StoryDraft]:
        """Create Jira issues from the given drafts.

        Parameters
        ----------
        drafts : List[StoryDraft]
            Draft issue objects to create in Jira.

        Returns
        -------
        List[StoryDraft]
            Drafts updated with their created issue keys.
        """

        if not drafts:
            return []

        if self.dry_run:
            return drafts

        jira = self.jira_client or self._get_jira_client(**self.jira_kwargs)
        story_points_field = None
        if self.include_story_points:
            story_points_field = self._resolve_story_points_field(jira)

        created: List[StoryDraft] = []

        for batch_start in range(0, len(drafts), 50):
            batch = drafts[batch_start : batch_start + 50]
            issue_updates = []
            for draft in batch:
                fields = {
                    "project": {"key": self.jira_project_key},
                    "summary": draft.summary,
                    "description": draft.description,
                    "issuetype": {"name": draft.issue_type},
                }
                if (
                    self.include_story_points
                    and story_points_field
                    and draft.story_points is not None
                ):
                    fields[story_points_field] = draft.story_points
                if draft.labels:
                    fields["labels"] = draft.labels
                issue_updates.append({"fields": fields})

            # Attempt bulk creation with retries on 429. Some Jira clients
            # expose `issue_create_bulk` while others use `create_issues`.
            bulk_method = None
            if hasattr(jira, "issue_create_bulk"):
                bulk_method = jira.issue_create_bulk
            elif hasattr(jira, "create_issues"):
                bulk_method = jira.create_issues

            attempts = 0
            bulk_resp: Any = None
            if bulk_method:
                while attempts < 3:
                    try:
                        bulk_resp = bulk_method(issue_updates)
                        break  # Success
                    except Exception as e:
                        if (
                            getattr(e, "status", None) == 429
                            or getattr(e, "status_code", None) == 429
                            or "429" in str(e)
                        ):
                            time.sleep(2**attempts)
                            attempts += 1
                            continue

                        # For other errors, log and break to fallback
                        error_msg = f"Bulk issue creation failed: {e}"
                        if hasattr(e, "response") and e.response is not None:
                            try:
                                error_msg += f" - {e.response.text}"
                            except Exception:
                                pass
                        self.logger.warning(error_msg)
                        bulk_resp = None
                        break

            if bulk_resp is None:
                # Fall back to creating issues one-by-one if bulk failed or was skipped
                bulk_resp = []

            bulk_issues = []
            if isinstance(bulk_resp, dict):
                bulk_issues = bulk_resp.get("issues", [])
            elif isinstance(bulk_resp, list):
                bulk_issues = bulk_resp

            for idx, draft in enumerate(batch):
                key = None
                if idx < len(bulk_issues) and isinstance(bulk_issues[idx], dict):
                    key = bulk_issues[idx].get("key")
                if not key:
                    try:
                        single_attempts = 0
                        while True:
                            try:
                                single_resp = jira.create_issue(
                                    fields=issue_updates[idx]["fields"]
                                )
                                if isinstance(single_resp, dict):
                                    key = single_resp.get("key")
                                break
                            except Exception as e:  # pragma: no cover - network errors
                                if (
                                    getattr(e, "status", None) == 429
                                    or getattr(e, "status_code", None) == 429
                                    or "429" in str(e)
                                ) and single_attempts < 3:
                                    time.sleep(2**single_attempts)
                                    single_attempts += 1
                                    continue

                                error_text = str(e).lower()
                                # If story points are the problem, retry without them.
                                if (
                                    self.include_story_points
                                    and "customfield_10028" in error_text
                                    and story_points_field
                                    in issue_updates[idx]["fields"]
                                ):
                                    self.logger.warning(
                                        f"Failed to set story points for issue {draft.summary}. Retrying without story points."
                                    )
                                    del issue_updates[idx]["fields"][story_points_field]
                                    single_attempts += 1  # count as a retry
                                    continue

                                # If issue type is the problem, try a sequence of fallbacks.
                                if (
                                    "issuetype" in error_text
                                    or "issue type" in error_text
                                ):
                                    original_type = draft.issue_type
                                    current_type = issue_updates[idx]["fields"][
                                        "issuetype"
                                    ]["name"]
                                    fallbacks = ["Task", "Story", "Bug"]

                                    # Find the next fallback type that hasn't been tried
                                    next_type = None
                                    if current_type == original_type:
                                        next_type = fallbacks[0]
                                    else:
                                        try:
                                            current_index = fallbacks.index(
                                                current_type
                                            )
                                            if current_index + 1 < len(fallbacks):
                                                next_type = fallbacks[current_index + 1]
                                        except ValueError:
                                            # current_type is not in our standard fallbacks, start from the beginning
                                            next_type = fallbacks[0]

                                    if next_type:
                                        self.logger.warning(
                                            f"Failed to create issue '{draft.summary}' with type '{current_type}'. Retrying as '{next_type}'."
                                        )
                                        issue_updates[idx]["fields"]["issuetype"][
                                            "name"
                                        ] = next_type
                                        single_attempts += 1
                                        continue

                                # If we've exhausted retries, raise to be caught by the outer block
                                raise
                    except Exception as final_error:
                        self.logger.error(
                            f"Could not create issue for draft '{draft.summary}' after all retries: {final_error}"
                        )

                draft.key = key
                created.append(draft)

        return created

    def plan(self) -> List[StoryDraft]:
        """Execute planning and issue creation with structured logging."""

        self.last_token_usage = None
        start_time = time.perf_counter()
        prompt = self._build_prompt(self.plan_text)
        prompt_str = "".join(f"{m.role}:{m.content}" for m in prompt)
        prompt_hash = hashlib.sha256(prompt_str.encode()).hexdigest()

        drafts = self._call_llm(prompt)
        created = self._create_issues(drafts)

        latency = time.perf_counter() - start_time
        self.logger.info(
            "jira_story_planner.completed",
            extra={
                "prompt_hash": prompt_hash,
                "token_usage": self.last_token_usage,
                "created_issue_keys": [d.key for d in created if d.key],
                "latency": latency,
            },
        )
        return created
