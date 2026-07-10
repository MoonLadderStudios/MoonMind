"""Live Codex long-running command conformance canary contracts.

The live provider run is intentionally outside required PR CI. This module holds
the compact evidence schema and promotion-gate validation used by both the
provider-verification test and the GHCR stable promotion workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CANARY_SCENARIO_VERSION = "codex-long-command-v1"
DEFAULT_MARKER_PATH = "var/conformance/long_command_result.json"
DEFAULT_MAX_RESULT_AGE_HOURS = 72
MIN_LONG_COMMAND_SECONDS = 3.0

CANARY_PROCESS_ABANDONED = "CANARY_PROCESS_ABANDONED"
CANARY_TERMINAL_MARKER_MISSING = "CANARY_TERMINAL_MARKER_MISSING"
CANARY_SESSION_TERMINATED_EARLY = "CANARY_SESSION_TERMINATED_EARLY"
CANARY_TOOL_PROTOCOL_INCOMPATIBLE = "CANARY_TOOL_PROTOCOL_INCOMPATIBLE"
CANARY_DUPLICATE_EXECUTION = "CANARY_DUPLICATE_EXECUTION"
CANARY_TIMEOUT = "CANARY_TIMEOUT"
CANARY_PROVIDER_UNAVAILABLE = "CANARY_PROVIDER_UNAVAILABLE"
CANARY_CANDIDATE_DIGEST_MISMATCH = "CANARY_CANDIDATE_DIGEST_MISMATCH"
CANARY_RESULT_STALE = "CANARY_RESULT_STALE"
CANARY_EVIDENCE_UNSAFE = "CANARY_EVIDENCE_UNSAFE"

CANARY_FAILURE_CODES = frozenset(
    {
        CANARY_PROCESS_ABANDONED,
        CANARY_TERMINAL_MARKER_MISSING,
        CANARY_SESSION_TERMINATED_EARLY,
        CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
        CANARY_DUPLICATE_EXECUTION,
        CANARY_TIMEOUT,
        CANARY_PROVIDER_UNAVAILABLE,
        CANARY_CANDIDATE_DIGEST_MISMATCH,
        CANARY_RESULT_STALE,
        CANARY_EVIDENCE_UNSAFE,
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ATATT[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(authorization|token|password|api[_-]?key|secret)\s*[=:]\s*\S+"),
)


def _parse_timestamp(value: str | datetime | None, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CodexCanaryValidationError(
                CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
                f"{field_name} must be an ISO-8601 timestamp",
            ) from exc
    else:
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            f"{field_name} is required",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _contains_secret_like_text(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True, default=str)
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


class CodexCanaryValidationError(ValueError):
    """Typed validation failure for conformance canary evidence."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CanaryValidationResult(BaseModel):
    """Compact validation result safe for logs, summaries, and gates."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_code: str | None = Field(None, alias="reasonCode")
    message: str
    candidate_image_digest: str | None = Field(None, alias="candidateImageDigest")
    marker_artifact_ref: str | None = Field(None, alias="markerArtifactRef")
    evidence_artifact_ref: str | None = Field(None, alias="evidenceArtifactRef")


class CodexCanaryMarker(BaseModel):
    """Marker content that must be created only after the helper exits."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    scenario_version: str = Field(..., alias="scenarioVersion")
    nonce: str = Field(..., min_length=8)
    command: str = Field(..., min_length=1)
    process_exit_code: int = Field(..., alias="processExitCode")
    started_at: str = Field(..., alias="startedAt")
    completed_at: str = Field(..., alias="completedAt")
    duration_seconds: float = Field(..., alias="durationSeconds", ge=0)
    output_sha256: str = Field(..., alias="outputSha256", min_length=64, max_length=71)

    @field_validator("scenario_version", "nonce", "command", mode="after")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class CodexCanaryEvidence(BaseModel):
    """Versioned compact evidence published by one live canary run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    issue_ref: str = Field("MoonLadderStudios/MoonMind#3150", alias="issueRef")
    scenario_version: str = Field(..., alias="scenarioVersion")
    candidate_image_digest: str = Field(..., alias="candidateImageDigest")
    candidate_image_ref: str | None = Field(None, alias="candidateImageRef")
    codex_cli_version: str = Field(..., alias="codexCliVersion")
    codex_app_server_version: str | None = Field(None, alias="codexAppServerVersion")
    moonmind_build_sha: str = Field(..., alias="moonmindBuildSha")
    run_id: str = Field(..., alias="runId")
    workflow_id: str = Field(..., alias="workflowId")
    session_id: str = Field(..., alias="sessionId")
    session_ids_observed: list[str] = Field(..., alias="sessionIdsObserved")
    turn_id: str = Field(..., alias="turnId")
    marker_artifact_ref: str = Field(..., alias="markerArtifactRef")
    marker_path: str = Field(DEFAULT_MARKER_PATH, alias="markerPath")
    marker: CodexCanaryMarker
    timestamps: dict[str, str] = Field(default_factory=dict)
    protocol_events: list[str] = Field(default_factory=list, alias="protocolEvents")
    final_agent_status: Literal["completed", "failed", "canceled", "timed_out"] = Field(
        ..., alias="finalAgentStatus"
    )
    agent_run_result_successful: bool = Field(..., alias="agentRunResultSuccessful")
    cleanup_observed: bool = Field(..., alias="cleanupObserved")
    cleanup_session_id: str | None = Field(None, alias="cleanupSessionId")
    github_mutation_count: int = Field(0, alias="githubMutationCount", ge=0)
    process_invocation_count: int = Field(..., alias="processInvocationCount", ge=0)
    marker_artifact_create_count: int = Field(..., alias="markerArtifactCreateCount", ge=0)
    provider_available: bool = Field(True, alias="providerAvailable")
    failure_code: str | None = Field(None, alias="failureCode")
    evidence_artifact_ref: str | None = Field(None, alias="evidenceArtifactRef")

    @field_validator(
        "scenario_version",
        "candidate_image_digest",
        "codex_cli_version",
        "moonmind_build_sha",
        "run_id",
        "workflow_id",
        "session_id",
        "turn_id",
        "marker_artifact_ref",
        "marker_path",
        mode="after",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("failure_code", mode="after")
    @classmethod
    def _validate_failure_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized not in CANARY_FAILURE_CODES:
            raise ValueError(f"unknown canary failure code: {normalized}")
        return normalized


def canary_prompt(*, nonce: str, marker_path: str = DEFAULT_MARKER_PATH) -> str:
    """Return the live Codex canary instruction sent to a managed session."""

    return f"""MoonMind Codex conformance canary for MoonLadderStudios/MoonMind#3150.

