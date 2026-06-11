"""Versioned Temporal hard-switch cutover helpers for MM-730."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from moonmind.config.settings import TemporalSettings


LEGACY_USER_WORKFLOW_TYPE = "MoonMind.Run"
RENAMED_USER_WORKFLOW_TYPE = "MoonMind.UserWorkflow"
RENAMED_USER_WORKFLOW_CONTRACT = "renamed_contract"
MM730_RELEASE_NOTE_TEXT = (
    "MoonMind no longer exposes Tasks as a product/runtime concept. "
    "Use Workflow Execution, workflowId, runId, and Step Execution."
)

_VALID_CONTRACT_MODES = {RENAMED_USER_WORKFLOW_CONTRACT}
_VALID_ENVIRONMENT_DECISIONS = {
    "drain",
    "pause_resume",
    "terminate_restart",
}
_REQUIRED_AFFECTED_CONTRACT_KINDS = {
    "workflow",
    "activity",
    "signal",
    "update",
}


class HardSwitchCutoverError(ValueError):
    """Raised when the MM-730 cutover contract is incomplete."""


@dataclass(frozen=True, slots=True)
class UserWorkflowStartContract:
    """Temporal start contract for user Workflow Executions."""

    workflow_type: str
    task_queue: str
    contract_mode: str


def normalize_user_workflow_contract_mode(value: Any) -> str:
    """Normalize the configured user-workflow cutover mode."""

    normalized = str(value or RENAMED_USER_WORKFLOW_CONTRACT).strip().lower()
    if normalized not in _VALID_CONTRACT_MODES:
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_CONTRACT_MODE must be one of "
            f"{', '.join(sorted(_VALID_CONTRACT_MODES))}"
        )
    return normalized


def resolve_user_workflow_start_contract(
    temporal_settings: TemporalSettings,
) -> UserWorkflowStartContract:
    """Resolve the Temporal workflow type and queue for new user starts."""

    mode = normalize_user_workflow_contract_mode(
        temporal_settings.user_workflow_contract_mode
    )
    return _resolve_renamed_user_workflow_start_contract(
        workflow_task_queue=str(temporal_settings.workflow_task_queue),
        user_workflow_v2_task_queue=str(temporal_settings.user_workflow_v2_task_queue),
        cutover_record_path=temporal_settings.user_workflow_cutover_record_path,
        release_notes_path=temporal_settings.user_workflow_release_notes_path,
    )


@lru_cache(maxsize=32)
def _resolve_renamed_user_workflow_start_contract(
    *,
    workflow_task_queue: str,
    user_workflow_v2_task_queue: str,
    cutover_record_path: str | None,
    release_notes_path: str | None,
) -> UserWorkflowStartContract:
    """Resolve and cache the validated renamed user-workflow start contract."""

    _validate_hard_switch_cutover_input_paths(
        cutover_record_path=cutover_record_path,
        release_notes_path=release_notes_path,
    )
    task_queue = str(user_workflow_v2_task_queue).strip()
    if not task_queue:
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE is required for "
            "renamed_contract mode"
        )
    if task_queue == str(workflow_task_queue).strip():
        raise HardSwitchCutoverError(
            "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE must be distinct from "
            "TEMPORAL_WORKFLOW_TASK_QUEUE for renamed_contract mode"
        )
    return UserWorkflowStartContract(
        workflow_type=RENAMED_USER_WORKFLOW_TYPE,
        task_queue=task_queue,
        contract_mode=RENAMED_USER_WORKFLOW_CONTRACT,
    )


def registered_user_workflow_type(temporal_settings: TemporalSettings) -> str:
    """Return the single user workflow type this worker build should serve."""

    return resolve_user_workflow_start_contract(temporal_settings).workflow_type


def validate_hard_switch_cutover_inputs(temporal_settings: TemporalSettings) -> None:
    """Validate release and environment records before enabling the hard switch."""

    _validate_hard_switch_cutover_input_paths(
        cutover_record_path=temporal_settings.user_workflow_cutover_record_path,
        release_notes_path=temporal_settings.user_workflow_release_notes_path,
    )


def _validate_hard_switch_cutover_input_paths(
    *, cutover_record_path: Any, release_notes_path: Any
) -> None:
    """Validate release and environment records from configured paths."""

    record_path = _required_path(
        cutover_record_path,
        field_name="TEMPORAL_USER_WORKFLOW_CUTOVER_RECORD_PATH",
    )
    release_notes_path = _required_path(
        release_notes_path,
        field_name="TEMPORAL_USER_WORKFLOW_RELEASE_NOTES_PATH",
    )
    record = _load_json_mapping(record_path)
    _validate_release_notes(release_notes_path)
    _validate_cutover_record(record, release_notes_path=release_notes_path)


def _required_path(value: Any, *, field_name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise HardSwitchCutoverError(f"{field_name} is required for renamed_contract mode")
    path = Path(raw)
    if not path.exists():
        raise HardSwitchCutoverError(f"{field_name} does not exist: {path}")
    if not path.is_file():
        raise HardSwitchCutoverError(f"{field_name} must point to a file: {path}")
    return path


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HardSwitchCutoverError(
            f"Cutover record {path} is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HardSwitchCutoverError(f"Cutover record {path} must be a JSON object")
    return payload


def _validate_release_notes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MM730_RELEASE_NOTE_TEXT not in text:
        raise HardSwitchCutoverError(
            "MM-730 release notes must state the required Tasks removal notice"
        )
    lowered = text.lower()
    if "compatibility redirect" not in lowered or "alias" not in lowered:
        raise HardSwitchCutoverError(
            "MM-730 release notes must state that compatibility redirects and "
            "aliases are not kept"
        )


def _validate_cutover_record(
    record: Mapping[str, Any], *, release_notes_path: Path
) -> None:
    if str(record.get("jiraIssueKey") or "").strip() != "MM-730":
        raise HardSwitchCutoverError("Cutover record jiraIssueKey must be MM-730")
    if str(record.get("releaseMode") or "").strip() != "coordinated_branch_release":
        raise HardSwitchCutoverError(
            "Cutover record releaseMode must be coordinated_branch_release"
        )
    if str(record.get("releaseNotesPath") or "").strip() != str(release_notes_path):
        raise HardSwitchCutoverError(
            "Cutover record releaseNotesPath must match "
            "TEMPORAL_USER_WORKFLOW_RELEASE_NOTES_PATH"
        )
    if str(record.get("newWorkflowType") or "").strip() != RENAMED_USER_WORKFLOW_TYPE:
        raise HardSwitchCutoverError(
            f"Cutover record newWorkflowType must be {RENAMED_USER_WORKFLOW_TYPE}"
        )
    if str(record.get("legacyWorkflowType") or "").strip() != LEGACY_USER_WORKFLOW_TYPE:
        raise HardSwitchCutoverError(
            f"Cutover record legacyWorkflowType must be {LEGACY_USER_WORKFLOW_TYPE}"
        )

    environment_records = record.get("environments")
    if not isinstance(environment_records, list) or not environment_records:
        raise HardSwitchCutoverError(
            "Cutover record must include at least one environment decision"
        )
    for index, entry in enumerate(environment_records, start=1):
        if not isinstance(entry, Mapping):
            raise HardSwitchCutoverError(
                f"Cutover environment entry {index} must be an object"
            )
        name = str(entry.get("name") or "").strip()
        decision = str(entry.get("decision") or "").strip()
        recorded_at = str(entry.get("recordedAt") or "").strip()
        if not name:
            raise HardSwitchCutoverError(
                f"Cutover environment entry {index} is missing name"
            )
        if decision not in _VALID_ENVIRONMENT_DECISIONS:
            raise HardSwitchCutoverError(
                f"Cutover environment '{name}' decision must be one of "
                f"{', '.join(sorted(_VALID_ENVIRONMENT_DECISIONS))}"
            )
        if not recorded_at:
            raise HardSwitchCutoverError(
                f"Cutover environment '{name}' is missing recordedAt"
            )

    affected_contracts = record.get("affectedContracts")
    if not isinstance(affected_contracts, list):
        raise HardSwitchCutoverError("Cutover record affectedContracts must be a list")
    seen_kinds: set[str] = set()
    for index, entry in enumerate(affected_contracts, start=1):
        if not isinstance(entry, Mapping):
            raise HardSwitchCutoverError(
                f"Cutover affectedContracts entry {index} must be an object"
            )
        kind = str(entry.get("kind") or "").strip()
        owner = str(entry.get("owner") or "").strip()
        strategy = str(entry.get("strategy") or "").strip()
        if kind in _REQUIRED_AFFECTED_CONTRACT_KINDS:
            seen_kinds.add(kind)
        if not owner or not strategy:
            raise HardSwitchCutoverError(
                f"Cutover affectedContracts entry {index} requires owner and strategy"
            )
    missing = _REQUIRED_AFFECTED_CONTRACT_KINDS - seen_kinds
    if missing:
        raise HardSwitchCutoverError(
            "Cutover record missing affected contract kinds: "
            f"{', '.join(sorted(missing))}"
        )
