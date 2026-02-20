"""Business logic for task proposal queue operations."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

import httpx

from moonmind.config import settings
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
    WorkerAuthPolicy,
)
from moonmind.workflows.task_proposals.models import (
    TaskProposal,
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.repositories import (
    TaskProposalNotFoundError,
    TaskProposalRepository,
)

logger = logging.getLogger(__name__)
_PROPOSALS_WRITE_CAPABILITY = "proposals_write"
_NOTIFICATION_CATEGORIES = {"security", "tests"}
_DEDUP_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_MOONMIND_SIGNAL_TAGS = frozenset(
    {
        "retry",
        "duplicate_output",
        "missing_ref",
        "conflicting_instructions",
        "flaky_test",
        "loop_detected",
        "artifact_gap",
    }
)


class TaskProposalError(RuntimeError):
    """Base error for task proposal operations."""


class TaskProposalValidationError(TaskProposalError):
    """Raised when proposal input values are invalid."""


class TaskProposalStatusError(TaskProposalError):
    """Raised when proposal state does not permit the requested action."""


class TaskProposalService:
    """Application service that validates proposal actions."""

    def __init__(
        self,
        repository: TaskProposalRepository,
        queue_service: AgentQueueService,
        *,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._repository = repository
        self._queue_service = queue_service
        self._redactor = redactor or SecretRedactor.from_environ(
            placeholder="[REDACTED]"
        )
        proposal_settings = getattr(settings, "task_proposals", None)
        self._notification_webhook = (
            getattr(proposal_settings, "notifications_webhook_url", None)
            if proposal_settings
            else None
        )
        self._notification_authorization = (
            getattr(proposal_settings, "notifications_authorization", None)
            if proposal_settings
            else None
        )
        timeout = (
            getattr(proposal_settings, "notifications_timeout_seconds", 5)
            if proposal_settings
            else 5
        )
        self._notification_timeout = max(1, int(timeout or 5))
        enabled_flag = (
            getattr(proposal_settings, "notifications_enabled", False)
            if proposal_settings
            else False
        )
        self._notifications_enabled = bool(enabled_flag) and bool(
            self._notification_webhook
        )
        self._similar_limit = 10
        self._moonmind_repository = (
            str(getattr(settings.task_proposals, "moonmind_ci_repository", "") or "")
            .strip()
            .lower()
        )

    async def resolve_worker_token(self, raw_token: str | None) -> WorkerAuthPolicy:
        """Validate worker token capability for proposals_write."""

        if not raw_token:
            raise TaskProposalValidationError(
                "worker token is required for worker-authenticated proposal submission"
            )
        policy = await self._queue_service.resolve_worker_token(raw_token)
        if _PROPOSALS_WRITE_CAPABILITY not in policy.capabilities:
            raise TaskProposalValidationError(
                "worker token is not authorized for proposal submission"
            )
        return policy

    @staticmethod
    def _clean_str(value: object) -> str:
        return str(value).strip() if value is not None else ""

    def _scrub_text(self, text: str) -> str:
        return self._redactor.scrub(text)

    def _scrub_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False)
        scrubbed = self._redactor.scrub(serialized)
        return json.loads(scrubbed)

    @staticmethod
    def _slugify_title(title: str) -> str:
        normalized = _DEDUP_SLUG_PATTERN.sub("-", title.lower()).strip("-")
        return normalized or "untitled"

    def _compute_dedup_fields(self, *, repository: str, title: str) -> tuple[str, str]:
        repo = (repository or "").strip().lower() or "unknown"
        slug = self._slugify_title(title or "")
        dedup_key = f"{repo}:{slug}"
        dedup_hash = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
        return dedup_key[:512], dedup_hash

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _normalize_category(self, value: object) -> str | None:
        text = self._clean_str(value).lower()
        if not text:
            return None
        if len(text) > 64:
            raise TaskProposalValidationError("category exceeds max length")
        return text

    def _normalize_tags(
        self, values: list[object] | tuple[object, ...] | None
    ) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            candidate = self._clean_str(raw).lower()
            if not candidate or candidate in seen:
                continue
            if len(candidate) > 64:
                raise TaskProposalValidationError("tag exceeds max length")
            normalized.append(candidate)
            seen.add(candidate)
        return normalized

    def _is_moonmind_repository(self, repository: str) -> bool:
        if not repository:
            return False
        return repository.strip().lower() == self._moonmind_repository

    def _normalize_moonmind_title(self, title: str, tags: Sequence[str]) -> str:
        normalized = title.strip()
        if not normalized.lower().startswith("[run_quality]"):
            normalized = f"[run_quality] {normalized or 'MoonMind proposal'}".strip()
        slug_items = sorted({tag for tag in tags if tag})
        if slug_items:
            slug_text = "+".join(slug_items)
            marker = f"(tags: {slug_text})"
            if marker not in normalized:
                normalized = f"{normalized} (tags: {slug_text})"
        return normalized

    def _enforce_moonmind_policy(
        self,
        *,
        title: str,
        category: str | None,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> tuple[str, list[str], str]:
        normalized_category = (category or "run_quality").lower()
        if normalized_category == "moonmind_ci":
            normalized_category = "run_quality"
        if normalized_category != "run_quality":
            raise TaskProposalValidationError(
                "MoonMind proposals must use category 'run_quality'"
            )
        allowed_tags = [tag for tag in tags if tag in _MOONMIND_SIGNAL_TAGS]
        if not allowed_tags:
            raise TaskProposalValidationError(
                "MoonMind proposals require at least one approved signal tag"
            )
        trigger_repo = self._clean_str(metadata.get("triggerRepo"))
        trigger_job = self._clean_str(metadata.get("triggerJobId"))
        signal_payload = metadata.get("signal")
        if not trigger_repo or not trigger_job:
            raise TaskProposalValidationError(
                "MoonMind proposals must include triggerRepo and triggerJobId metadata"
            )
        if not isinstance(signal_payload, dict):
            raise TaskProposalValidationError(
                "MoonMind proposals must provide origin_metadata.signal details"
            )
        metadata["triggerRepo"] = trigger_repo
        metadata["triggerJobId"] = trigger_job
        metadata["signal"] = dict(signal_payload)
        normalized_title = self._normalize_moonmind_title(title, allowed_tags)
        return normalized_category, allowed_tags, normalized_title

    @staticmethod
    def _priority_rank(value: TaskProposalReviewPriority) -> int:
        ordering = {
            TaskProposalReviewPriority.LOW: 0,
            TaskProposalReviewPriority.NORMAL: 1,
            TaskProposalReviewPriority.HIGH: 2,
            TaskProposalReviewPriority.URGENT: 3,
        }
        return ordering.get(value, 1)

    def _derive_moonmind_priority(
        self, tags: Sequence[str], metadata: Mapping[str, Any]
    ) -> tuple[TaskProposalReviewPriority | None, str | None]:
        signal = metadata.get("signal")
        signal_dict = signal if isinstance(signal, Mapping) else {}
        severity = self._clean_str(signal_dict.get("severity")).lower()
        if severity in {"high", "critical"}:
            return TaskProposalReviewPriority.HIGH, "signal:severity"
        tag_set = {tag for tag in tags}
        if "loop_detected" in tag_set:
            return TaskProposalReviewPriority.HIGH, "signal:loop_detected"
        if "conflicting_instructions" in tag_set:
            return TaskProposalReviewPriority.HIGH, "signal:conflicting_instructions"
        if "missing_ref" in tag_set:
            missing_refs = signal_dict.get("missingRefs") or signal_dict.get(
                "missing_refs"
            )
            if isinstance(missing_refs, (list, tuple)) and missing_refs:
                return TaskProposalReviewPriority.HIGH, "signal:missing_ref"
        if "retry" in tag_set:
            retries = signal_dict.get("retries")
            try:
                retry_count = int(retries)
            except (TypeError, ValueError):
                retry_count = 0
            if retry_count >= 2:
                return TaskProposalReviewPriority.HIGH, "signal:retry_exhausted"
            return TaskProposalReviewPriority.NORMAL, "signal:retry"
        if "duplicate_output" in tag_set or "artifact_gap" in tag_set:
            return TaskProposalReviewPriority.NORMAL, "signal:quality_gap"
        if "flaky_test" in tag_set:
            return TaskProposalReviewPriority.LOW, "signal:flaky_test"
        return None, None

    def _normalize_origin_source(self, raw_source: object) -> TaskProposalOriginSource:
        text = self._clean_str(raw_source).lower()
        if not text:
            raise TaskProposalValidationError("origin.source is required")
        try:
            return TaskProposalOriginSource(text)
        except ValueError as exc:  # pragma: no cover - validation guard
            allowed = ", ".join(member.value for member in TaskProposalOriginSource)
            raise TaskProposalValidationError(
                f"origin.source must be one of: {allowed}"
            ) from exc

    def _normalize_review_priority(
        self, value: object | None
    ) -> TaskProposalReviewPriority:
        text = self._clean_str(value).lower() or TaskProposalReviewPriority.NORMAL.value
        try:
            return TaskProposalReviewPriority(text)
        except ValueError as exc:
            allowed = ", ".join(member.value for member in TaskProposalReviewPriority)
            raise TaskProposalValidationError(
                f"priority must be one of: {allowed}"
            ) from exc

    @staticmethod
    def _encode_cursor(proposal: TaskProposal) -> str:
        return f"{proposal.created_at.astimezone(UTC).isoformat()}|{proposal.id}"

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            timestamp_text, proposal_id = cursor.split("|", 1)
            return datetime.fromisoformat(timestamp_text), UUID(proposal_id)
        except Exception as exc:  # pragma: no cover - validation guard
            raise TaskProposalValidationError("cursor is invalid") from exc

    def _prepare_task_create_request(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(request, dict):
            raise TaskProposalValidationError("taskCreateRequest must be an object")

        job_type = self._clean_str(request.get("type") or "task").lower()
        if job_type != "task":
            raise TaskProposalValidationError("taskCreateRequest.type must be 'task'")

        priority_raw = request.get("priority", 0)
        try:
            priority = int(priority_raw)
        except Exception as exc:  # pragma: no cover - validation guard
            raise TaskProposalValidationError(
                "taskCreateRequest.priority must be an integer"
            ) from exc

        max_attempts_raw = request.get("maxAttempts", 3)
        try:
            max_attempts = int(max_attempts_raw)
        except Exception as exc:  # pragma: no cover - validation guard
            raise TaskProposalValidationError(
                "taskCreateRequest.maxAttempts must be an integer"
            ) from exc
        if max_attempts < 1:
            raise TaskProposalValidationError("maxAttempts must be >= 1")

        affinity_key = self._clean_str(request.get("affinityKey"))
        affinity_key = affinity_key or None

        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            raise TaskProposalValidationError(
                "taskCreateRequest.payload must be an object"
            )
        try:
            normalized_payload = self._queue_service.normalize_task_job_payload(payload)
        except AgentQueueValidationError as exc:
            raise TaskProposalValidationError(str(exc)) from exc

        repository = self._clean_str(normalized_payload.get("repository"))
        if not repository:
            raise TaskProposalValidationError(
                "taskCreateRequest.payload.repository is required"
            )

        envelope: dict[str, Any] = {
            "type": "task",
            "priority": priority,
            "maxAttempts": max_attempts,
            "payload": normalized_payload,
        }
        if affinity_key:
            envelope["affinityKey"] = affinity_key
        return envelope, repository

    def _should_notify_category(self, category: str | None) -> bool:
        if not category:
            return False
        return category.lower() in _NOTIFICATION_CATEGORIES

    def _build_notification_payload(self, proposal: TaskProposal) -> dict[str, Any]:
        preview = proposal.task_create_request or {}
        payload = {
            "text": f"[Task Proposal] {proposal.category or 'general'} → {proposal.repository}",
            "attachments": [
                {
                    "title": proposal.title,
                    "title_link": f"/tasks/proposals/{proposal.id}",
                    "text": proposal.summary[:4000],
                    "fields": [
                        {
                            "title": "Repository",
                            "value": proposal.repository,
                            "short": True,
                        },
                        {
                            "title": "Priority",
                            "value": proposal.review_priority.value,
                            "short": True,
                        },
                    ],
                }
            ],
            "proposalId": str(proposal.id),
            "category": proposal.category,
        }
        origin_id = proposal.origin_id
        if origin_id:
            payload["originJobId"] = str(origin_id)
        payload["taskPreview"] = preview
        return payload

    def _notification_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._notification_authorization:
            headers["Authorization"] = self._notification_authorization
        return headers

    async def _emit_notification(self, proposal: TaskProposal) -> None:
        if not self._notifications_enabled or not self._notification_webhook:
            return
        if not self._should_notify_category(proposal.category):
            return
        already_sent = await self._repository.has_notification(
            proposal_id=proposal.id, target=self._notification_webhook
        )
        if already_sent:
            return
        payload = self._build_notification_payload(proposal)
        status = "sent"
        error_message: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self._notification_timeout) as client:
                response = await client.post(
                    self._notification_webhook,
                    json=payload,
                    headers=self._notification_headers(),
                )
                response.raise_for_status()
        except Exception as exc:  # pragma: no cover - best effort
            status = "failed"
            error_message = str(exc)
            logger.warning(
                "Proposal notification failed for %s: %s", proposal.id, error_message
            )
        finally:
            try:
                await self._repository.log_notification(
                    proposal_id=proposal.id,
                    category=proposal.category or "",
                    target=self._notification_webhook,
                    status=status,
                    error=error_message,
                )
                await self._repository.commit()
            except Exception:  # pragma: no cover - logging only
                logger.debug(
                    "Notification audit insert failed for proposal %s", proposal.id
                )

    async def create_proposal(
        self,
        *,
        title: str,
        summary: str,
        category: str | None,
        tags: list[str] | None,
        task_create_request: dict[str, Any],
        origin_source: object,
        origin_id: UUID | None,
        origin_metadata: dict[str, Any] | None,
        proposed_by_worker_id: str | None,
        proposed_by_user_id: UUID | None,
        review_priority: object | None = None,
    ) -> TaskProposal:
        if not proposed_by_worker_id and not proposed_by_user_id:
            raise TaskProposalValidationError(
                "one of proposed_by_worker_id or proposed_by_user_id is required"
            )
        cleaned_title = self._scrub_text(self._clean_str(title))
        if not cleaned_title:
            raise TaskProposalValidationError("title is required")
        if len(cleaned_title) > 256:
            raise TaskProposalValidationError("title exceeds max length")

        cleaned_summary = self._scrub_text(self._clean_str(summary))
        if not cleaned_summary:
            raise TaskProposalValidationError("summary is required")
        if len(cleaned_summary) > 10000:
            raise TaskProposalValidationError("summary exceeds max length")

        normalized_category = self._normalize_category(category)
        normalized_tags = self._normalize_tags(tags)
        origin = self._normalize_origin_source(origin_source)
        metadata = origin_metadata if isinstance(origin_metadata, dict) else {}
        requested_priority = (
            self._normalize_review_priority(review_priority)
            if review_priority is not None
            else TaskProposalReviewPriority.NORMAL
        )
        priority_override_reason: str | None = None

        envelope, repository = self._prepare_task_create_request(task_create_request)
        scrubbed_request = self._scrub_json(envelope)
        if self._is_moonmind_repository(repository):
            normalized_category, normalized_tags, cleaned_title = (
                self._enforce_moonmind_policy(
                    title=cleaned_title,
                    category=normalized_category,
                    tags=normalized_tags,
                    metadata=metadata,
                )
            )
            derived_priority, derived_reason = self._derive_moonmind_priority(
                normalized_tags, metadata
            )
            if derived_priority is not None and self._priority_rank(
                derived_priority
            ) > self._priority_rank(requested_priority):
                requested_priority = derived_priority
                priority_override_reason = derived_reason

        dedup_key, dedup_hash = self._compute_dedup_fields(
            repository=repository, title=cleaned_title
        )

        proposal = await self._repository.create_proposal(
            title=cleaned_title,
            summary=cleaned_summary,
            category=normalized_category,
            tags=normalized_tags,
            repository=repository,
            task_create_request=scrubbed_request,
            proposed_by_worker_id=proposed_by_worker_id,
            proposed_by_user_id=proposed_by_user_id,
            origin_source=origin,
            origin_id=origin_id,
            origin_metadata=metadata,
            dedup_key=dedup_key,
            dedup_hash=dedup_hash,
            review_priority=requested_priority,
            priority_override_reason=priority_override_reason,
        )
        await self._repository.commit()
        await self._emit_notification(proposal)
        logger.info(
            "Created task proposal %s (repository=%s category=%s)",
            proposal.id,
            repository,
            normalized_category or "-",
        )
        return proposal

    async def list_proposals(
        self,
        *,
        status: TaskProposalStatus | None,
        category: str | None,
        repository: str | None,
        origin_source: TaskProposalOriginSource | None,
        cursor: str | None,
        limit: int,
        include_snoozed: bool = False,
        only_snoozed: bool = False,
    ) -> tuple[list[TaskProposal], str | None]:
        if limit < 1 or limit > 200:
            raise TaskProposalValidationError("limit must be between 1 and 200")
        normalized_category = self._normalize_category(category)
        normalized_repository = self._clean_str(repository) or None
        cursor_tuple = self._decode_cursor(cursor) if cursor else None
        now = datetime.now(UTC)
        await self._repository.expire_snoozed(now=now)
        await self._repository.commit()
        proposals, has_more = await self._repository.list_proposals(
            status=status,
            category=normalized_category,
            repository=normalized_repository,
            origin_source=origin_source,
            cursor=cursor_tuple,
            limit=limit,
            now=now,
            include_snoozed=include_snoozed,
            only_snoozed=only_snoozed,
        )
        next_cursor = None
        if has_more and proposals:
            next_cursor = self._encode_cursor(proposals[-1])
        return proposals, next_cursor

    async def get_proposal(self, proposal_id: UUID) -> TaskProposal:
        proposal = await self._repository.get_proposal(proposal_id)
        if proposal is None:
            raise TaskProposalNotFoundError(str(proposal_id))
        return proposal

    async def get_similar_proposals(
        self, proposal: TaskProposal, limit: int | None = None
    ) -> list[TaskProposal]:
        limit = limit or self._similar_limit
        if not proposal.dedup_hash:
            return []
        return await self._repository.list_similar(proposal=proposal, limit=limit)

    async def promote_proposal(
        self,
        *,
        proposal_id: UUID,
        promoted_by_user_id: UUID,
        priority_override: int | None = None,
        max_attempts_override: int | None = None,
        note: str | None = None,
        task_create_request_override: dict[str, Any] | None = None,
    ) -> tuple[TaskProposal, Any]:
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status is TaskProposalStatus.PROMOTED:
            if proposal.promoted_job_id is None:
                raise TaskProposalStatusError(
                    "proposal already promoted without job id"
                )
            job = await self._queue_service.get_job(proposal.promoted_job_id)
            if job is None:
                raise TaskProposalStatusError(
                    "proposal already promoted but job record is unavailable"
                )
            return proposal, job
        if proposal.status is not TaskProposalStatus.OPEN:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be promoted"
            )

        if task_create_request_override:
            override_envelope, _ = self._prepare_task_create_request(
                task_create_request_override
            )
            request = override_envelope
        else:
            request = dict(proposal.task_create_request or {})
        payload = dict(request.get("payload") or {})
        try:
            payload = self._queue_service.normalize_task_job_payload(payload)
        except AgentQueueValidationError as exc:
            raise TaskProposalValidationError(
                f"stored task payload is invalid: {exc}"
            ) from exc
        request["payload"] = payload

        priority = request.get("priority", 0)
        if priority_override is not None:
            priority = priority_override
        try:
            priority = int(priority)
        except Exception as exc:  # pragma: no cover
            raise TaskProposalValidationError("priority override is invalid") from exc

        max_attempts = request.get("maxAttempts", 3)
        if max_attempts_override is not None:
            max_attempts = max_attempts_override
        try:
            max_attempts = int(max_attempts)
        except Exception as exc:  # pragma: no cover
            raise TaskProposalValidationError(
                "maxAttempts override is invalid"
            ) from exc
        if max_attempts < 1:
            raise TaskProposalValidationError("maxAttempts must be >= 1")

        job = await self._queue_service.create_job(
            job_type="task",
            payload=payload,
            priority=priority,
            created_by_user_id=promoted_by_user_id,
            affinity_key=request.get("affinityKey"),
            max_attempts=max_attempts,
        )

        proposal.status = TaskProposalStatus.PROMOTED
        proposal.promoted_job_id = job.id
        proposal.promoted_at = datetime.now(UTC)
        proposal.promoted_by_user_id = promoted_by_user_id
        proposal.decided_by_user_id = promoted_by_user_id
        proposal.task_create_request = self._scrub_json(
            {
                **request,
                "priority": priority,
                "maxAttempts": max_attempts,
                "payload": payload,
            }
        )
        proposal.decision_note = self._scrub_text(self._clean_str(note)) or None
        await self._repository.commit()
        await self._repository.refresh(proposal)
        logger.info(
            "Promoted proposal %s to job %s (priority=%s maxAttempts=%s)",
            proposal.id,
            job.id,
            priority,
            max_attempts,
        )
        return proposal, job

    async def dismiss_proposal(
        self,
        *,
        proposal_id: UUID,
        dismissed_by_user_id: UUID,
        note: str | None = None,
    ) -> TaskProposal:
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status is not TaskProposalStatus.OPEN:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be dismissed"
            )
        proposal.status = TaskProposalStatus.DISMISSED
        proposal.decided_by_user_id = dismissed_by_user_id
        proposal.decision_note = self._scrub_text(self._clean_str(note)) or None
        await self._repository.commit()
        await self._repository.refresh(proposal)
        logger.info("Dismissed proposal %s", proposal.id)
        return proposal

    async def update_review_priority(
        self,
        *,
        proposal_id: UUID,
        priority: object,
        updated_by_user_id: UUID,
    ) -> TaskProposal:
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status is not TaskProposalStatus.OPEN:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be reprioritized"
            )
        value = self._normalize_review_priority(priority)
        await self._repository.update_priority(
            proposal=proposal, priority=value, user_id=updated_by_user_id
        )
        await self._repository.commit()
        await self._repository.refresh(proposal)
        logger.info(
            "Updated proposal %s review priority to %s", proposal.id, value.value
        )
        return proposal

    async def snooze_proposal(
        self,
        *,
        proposal_id: UUID,
        until: datetime,
        note: str | None,
        user_id: UUID,
    ) -> TaskProposal:
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status is not TaskProposalStatus.OPEN:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be snoozed"
            )
        normalized_until = self._normalize_datetime(until)
        if normalized_until <= datetime.now(UTC):
            raise TaskProposalValidationError("snooze expiration must be in the future")
        cleaned_note = self._scrub_text(self._clean_str(note)) or None
        await self._repository.snooze(
            proposal=proposal,
            until=normalized_until,
            user_id=user_id,
            note=cleaned_note,
        )
        await self._repository.commit()
        await self._repository.refresh(proposal)
        logger.info(
            "Snoozed proposal %s until %s", proposal.id, normalized_until.isoformat()
        )
        return proposal

    async def unsnooze_proposal(
        self,
        *,
        proposal_id: UUID,
        user_id: UUID,
    ) -> TaskProposal:
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status is not TaskProposalStatus.OPEN:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be unsnoozed"
            )
        await self._repository.unsnooze(proposal=proposal, user_id=user_id)
        await self._repository.commit()
        await self._repository.refresh(proposal)
        logger.info("Unsnoozed proposal %s", proposal.id)
        return proposal
