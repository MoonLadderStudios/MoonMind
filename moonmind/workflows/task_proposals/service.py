"""Business logic for task proposal queue operations."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import httpx
from pydantic import ValidationError

from moonmind.config import settings
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.task_proposals.models import (
    TaskProposal,
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.delivery import (
    ProposalDeliveryError,
    ProviderDecisionEvent,
    ProviderDecisionResult,
    parse_provider_decision,
    request_from_proposal,
)
from moonmind.workflows.task_proposals.repositories import (
    TaskProposalNotFoundError,
    TaskProposalRepository,
)
from moonmind.workflows.tasks.task_contract import (
    CanonicalTaskPayload,
    SUPPORTED_EXECUTION_RUNTIMES,
    TaskContractError,
)

logger = logging.getLogger(__name__)
_PROPOSALS_WRITE_CAPABILITY = "proposals_write"
_LEGACY_TASK_WORKER_RUNTIME_CAPABILITIES = frozenset(
    {"codex", "gemini_cli", "claude", "jules"}
)
_PRESET_SOURCE_KINDS = frozenset({"preset-derived", "preset-include", "detached"})
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
_PROPOSAL_RUNTIME_MODE_ALIASES = {
    "codex_cli": "codex",
    "claude_code": "claude",
}

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
        *,
        redactor: SecretRedactor | None = None,
        delivery_service: object | None = None,
    ) -> None:
        self._repository = repository
        self._delivery_service = delivery_service
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

    @staticmethod
    def _normalize_policy_tokens(values: Sequence[str] | None) -> set[str]:
        normalized: set[str] = set()
        for value in values or ():
            token = str(value or "").strip().lower()
            if token == "priority":
                token = "reprioritize"
            if token:
                normalized.add(token)
        return normalized

    @staticmethod
    def _clean_str(value: object) -> str:
        return str(value).strip() if value is not None else ""

    def _scrub_text(self, text: str) -> str:
        return self._redactor.scrub(text)

    def _scrub_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False)
        scrubbed = self._redactor.scrub(serialized)
        return json.loads(scrubbed)

    def _merge_json_objects(
        self, existing: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            old_value = merged.get(key)
            if isinstance(old_value, Mapping) and isinstance(value, Mapping):
                merged[key] = self._merge_json_objects(old_value, value)
            else:
                merged[key] = value
        return merged

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
        if isinstance(raw_source, TaskProposalOriginSource):
            return raw_source
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

    @staticmethod
    def _enforce_proposal_pr_publish_mode(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize proposal payloads to PR publish mode for promoted follow-up jobs."""

        normalized_payload = dict(payload)
        task_node = normalized_payload.get("task")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = dict(publish_node) if isinstance(publish_node, Mapping) else {}
        publish["mode"] = "pr"
        task["publish"] = publish
        normalized_payload["task"] = task
        return normalized_payload

    @staticmethod
    def _enforce_flat_preset_derived_steps(payload: Mapping[str, Any]) -> None:
        task = payload.get("task")
        if not isinstance(task, Mapping):
            return
        steps = task.get("steps")
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
            return
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, Mapping):
                continue
            source = raw_step.get("source")
            source_kind = ""
            if isinstance(source, Mapping):
                source_kind = str(source.get("kind") or "").strip()
            if source_kind not in _PRESET_SOURCE_KINDS:
                continue
            step_type = str(raw_step.get("type") or "").strip().lower()
            if step_type == "tool" and isinstance(raw_step.get("tool"), Mapping):
                continue
            if step_type == "skill" and isinstance(raw_step.get("skill"), Mapping):
                continue
            raise TaskProposalValidationError(
                "stored task payload is invalid: preset-derived proposal steps "
                f"must be flat executable Tool or Skill steps at task.steps[{index}]"
            )

    @staticmethod
    def _proposal_has_explicit_skill(task: Mapping[str, Any]) -> bool:
        skill = task.get("skill")
        if not isinstance(skill, Mapping):
            return False
        skill_id = str(skill.get("id") or "").strip().lower()
        return bool(skill_id and skill_id != "auto")

    def _normalize_proposal_task_payload(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """Validate proposal payload shape without applying runtime defaults."""

        payload_for_validation = self._normalize_proposal_runtime_payload(payload)
        task_node = payload_for_validation.get("task")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        if not task:
            task = {
                "instructions": (
                    self._clean_str(
                        payload_for_validation.get("instructions")
                        or payload_for_validation.get("instruction")
                    )
                    or "Queue job"
                ),
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": None, "model": None, "effort": None},
                "git": {"startingBranch": None, "targetBranch": None},
                "publish": {"mode": "pr"},
            }
        elif not self._clean_str(
            task.get("instructions")
        ) and not self._proposal_has_explicit_skill(task):
            task["instructions"] = (
                self._clean_str(
                    payload_for_validation.get("instructions")
                    or payload_for_validation.get("instruction")
                )
                or "Queue job"
            )
        payload_for_validation["task"] = task

        try:
            model = CanonicalTaskPayload.model_validate(payload_for_validation)
        except (ValidationError, TaskContractError) as exc:
            raise TaskProposalValidationError(str(exc)) from exc
        normalized_payload = model.model_dump(by_alias=True, exclude_none=False)
        normalized_payload = self._enforce_proposal_pr_publish_mode(normalized_payload)

        repository = self._clean_str(normalized_payload.get("repository"))
        if not repository:
            raise TaskProposalValidationError(
                "taskCreateRequest.payload.repository is required"
            )
        return normalized_payload, repository

    @classmethod
    def _normalize_proposal_runtime_mode(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        lowered = value.strip().lower()
        if not lowered:
            return value
        return _PROPOSAL_RUNTIME_MODE_ALIASES.get(lowered, lowered)

    @classmethod
    def _normalize_proposal_runtime_mapping(
        cls, runtime_node: Mapping[str, Any]
    ) -> dict[str, Any]:
        runtime = dict(runtime_node)
        for key in ("mode", "targetRuntime", "target_runtime"):
            if key in runtime:
                runtime[key] = cls._normalize_proposal_runtime_mode(runtime.get(key))
        return runtime

    @classmethod
    def _normalize_proposal_runtime_payload(
        cls, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Normalize proposal runtime ids to the task contract vocabulary."""

        normalized = deepcopy(dict(payload))

        for key in ("targetRuntime", "target_runtime", "runtime"):
            if key in normalized:
                normalized[key] = cls._normalize_proposal_runtime_mode(
                    normalized.get(key)
                )

        task_node = normalized.get("task")
        if not isinstance(task_node, Mapping):
            return normalized

        task = dict(task_node)
        for key in ("targetRuntime", "target_runtime", "runtime"):
            if key not in task:
                continue
            value = task.get(key)
            if key == "runtime" and isinstance(value, Mapping):
                task["runtime"] = cls._normalize_proposal_runtime_mapping(value)
                continue
            task[key] = cls._normalize_proposal_runtime_mode(value)
        normalized["task"] = task
        return normalized

    def _prepare_task_create_request(
        self,
        request: dict[str, Any],
        *,
        apply_runtime_defaults: bool,
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
        if apply_runtime_defaults:
            try:
                normalized_payload_input = self._normalize_proposal_runtime_payload(
                    payload
                )
                parsed = CanonicalTaskPayload.model_validate(normalized_payload_input)
                normalized_payload = parsed.model_dump(by_alias=True, exclude_none=True)
            except ValidationError as exc:
                raise TaskProposalValidationError(f"Invalid task payload: {exc}") from exc
            normalized_payload = self._enforce_proposal_pr_publish_mode(
                normalized_payload
            )
            repository = self._clean_str(normalized_payload.get("repository"))
            if not repository:
                raise TaskProposalValidationError(
                    "taskCreateRequest.payload.repository is required"
                )
        else:
            normalized_payload, repository = self._normalize_proposal_task_payload(
                payload
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

    async def _deliver_proposal_if_configured(self, proposal: TaskProposal) -> None:
        if self._delivery_service is None:
            return
        existing_metadata = (
            dict(getattr(proposal, "provider_metadata", {}))
            if isinstance(getattr(proposal, "provider_metadata", {}), Mapping)
            else {}
        )
        try:
            result = await self._delivery_service.deliver(request_from_proposal(proposal))
        except ProposalDeliveryError as exc:
            existing_metadata["delivery"] = {
                "status": "failed",
                "error": self._scrub_json(exc.to_output(record_id=str(proposal.id))),
            }
            proposal.provider_metadata = existing_metadata
            await self._repository.commit()
            return
        except Exception as exc:
            provider = self._clean_str(getattr(proposal, "provider", "")) or "unknown"
            destination = self._clean_str(getattr(proposal, "repository", "")) or "unknown"
            logger.warning(
                "Proposal delivery adapter failed for %s via %s: %s",
                proposal.id,
                provider,
                type(exc).__name__,
            )
            existing_metadata["delivery"] = self._scrub_json(
                {
                    "status": "failed",
                    "error": {
                        "provider": provider,
                        "destination": destination,
                        "sanitizedReason": "provider delivery failed",
                        "recoverableNextAction": (
                            "Review trusted provider adapter logs and configuration."
                        ),
                        "retryable": True,
                        "deliveryRecordId": str(proposal.id),
                        "errorType": type(exc).__name__,
                    },
                }
            )
            proposal.provider_metadata = existing_metadata
            await self._repository.commit()
            return
        proposal.external_key = result.external_key
        proposal.external_url = result.external_url
        proposal.delivered_at = result.delivered_at
        proposal.last_synced_at = result.delivered_at
        delivery_metadata = {
            "status": "delivered",
            "created": result.created,
            "duplicateSource": result.duplicate_source,
            "storedSnapshotNotice": True,
            **dict(result.provider_metadata or {}),
        }
        existing_metadata["delivery"] = self._scrub_json(delivery_metadata)
        proposal.provider_metadata = existing_metadata
        await self._repository.commit()

    async def redeliver_proposal(self, *, proposal_id: UUID) -> TaskProposal:
        """Retry provider delivery through the trusted delivery adapter."""

        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if self._delivery_service is None:
            raise TaskProposalValidationError(
                "proposal delivery provider is not configured"
            )
        await self._deliver_proposal_if_configured(proposal)
        await self._repository.refresh(proposal)
        return proposal

    async def sync_proposal_delivery(self, *, proposal_id: UUID) -> TaskProposal:
        """Refresh delivery audit metadata without changing the executable payload."""

        proposal = await self._repository.get_proposal_for_update(proposal_id)
        provider_metadata = (
            dict(getattr(proposal, "provider_metadata", {}))
            if isinstance(getattr(proposal, "provider_metadata", {}), Mapping)
            else {}
        )
        syncer = getattr(self._delivery_service, "sync", None)
        now = datetime.now(UTC)
        if callable(syncer):
            try:
                result = await syncer(request_from_proposal(proposal))
            except Exception as exc:  # pragma: no cover - adapter-specific
                provider_metadata["sync"] = self._scrub_json(
                    {
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "sanitizedReason": str(exc),
                        "syncedAt": now.isoformat(),
                    }
                )
            else:
                metadata = result if isinstance(result, Mapping) else {}
                provider_metadata["sync"] = self._scrub_json(
                    {
                        "status": "synced",
                        "syncedAt": now.isoformat(),
                        **dict(metadata),
                    }
                )
        else:
            provider_metadata["sync"] = self._scrub_json(
                {
                    "status": "inspected",
                    "syncedAt": now.isoformat(),
                    "note": (
                        "trusted provider adapter does not expose a dedicated sync "
                        "operation"
                    ),
                }
            )
        proposal.last_synced_at = now
        proposal.provider_metadata = provider_metadata
        await self._repository.commit()
        await self._repository.refresh(proposal)
        return proposal

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
        origin_external_id: str | None = None,
        proposed_by_worker_id: str | None,
        proposed_by_user_id: UUID | None,
        review_priority: object | None = None,
        provider: str | None = None,
        external_key: str | None = None,
        external_url: str | None = None,
        delivered_at: datetime | None = None,
        last_synced_at: datetime | None = None,
        task_snapshot_ref: str | None = None,
        provider_metadata: dict[str, Any] | None = None,
        resolved_policy: dict[str, Any] | None = None,
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
        cleaned_origin_external_id = (
            self._scrub_text(self._clean_str(origin_external_id)) or None
        )
        if (
            origin is TaskProposalOriginSource.WORKFLOW
            and cleaned_origin_external_id is None
        ):
            cleaned_origin_external_id = (
                self._scrub_text(self._clean_str(metadata.get("workflow_id")))
                or None
            )
        normalized_provider = (self._clean_str(provider).lower() or "github")
        if normalized_provider not in {"github", "jira"}:
            raise TaskProposalValidationError("provider must be github or jira")
        scrubbed_provider_metadata = self._scrub_json(
            dict(provider_metadata or {})
        )
        scrubbed_resolved_policy = self._scrub_json(dict(resolved_policy or {}))
        cleaned_external_key = self._scrub_text(self._clean_str(external_key)) or None
        cleaned_external_url = self._scrub_text(self._clean_str(external_url)) or None
        cleaned_task_snapshot_ref = (
            self._scrub_text(self._clean_str(task_snapshot_ref)) or None
        )
        requested_priority = (
            self._normalize_review_priority(review_priority)
            if review_priority is not None
            else TaskProposalReviewPriority.NORMAL
        )
        priority_override_reason: str | None = None

        envelope, repository = self._prepare_task_create_request(
            task_create_request,
            apply_runtime_defaults=False,
        )
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

        duplicate_finder = getattr(self._repository, "find_open_duplicate", None)
        if callable(duplicate_finder):
            duplicate = await duplicate_finder(
                provider=normalized_provider,
                repository=repository,
                dedup_hash=dedup_hash,
            )
            if duplicate is not None and not isinstance(duplicate, Mock):
                logger.info(
                    "Reusing open task proposal %s (provider=%s repository=%s)",
                    getattr(duplicate, "id", "-"),
                    normalized_provider,
                    repository,
                )
                now = datetime.now(UTC)
                duplicate.last_synced_at = last_synced_at or now
                existing_provider_metadata = (
                    dict(getattr(duplicate, "provider_metadata", {}))
                    if isinstance(getattr(duplicate, "provider_metadata", {}), Mapping)
                    else {}
                )
                existing_resolved_policy = (
                    dict(getattr(duplicate, "resolved_policy", {}))
                    if isinstance(getattr(duplicate, "resolved_policy", {}), Mapping)
                    else {}
                )
                duplicate.provider_metadata = self._merge_json_objects(
                    existing_provider_metadata, scrubbed_provider_metadata
                )
                duplicate.resolved_policy = self._merge_json_objects(
                    existing_resolved_policy,
                    {
                        **scrubbed_resolved_policy,
                        "duplicate": True,
                        "duplicate_record_id": str(getattr(duplicate, "id", "")),
                    },
                )
                if cleaned_external_key is not None:
                    duplicate.external_key = cleaned_external_key
                if cleaned_external_url is not None:
                    duplicate.external_url = cleaned_external_url
                if delivered_at is not None:
                    duplicate.delivered_at = delivered_at
                if cleaned_task_snapshot_ref is not None:
                    duplicate.task_snapshot_ref = cleaned_task_snapshot_ref
                duplicate.origin_external_id = (
                    getattr(duplicate, "origin_external_id", None)
                    or cleaned_origin_external_id
                )
                await self._repository.commit()
                await self._deliver_proposal_if_configured(duplicate)
                refresh = getattr(self._repository, "refresh", None)
                if callable(refresh):
                    refreshed = await refresh(duplicate)
                    if refreshed is not None and not isinstance(refreshed, Mock):
                        duplicate = refreshed
                return duplicate

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
            origin_external_id=cleaned_origin_external_id,
            origin_metadata=metadata,
            dedup_key=dedup_key,
            dedup_hash=dedup_hash,
            review_priority=requested_priority,
            priority_override_reason=priority_override_reason,
            provider=normalized_provider,
            external_key=cleaned_external_key,
            external_url=cleaned_external_url,
            delivered_at=delivered_at,
            last_synced_at=last_synced_at,
            task_snapshot_ref=cleaned_task_snapshot_ref,
            provider_metadata=scrubbed_provider_metadata,
            resolved_policy=scrubbed_resolved_policy,
        )
        await self._repository.commit()
        await self._deliver_proposal_if_configured(proposal)
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
        origin_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[TaskProposal], str | None]:
        if limit < 1 or limit > 200:
            raise TaskProposalValidationError("limit must be between 1 and 200")
        normalized_category = self._normalize_category(category)
        normalized_repository = self._clean_str(repository) or None
        cursor_tuple = self._decode_cursor(cursor) if cursor else None
        proposals, has_more = await self._repository.list_proposals(
            status=status,
            category=normalized_category,
            repository=normalized_repository,
            origin_source=origin_source,
            origin_id=origin_id,
            cursor=cursor_tuple,
            limit=limit,
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

    async def record_provider_decision_event(
        self,
        *,
        proposal_id: UUID,
        event: ProviderDecisionEvent,
    ) -> ProviderDecisionResult:
        """Record a bounded provider reviewer decision without trusting issue text."""

        proposal = await self._repository.get_proposal_for_update(proposal_id)
        provider_metadata = (
            dict(getattr(proposal, "provider_metadata", {}))
            if isinstance(getattr(proposal, "provider_metadata", {}), Mapping)
            else {}
        )
        decision_rows = provider_metadata.get("providerDecisions")
        decisions: list[dict[str, Any]] = (
            list(decision_rows) if isinstance(decision_rows, list) else []
        )
        for row in decisions:
            if not isinstance(row, Mapping):
                continue
            if row.get("providerEventId") == event.provider_event_id:
                return ProviderDecisionResult(
                    accepted=bool(row.get("accepted")),
                    decision=self._clean_str(row.get("decision")) or None,
                    note=self._clean_str(row.get("note")) or None,
                    actor=self._clean_str(row.get("actor")),
                    provider_event_id=event.provider_event_id,
                    reason="duplicate_event",
                    priority=self._clean_str(row.get("priority")) or None,
                    defer_until=self._clean_str(row.get("deferUntil")) or None,
                    runtime_mode=self._clean_str(row.get("runtimeMode")) or None,
                    external_state=self._clean_str(row.get("resultingExternalState"))
                    or None,
                    promoted_execution_id=self._clean_str(
                        row.get("promotedExecutionId")
                    )
                    or None,
                )

        proposal_provider = self._clean_str(getattr(proposal, "provider", "")).lower()
        event_provider = self._clean_str(event.provider).lower()
        proposal_external_key = self._clean_str(getattr(proposal, "external_key", ""))
        event_external_key = self._clean_str(event.external_key)
        if not self._clean_str(event.provider_event_id):
            result = ProviderDecisionResult(
                accepted=False,
                decision=None,
                note=None,
                actor=event.actor,
                provider_event_id=event.provider_event_id,
                reason="missing_provider_event_id",
            )
        elif (
            not proposal_provider
            or not proposal_external_key
            or event_provider != proposal_provider
            or event_external_key != proposal_external_key
        ):
            result = ProviderDecisionResult(
                accepted=False,
                decision=None,
                note=None,
                actor=event.actor,
                provider_event_id=event.provider_event_id,
                reason="provider_identity_mismatch",
            )
        elif not event.authenticity_verified:
            result = ProviderDecisionResult(
                accepted=False,
                decision=None,
                note=None,
                actor=event.actor,
                provider_event_id=event.provider_event_id,
                reason="provider_auth_failed",
            )
        elif not event.actor_authorized:
            result = ProviderDecisionResult(
                accepted=False,
                decision=None,
                note=None,
                actor=event.actor,
                provider_event_id=event.provider_event_id,
                reason="actor_not_authorized",
            )
        else:
            result = parse_provider_decision(event)
        policy = (
            dict(getattr(proposal, "resolved_policy", {}))
            if isinstance(getattr(proposal, "resolved_policy", {}), Mapping)
            else {}
        )
        allowed_actions = self._normalize_policy_tokens(
            policy.get("allowedActions")
            if isinstance(policy.get("allowedActions"), Sequence)
            and not isinstance(policy.get("allowedActions"), (str, bytes))
            else None
        )
        allowed_actors = self._normalize_policy_tokens(
            policy.get("allowedActors")
            if isinstance(policy.get("allowedActors"), Sequence)
            and not isinstance(policy.get("allowedActors"), (str, bytes))
            else None
        )
        actor_token = self._clean_str(event.actor).lower()
        if result.accepted and allowed_actors and actor_token not in allowed_actors:
            result = ProviderDecisionResult(
                accepted=False,
                decision=result.decision,
                note=None,
                actor=result.actor,
                provider_event_id=result.provider_event_id,
                reason="actor_not_authorized",
                runtime_mode=result.runtime_mode,
            )
        if (
            result.accepted
            and allowed_actions
            and result.decision not in allowed_actions
        ):
            result = ProviderDecisionResult(
                accepted=False,
                decision=result.decision,
                note=None,
                actor=result.actor,
                provider_event_id=result.provider_event_id,
                reason="action_not_allowed",
                runtime_mode=result.runtime_mode,
            )

        resulting_state = (
            result.external_state or result.decision or result.reason or "ignored"
        )
        if result.accepted and result.decision == "dismiss":
            proposal.status = TaskProposalStatus.DISMISSED
            proposal.decision_note = (
                self._scrub_text(self._clean_str(result.note)) or None
            )
            resulting_state = TaskProposalStatus.DISMISSED.value
        elif result.accepted and result.decision == "promote":
            proposal.status = TaskProposalStatus.ACCEPTED
            proposal.decision_note = (
                self._scrub_text(self._clean_str(result.note)) or None
            )
            resulting_state = TaskProposalStatus.ACCEPTED.value
        elif result.accepted and result.decision == "reprioritize" and result.priority:
            proposal.review_priority = self._normalize_review_priority(result.priority)
            proposal.decision_note = (
                self._scrub_text(self._clean_str(result.note)) or None
            )
            resulting_state = result.external_state or "reprioritized"
        elif result.accepted and result.decision == "defer":
            proposal.decision_note = (
                self._scrub_text(self._clean_str(result.note)) or None
            )
            resulting_state = result.external_state or "deferred"
        elif result.accepted and result.decision == "request_revision":
            proposal.decision_note = (
                self._scrub_text(self._clean_str(result.note)) or None
            )
            resulting_state = result.external_state or "revision_requested"

        decisions.append(
            self._scrub_json(
                {
                    "provider": event.provider,
                    "externalKey": event.external_key,
                    "providerEventId": event.provider_event_id,
                    "actor": event.actor,
                    "decision": result.decision,
                    "accepted": result.accepted,
                    "note": result.note,
                    "priority": result.priority,
                    "deferUntil": result.defer_until,
                    "runtimeMode": result.runtime_mode,
                    "observedAt": event.observed_at.isoformat(),
                    "resultingExternalState": resulting_state,
                    "reason": result.reason,
                }
            )
        )
        provider_metadata["providerDecisions"] = decisions
        proposal.provider_metadata = provider_metadata
        await self._sync_provider_decision_state_if_configured(
            proposal=proposal,
            result=result,
            resulting_state=resulting_state,
            promoted_execution_id=None,
        )
        await self._repository.commit()
        await self._repository.refresh(proposal)
        return result

    async def _sync_provider_decision_state_if_configured(
        self,
        *,
        proposal: TaskProposal,
        result: ProviderDecisionResult,
        resulting_state: str,
        promoted_execution_id: str | None,
    ) -> None:
        """Best-effort external review state update through a trusted adapter."""

        updater = getattr(self._delivery_service, "record_decision", None)
        if not callable(updater):
            return
        payload = self._scrub_json(
            {
                "proposalId": str(getattr(proposal, "id", "")),
                "provider": getattr(proposal, "provider", None),
                "externalKey": getattr(proposal, "external_key", None),
                "decision": result.decision,
                "accepted": result.accepted,
                "actor": result.actor,
                "providerEventId": result.provider_event_id,
                "reason": result.reason,
                "note": result.note,
                "resultingExternalState": resulting_state,
                "promotedExecutionId": promoted_execution_id,
            }
        )
        try:
            await updater(payload)
        except Exception as exc:  # pragma: no cover - adapter-specific
            provider_metadata = (
                dict(getattr(proposal, "provider_metadata", {}))
                if isinstance(getattr(proposal, "provider_metadata", {}), Mapping)
                else {}
            )
            warnings = provider_metadata.get("providerDecisionUpdateWarnings")
            warning_rows: list[dict[str, Any]] = (
                list(warnings) if isinstance(warnings, list) else []
            )
            warning_rows.append(
                self._scrub_json(
                    {
                        "providerEventId": result.provider_event_id,
                        "errorType": type(exc).__name__,
                        "sanitizedReason": str(exc),
                    }
                )
            )
            provider_metadata["providerDecisionUpdateWarnings"] = warning_rows
            proposal.provider_metadata = provider_metadata

    async def attach_provider_decision_execution(
        self,
        *,
        proposal_id: UUID,
        provider_event_id: str,
        promoted_execution_id: str,
    ) -> TaskProposal:
        """Attach a created execution id to an accepted provider decision row."""

        proposal = await self._repository.get_proposal_for_update(proposal_id)
        provider_metadata = (
            dict(getattr(proposal, "provider_metadata", {}))
            if isinstance(getattr(proposal, "provider_metadata", {}), Mapping)
            else {}
        )
        decision_rows = provider_metadata.get("providerDecisions")
        decisions: list[dict[str, Any]] = (
            list(decision_rows) if isinstance(decision_rows, list) else []
        )
        result: ProviderDecisionResult | None = None
        for row in decisions:
            if not isinstance(row, dict):
                continue
            if row.get("providerEventId") == provider_event_id:
                row["promotedExecutionId"] = self._scrub_text(
                    self._clean_str(promoted_execution_id)
                )
                row["resultingExternalState"] = "promoted"
                result = ProviderDecisionResult(
                    accepted=bool(row.get("accepted")),
                    decision=self._clean_str(row.get("decision")) or None,
                    note=self._clean_str(row.get("note")) or None,
                    actor=self._clean_str(row.get("actor")),
                    provider_event_id=provider_event_id,
                    reason=self._clean_str(row.get("reason")) or None,
                    runtime_mode=self._clean_str(row.get("runtimeMode")) or None,
                    promoted_execution_id=promoted_execution_id,
                )
                break
        provider_metadata["providerDecisions"] = decisions
        proposal.provider_metadata = self._scrub_json(provider_metadata)
        if result is not None:
            await self._sync_provider_decision_state_if_configured(
                proposal=proposal,
                result=result,
                resulting_state="promoted",
                promoted_execution_id=promoted_execution_id,
            )
        await self._repository.commit()
        await self._repository.refresh(proposal)
        return proposal

    async def promote_proposal(
        self,
        *,
        proposal_id: UUID,
        promoted_by_user_id: UUID,
        priority_override: int | None = None,
        max_attempts_override: int | None = None,
        note: str | None = None,
        runtime_mode_override: str | None = None,
    ) -> tuple[TaskProposal, dict[str, Any]]:
        """Validate and finalize a proposal for execution promotion.

        Returns the updated TaskProposal and the finalized taskCreateRequest
        envelope (suitable for use as ``initial_parameters``) ready to execute.
        Runtime mode may be overridden as a bounded promotion control; reviewed
        task steps, instructions, and provenance are always loaded from the
        stored proposal payload.
        """
        proposal = await self._repository.get_proposal_for_update(proposal_id)
        if proposal.status not in {
            TaskProposalStatus.OPEN,
            TaskProposalStatus.ACCEPTED,
        }:
            raise TaskProposalStatusError(
                f"proposal status {proposal.status.value} cannot be promoted"
            )

        request = dict(proposal.task_create_request or {})
        payload = dict(request.get("payload") or {})
        try:
            parsed = CanonicalTaskPayload.model_validate(payload)
            payload = parsed.model_dump(by_alias=True, exclude_none=True)
        except ValidationError as exc:
            raise TaskProposalValidationError(
                f"stored task payload is invalid: {exc}"
            ) from exc
        self._enforce_flat_preset_derived_steps(payload)
        if runtime_mode_override is not None:
            normalized_runtime_mode = self._normalize_proposal_runtime_mode(
                runtime_mode_override
            )
            if (
                not isinstance(normalized_runtime_mode, str)
                or normalized_runtime_mode not in SUPPORTED_EXECUTION_RUNTIMES
            ):
                supported = ", ".join(sorted(SUPPORTED_EXECUTION_RUNTIMES))
                raise TaskProposalValidationError(
                    f"runtimeMode must be one of: {supported}"
                )
            task_node = payload.get("task")
            task = dict(task_node) if isinstance(task_node, Mapping) else {}
            runtime_node = task.get("runtime")
            runtime = dict(runtime_node) if isinstance(runtime_node, Mapping) else {}
            runtime["mode"] = normalized_runtime_mode
            task["runtime"] = runtime
            payload["task"] = task
            payload["targetRuntime"] = normalized_runtime_mode
            try:
                parsed = CanonicalTaskPayload.model_validate(payload)
                payload = parsed.model_dump(by_alias=True, exclude_none=True)
            except ValidationError as exc:
                raise TaskProposalValidationError(
                    f"runtimeMode override is invalid: {exc}"
                ) from exc
            self._enforce_flat_preset_derived_steps(payload)
        payload = self._enforce_proposal_pr_publish_mode(payload)
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

        proposal.status = TaskProposalStatus.PROMOTED
        proposal.promoted_at = datetime.now(UTC)
        proposal.promoted_by_user_id = promoted_by_user_id
        proposal.decided_by_user_id = promoted_by_user_id

        final_request = self._scrub_json(
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
            "Prepared promoted proposal %s for execution (priority=%s maxAttempts=%s)",
            proposal.id,
            priority,
            max_attempts,
        )
        return proposal, final_request

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