Run a harmless foreground shell command that takes at least {MIN_LONG_COMMAND_SECONDS:.0f} seconds,
let the tool call yield a resumable process handle, poll it at least once, and only after it
has truly exited write {marker_path}.

The marker must be JSON with schemaVersion, scenarioVersion, nonce, command,
processExitCode, startedAt, completedAt, durationSeconds, and outputSha256.
Use nonce {nonce}. Do not mutate GitHub, do not create a pull request, and do not
write outside this isolated workspace except {marker_path}. Finish by reporting
the marker path and the process exit code."""


def validate_canary_evidence(
    evidence: CodexCanaryEvidence | Mapping[str, Any],
    *,
    expected_candidate_digest: str | None = None,
    max_age_hours: int = DEFAULT_MAX_RESULT_AGE_HOURS,
    now: datetime | None = None,
) -> CanaryValidationResult:
    """Validate one live canary evidence document for promotion use."""

    try:
        parsed = (
            evidence
            if isinstance(evidence, CodexCanaryEvidence)
            else CodexCanaryEvidence.model_validate(evidence)
        )
        _validate_canary_evidence_or_raise(
            parsed,
            expected_candidate_digest=expected_candidate_digest,
            max_age_hours=max_age_hours,
            now=now,
        )
    except ValidationError as exc:
        return CanaryValidationResult(
            passed=False,
            reasonCode=CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            message=f"Invalid canary evidence schema: {exc.errors()[0]['msg']}",
        )
    except CodexCanaryValidationError as exc:
        candidate_digest = None
        marker_ref = None
        evidence_ref = None
        if "parsed" in locals():
            candidate_digest = parsed.candidate_image_digest
            marker_ref = parsed.marker_artifact_ref
            evidence_ref = parsed.evidence_artifact_ref
        return CanaryValidationResult(
            passed=False,
            reasonCode=exc.reason_code,
            message=str(exc),
            candidateImageDigest=candidate_digest,
            markerArtifactRef=marker_ref,
            evidenceArtifactRef=evidence_ref,
        )

    return CanaryValidationResult(
        passed=True,
        message="Codex conformance canary evidence is valid.",
        candidateImageDigest=parsed.candidate_image_digest,
        markerArtifactRef=parsed.marker_artifact_ref,
        evidenceArtifactRef=parsed.evidence_artifact_ref,
    )


def _validate_canary_evidence_or_raise(
    evidence: CodexCanaryEvidence,
    *,
    expected_candidate_digest: str | None,
    max_age_hours: int,
    now: datetime | None,
) -> None:
    if _contains_secret_like_text(evidence.model_dump(mode="json", by_alias=True)):
        raise CodexCanaryValidationError(
            CANARY_EVIDENCE_UNSAFE,
            "Canary evidence contains secret-like material and must not be published.",
        )
    if evidence.scenario_version != CANARY_SCENARIO_VERSION:
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            f"Unsupported scenario version {evidence.scenario_version!r}.",
        )
    if evidence.marker.scenario_version != evidence.scenario_version:
        raise CodexCanaryValidationError(
            CANARY_TERMINAL_MARKER_MISSING,
            "Marker scenario version does not match evidence scenario version.",
        )
    expected = (expected_candidate_digest or "").strip()
    if expected and evidence.candidate_image_digest != expected:
        raise CodexCanaryValidationError(
            CANARY_CANDIDATE_DIGEST_MISMATCH,
            "Canary result digest does not match the candidate digest.",
        )
    if not evidence.provider_available:
        raise CodexCanaryValidationError(
            CANARY_PROVIDER_UNAVAILABLE,
            "Codex provider was unavailable during the canary run.",
        )
    if evidence.failure_code:
        raise CodexCanaryValidationError(
            evidence.failure_code,
            f"Canary run reported failure code {evidence.failure_code}.",
        )
    if evidence.final_agent_status != "completed" or not evidence.agent_run_result_successful:
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            "Canonical AgentRunResult was not successful.",
        )
    if evidence.github_mutation_count != 0:
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            "Canary evidence reports a GitHub mutation.",
        )
    if evidence.process_invocation_count != 1 or evidence.marker_artifact_create_count != 1:
        raise CodexCanaryValidationError(
            CANARY_DUPLICATE_EXECUTION,
            "Canary must create exactly one helper process and one marker artifact.",
        )
    if evidence.marker_path != DEFAULT_MARKER_PATH:
        raise CodexCanaryValidationError(
            CANARY_TERMINAL_MARKER_MISSING,
            f"Marker path must be {DEFAULT_MARKER_PATH}.",
        )
    if evidence.marker.process_exit_code != 0:
        raise CodexCanaryValidationError(
            CANARY_PROCESS_ABANDONED,
            "Helper process did not exit successfully.",
        )
    if evidence.marker.duration_seconds < MIN_LONG_COMMAND_SECONDS:
        raise CodexCanaryValidationError(
            CANARY_PROCESS_ABANDONED,
            "Helper process did not outlive the initial tool yield window.",
        )
    if len(set(evidence.session_ids_observed)) != 1:
        raise CodexCanaryValidationError(
            CANARY_SESSION_TERMINATED_EARLY,
            "More than one managed session id was observed during the canary.",
        )
    if evidence.session_ids_observed[0] != evidence.session_id:
        raise CodexCanaryValidationError(
            CANARY_SESSION_TERMINATED_EARLY,
            "Observed managed session id does not match terminal session id.",
        )
    if evidence.cleanup_observed and evidence.cleanup_session_id not in {None, evidence.session_id}:
        raise CodexCanaryValidationError(
            CANARY_SESSION_TERMINATED_EARLY,
            "Cleanup targeted a different managed session.",
        )

    timestamps = evidence.timestamps
    ordered_fields = (
        "processStart",
        "firstToolYield",
        "subsequentPoll",
        "processComplete",
        "markerCreation",
        "turnComplete",
        "cleanup",
    )
    parsed_times = {
        field: _parse_timestamp(timestamps.get(field), field_name=f"timestamps.{field}")
        for field in ordered_fields
    }
    if not (
        parsed_times["processStart"]
        < parsed_times["firstToolYield"]
        < parsed_times["subsequentPoll"]
        < parsed_times["processComplete"]
        <= parsed_times["markerCreation"]
        <= parsed_times["turnComplete"]
        <= parsed_times["cleanup"]
    ):
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            "Canary timestamps do not prove yield, poll, marker, turn, cleanup order.",
        )
    marker_started = _parse_timestamp(evidence.marker.started_at, field_name="marker.startedAt")
    marker_completed = _parse_timestamp(
        evidence.marker.completed_at,
        field_name="marker.completedAt",
    )
    if marker_started != parsed_times["processStart"]:
        raise CodexCanaryValidationError(
            CANARY_TERMINAL_MARKER_MISSING,
            "Marker process start timestamp does not match evidence.",
        )
    if marker_completed != parsed_times["processComplete"]:
        raise CodexCanaryValidationError(
            CANARY_TERMINAL_MARKER_MISSING,
            "Marker process completion timestamp does not match evidence.",
        )
    current_time = (now or datetime.now(tz=UTC)).astimezone(UTC)
    if current_time - parsed_times["turnComplete"] > timedelta(hours=max_age_hours):
        raise CodexCanaryValidationError(
            CANARY_RESULT_STALE,
            "Canary result is too old for promotion.",
        )
    if not any(event == "resumable_process_handle" for event in evidence.protocol_events):
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            "Canary evidence did not record a resumable process handle.",
        )
    if not any(event == "poll_after_yield" for event in evidence.protocol_events):
        raise CodexCanaryValidationError(
            CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
            "Canary evidence did not record a poll after the initial yield.",
        )


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate Codex conformance canary evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate one evidence JSON file")
    check.add_argument("--result", required=True, type=Path)
    check.add_argument("--candidate-digest", default="")
    check.add_argument("--max-age-hours", type=int, default=DEFAULT_MAX_RESULT_AGE_HOURS)
    args = parser.parse_args(argv)

    if args.command == "check":
        result = validate_canary_evidence(
            _load_json(args.result),
            expected_candidate_digest=args.candidate_digest or None,
            max_age_hours=args.max_age_hours,
        )
        print(result.model_dump_json(by_alias=True))
        return 0 if result.passed else 1
    return 64


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
