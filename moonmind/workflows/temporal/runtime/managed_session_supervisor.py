"""Session-level supervision for durable managed session records."""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from moonmind.codex_conformance.canary import (
    CANARY_SCENARIO_VERSION,
    DEFAULT_MARKER_PATH,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord

from .log_streamer import RuntimeLogStreamer
from .managed_session_store import ManagedSessionStore
from .session_observability_bridge import ManagedSessionObservabilityBridge

logger = logging.getLogger(__name__)

_LOG_READ_CHUNK_BYTES = 64 * 1024
_SESSION_STATE_FILENAME = ".moonmind-codex-session-state.json"
_CANARY_EVIDENCE_ARTIFACT_NAME = "codex_conformance_canary.evidence.json"
_CANARY_MARKER_ARTIFACT_NAME = "codex_conformance_canary.marker.json"
_CANARY_PROTOCOL_EVENT_KEY = "codexCanaryProtocolEvent"
_CANARY_MUTATION_MARKERS = (
    "github mutation",
    "create pull request",
    "created pull request",
    "gh pr create",
    "gh issue",
    "api.github.com",
)

class ArtifactStorageWriter(Protocol):
    def write_artifact(
        self,
        *,
        job_id: str,
        artifact_name: str,
        data: bytes,
    ) -> tuple[Path, str]:
        pass

class ManagedSessionSupervisor:
    """Track session spool progress and publish durable observability artifacts."""

    def __init__(
        self,
        *,
        store: ManagedSessionStore,
        log_streamer: RuntimeLogStreamer,
        artifact_storage: ArtifactStorageWriter,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._log_streamer = log_streamer
        self._artifact_storage = artifact_storage
        self._poll_interval_seconds = poll_interval_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    @staticmethod
    def _stdout_path(record: CodexManagedSessionRecord) -> Path:
        return Path(record.artifact_spool_path) / "stdout.log"

    @staticmethod
    def _stderr_path(record: CodexManagedSessionRecord) -> Path:
        return Path(record.artifact_spool_path) / "stderr.log"

    @staticmethod
    def _session_state_path(record: CodexManagedSessionRecord) -> Path:
        return Path(record.session_workspace_path) / _SESSION_STATE_FILENAME

    @staticmethod
    def _initial_stream_offsets(
        record: CodexManagedSessionRecord,
    ) -> dict[str, int]:
        return {
            "stdout": record.stdout_log_offset or 0,
            "stderr": record.stderr_log_offset or 0,
        }

    def _publish_output_chunk(
        self,
        *,
        record: CodexManagedSessionRecord,
        stream_name: str,
        text: str,
        offset: int,
    ) -> None:
        try:
            bridge = ManagedSessionObservabilityBridge(
                log_streamer=self._log_streamer,
                run_id=record.agent_run_id,
                workspace_path=record.workspace_path,
                session_id=record.session_id,
                session_epoch=record.session_epoch,
                container_id=record.container_id,
                thread_id=record.thread_id,
                active_turn_id=record.active_turn_id,
            )
            bridge.emit(
                stream=stream_name,
                text=text,
                kind=f"{stream_name}_chunk",
                offset=offset,
                metadata={"source": "managed_session_artifact_spool"},
                preserve_text=True,
            )
        except Exception:
            logger.warning(
                "Session output publication failed for agent run %s session %s stream %s",
                record.agent_run_id,
                record.session_id,
                stream_name,
                exc_info=True,
            )

    def _publish_new_output_chunks(
        self,
        record: CodexManagedSessionRecord,
        stream_offsets: dict[str, int],
        stream_decoders: dict[str, codecs.IncrementalDecoder],
    ) -> bool:
        emitted = False
        for stream_name, path in (
            ("stdout", self._stdout_path(record)),
            ("stderr", self._stderr_path(record)),
        ):
            try:
                if not path.exists():
                    stream_offsets.setdefault(stream_name, 0)
                    continue
                current_size = path.stat().st_size
                previous_offset = stream_offsets.get(stream_name, 0)
                if current_size < previous_offset:
                    previous_offset = 0
                    stream_decoders.pop(stream_name, None)
                if current_size <= previous_offset:
                    stream_offsets[stream_name] = current_size
                    continue
                decoder = stream_decoders.setdefault(
                    stream_name,
                    codecs.getincrementaldecoder("utf-8")(errors="replace"),
                )
                committed_offset = previous_offset
                with path.open("rb") as handle:
                    handle.seek(previous_offset)
                    remaining = current_size - previous_offset
                    while remaining > 0:
                        chunk = handle.read(min(_LOG_READ_CHUNK_BYTES, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        text = decoder.decode(chunk, final=False)
                        buffered_bytes = len(decoder.getstate()[0])
                        decoded_offset = handle.tell() - buffered_bytes
                        if not text:
                            continue
                        self._publish_output_chunk(
                            record=record,
                            stream_name=stream_name,
                            text=text,
                            offset=committed_offset,
                        )
                        committed_offset = decoded_offset
                        emitted = True
                if decoder.getstate()[0]:
                    decoder.reset()
                stream_offsets[stream_name] = committed_offset
            except OSError:
                continue
        return emitted

    @staticmethod
    def _runtime_state_active_turn_id(
        record: CodexManagedSessionRecord,
    ) -> str | None:
        state_path = ManagedSessionSupervisor._session_state_path(record)
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if str(payload.get("sessionId") or "").strip() != record.session_id:
            return None
        if str(payload.get("containerId") or "").strip() != record.container_id:
            return None
        if str(payload.get("logicalThreadId") or "").strip() != record.thread_id:
            return None
        try:
            session_epoch = int(payload.get("sessionEpoch"))
        except (TypeError, ValueError):
            return None
        if session_epoch != record.session_epoch:
            return None
        last_turn_status = str(payload.get("lastTurnStatus") or "").strip().lower()
        active_turn_id = str(payload.get("activeTurnId") or "").strip() or None
        if active_turn_id and last_turn_status in {"accepted", "running"}:
            return active_turn_id
        return None

    async def _sync_active_turn_state(
        self,
        record: CodexManagedSessionRecord,
    ) -> CodexManagedSessionRecord:
        active_turn_id = self._runtime_state_active_turn_id(record)
        if not active_turn_id:
            return record
        if record.active_turn_id == active_turn_id and record.status == "busy":
            return record

        updated = await self._store.update(
            record.session_id,
            active_turn_id=active_turn_id,
            status="busy",
            updated_at=datetime.now(tz=UTC),
        )
        if record.active_turn_id != active_turn_id:
            self.emit_session_event(
                record=updated,
                kind="turn_started",
                text=f"Turn started: {active_turn_id}.",
                turn_id=active_turn_id,
                active_turn_id=active_turn_id,
                metadata={
                    "action": "send_turn",
                    "source": "managed_session_runtime_state",
                },
            )
        return updated

    def emit_session_event(
        self,
        *,
        record: CodexManagedSessionRecord,
        text: str,
        kind: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Publish one session-aware observability row into the run-level stream."""
        try:
            bridge = ManagedSessionObservabilityBridge(
                log_streamer=self._log_streamer,
                run_id=record.agent_run_id,
                workspace_path=record.workspace_path,
                session_id=record.session_id,
                session_epoch=record.session_epoch,
                container_id=record.container_id,
                thread_id=record.thread_id,
                active_turn_id=record.active_turn_id,
            )
            bridge.session_event(
                text=text,
                kind=kind,
                turn_id=turn_id,
                active_turn_id=active_turn_id,
                metadata=dict(metadata or {}),
            )
        except Exception:
            logger.warning(
                "Session observability publication failed for agent run %s session %s kind %s",
                record.agent_run_id,
                record.session_id,
                kind,
                exc_info=True,
            )

    async def _watch(self, session_id: str) -> None:
        stop_event = self._stop_events[session_id]
        initial_record = self._store.load(session_id)
        stream_offsets = (
            self._initial_stream_offsets(initial_record)
            if initial_record is not None
            else {"stdout": 0, "stderr": 0}
        )
        stream_decoders: dict[str, codecs.IncrementalDecoder] = {}
        while not stop_event.is_set():
            record = self._store.load(session_id)
            if record is None:
                return
            record = await self._sync_active_turn_state(record)
            emitted = self._publish_new_output_chunks(
                record,
                stream_offsets,
                stream_decoders,
            )
            combined_offset = sum(stream_offsets.values())
            if emitted or combined_offset != (record.last_log_offset or 0):
                await self._store.update(
                    session_id,
                    last_log_offset=combined_offset,
                    stdout_log_offset=stream_offsets.get("stdout", 0),
                    stderr_log_offset=stream_offsets.get("stderr", 0),
                    last_log_at=datetime.now(tz=UTC),
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def start(self, record: CodexManagedSessionRecord) -> None:
        existing = self._tasks.get(record.session_id)
        if existing is not None and not existing.done():
            return
        stop_event = asyncio.Event()
        self._stop_events[record.session_id] = stop_event
        self._tasks[record.session_id] = asyncio.create_task(
            self._watch(record.session_id)
        )

    @staticmethod
    def _read_spool_bytes(record: CodexManagedSessionRecord) -> tuple[bytes, bytes]:
        stdout_bytes = b""
        stderr_bytes = b""
        stdout_path = ManagedSessionSupervisor._stdout_path(record)
        stderr_path = ManagedSessionSupervisor._stderr_path(record)
        if stdout_path.exists():
            stdout_bytes = stdout_path.read_bytes()
        if stderr_path.exists():
            stderr_bytes = stderr_path.read_bytes()
        return stdout_bytes, stderr_bytes

    def _write_json_artifact(
        self,
        *,
        job_id: str,
        artifact_name: str,
        payload: dict[str, object],
    ) -> str:
        _path, ref = self._artifact_storage.write_artifact(
            job_id=job_id,
            artifact_name=artifact_name,
            data=(json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return ref

    @staticmethod
    def _event_timestamp(event: dict[str, Any]) -> str | None:
        timestamp = str(event.get("timestamp") or "").strip()
        return timestamp or None

    @staticmethod
    def _event_kind(event: dict[str, Any]) -> str:
        return str(event.get("kind") or "").strip().lower()

    @staticmethod
    def _event_text(event: dict[str, Any]) -> str:
        return str(event.get("text") or "").strip()

    @staticmethod
    def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
        metadata = event.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}

    @classmethod
    def _event_protocol_marker(cls, event: dict[str, Any]) -> str | None:
        metadata = cls._event_metadata(event)
        marker = str(metadata.get(_CANARY_PROTOCOL_EVENT_KEY) or "").strip()
        if marker in {"resumable_process_handle", "poll_after_yield"}:
            return marker
        return None

    @classmethod
    def _is_tool_event(cls, event: dict[str, Any]) -> bool:
        kind = cls._event_kind(event)
        text = cls._event_text(event).lower()
        return kind in {
            "tool_call",
            "tool_call_started",
            "tool_call_completed",
            "tool_call_failed",
            "tool_output",
        } or text.startswith("tool call:")

    @classmethod
    def _github_mutation_count(cls, events: list[dict[str, Any]]) -> int:
        count = 0
        for event in events:
            metadata = cls._event_metadata(event)
            if metadata.get("githubMutation") is True:
                count += 1
                continue
            text = " ".join(
                (
                    cls._event_kind(event),
                    cls._event_text(event).lower(),
                    json.dumps(metadata, sort_keys=True, default=str).lower(),
                )
            )
            if any(marker in text for marker in _CANARY_MUTATION_MARKERS):
                count += 1
        return count

    @classmethod
    def _max_event_int(
        cls,
        events: list[dict[str, Any]],
        key: str,
        *,
        default: int,
    ) -> int:
        values = [default]
        for event in events:
            value = cls._event_metadata(event).get(key)
            if isinstance(value, bool):
                continue
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                continue
        return max(values)

    @classmethod
    def _canary_protocol_events(
        cls,
        events: list[dict[str, Any]],
    ) -> tuple[list[str], dict[str, str]]:
        protocol_events: list[str] = []
        timestamps: dict[str, str] = {}
        tool_event_timestamps: list[str] = []
        for event in events:
            timestamp = cls._event_timestamp(event)
            marker = cls._event_protocol_marker(event)
            if marker is not None:
                if marker not in protocol_events:
                    protocol_events.append(marker)
                    if timestamp is not None:
                        key = (
                            "firstToolYield"
                            if marker == "resumable_process_handle"
                            else "subsequentPoll"
                        )
                        timestamps.setdefault(key, timestamp)
                continue
            if cls._is_tool_event(event) and timestamp is not None:
                tool_event_timestamps.append(timestamp)

        return protocol_events, timestamps

    @staticmethod
    def _session_ids_observed(
        record: CodexManagedSessionRecord,
        events: list[dict[str, Any]],
    ) -> list[str]:
        observed: list[str] = []
        for event in events:
            session_id = str(event.get("sessionId") or "").strip()
            if session_id and session_id not in observed:
                observed.append(session_id)
        if not observed:
            observed.append(record.session_id)
        return observed

    @classmethod
    def _build_codex_canary_observation(
        cls,
        *,
        record: CodexManagedSessionRecord,
        status: str,
        marker_ref: str,
        evidence_ref: str,
        events: list[dict[str, Any]],
        marker: dict[str, Any],
        cleanup_timestamp: str,
    ) -> dict[str, Any] | None:
        if marker.get("scenarioVersion") != CANARY_SCENARIO_VERSION:
            return None
        if str(marker.get("nonce") or "").strip() == "":
            return None
        process_start = str(marker.get("startedAt") or "").strip()
        process_complete = str(marker.get("completedAt") or "").strip()
        if not process_start or not process_complete:
            return None

        protocol_events, event_timestamps = cls._canary_protocol_events(events)
        if not {"resumable_process_handle", "poll_after_yield"}.issubset(
            set(protocol_events)
        ):
            return None
        if (
            "firstToolYield" not in event_timestamps
            or "subsequentPoll" not in event_timestamps
        ):
            return None

        turn_events = [
            event
            for event in events
            if cls._event_kind(event) in {"turn_completed", "turn_failed"}
        ]
        turn_complete = (
            cls._event_timestamp(turn_events[-1]) if turn_events else cleanup_timestamp
        )
        marker_created_at = (
            str(marker.get("createdAt") or "").strip()
            or process_complete
        )
        turn_id = str(record.active_turn_id or "").strip()
        for event in reversed(events):
            event_turn_id = str(
                event.get("turnId") or event.get("activeTurnId") or ""
            ).strip()
            if event_turn_id:
                turn_id = event_turn_id
                break
        if not turn_id:
            return None

        return {
            "schemaVersion": "v1",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "testedImageDigest": str(
                record.metadata.get("testedImageDigest")
                or record.metadata.get("runtimeImageDigest")
                or record.metadata.get("candidateImageDigest")
                or ""
            ).strip(),
            "sessionId": record.session_id,
            "sessionIdsObserved": cls._session_ids_observed(record, events),
            "turnId": turn_id,
            "markerArtifactRef": marker_ref,
            "markerPath": DEFAULT_MARKER_PATH,
            "timestamps": {
                "processStart": process_start,
                "firstToolYield": event_timestamps["firstToolYield"],
                "subsequentPoll": event_timestamps["subsequentPoll"],
                "processComplete": process_complete,
                "markerCreation": marker_created_at,
                "turnComplete": turn_complete or cleanup_timestamp,
                "cleanup": cleanup_timestamp,
            },
            "protocolEvents": protocol_events,
            "cleanupObserved": status in {"terminated", "failed", "canceled"},
            "cleanupSessionId": record.session_id,
            "githubMutationCount": cls._github_mutation_count(events),
            "processInvocationCount": cls._max_event_int(
                events,
                "processInvocationCount",
                default=1,
            ),
            "markerArtifactCreateCount": cls._max_event_int(
                events,
                "markerArtifactCreateCount",
                default=1,
            ),
            "evidenceArtifactRef": evidence_ref,
        }

    def _publish_codex_canary_observation(
        self,
        *,
        record: CodexManagedSessionRecord,
        status: str,
        observability_events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        marker_path = Path(record.workspace_path) / DEFAULT_MARKER_PATH
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(marker, dict):
            return None

        marker_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name=_CANARY_MARKER_ARTIFACT_NAME,
            payload=marker,
        )
        cleanup_timestamp = datetime.now(tz=UTC).isoformat()
        evidence_ref = f"{record.session_id}/{_CANARY_EVIDENCE_ARTIFACT_NAME}"
        observation = self._build_codex_canary_observation(
            record=record,
            status=status,
            marker_ref=marker_ref,
            evidence_ref=evidence_ref,
            events=observability_events,
            marker=marker,
            cleanup_timestamp=cleanup_timestamp,
        )
        if observation is None:
            return None
        observation["marker"] = marker
        evidence_payload = {
            "codexConformanceCanary": observation,
            "marker": marker,
        }
        actual_evidence_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name=_CANARY_EVIDENCE_ARTIFACT_NAME,
            payload=evidence_payload,
        )
        observation["evidenceArtifactRef"] = actual_evidence_ref
        return observation

    @staticmethod
    def _summary_payload(
        *,
        record: CodexManagedSessionRecord,
        status: str,
        stdout_ref: str,
        stderr_ref: str,
        diagnostics_ref: str | None,
        error_message: str | None,
        canary_observation: dict[str, Any] | None = None,
        marker: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "sessionId": record.session_id,
            "sessionEpoch": record.session_epoch,
            "containerId": record.container_id,
            "threadId": record.thread_id,
            "status": status,
            "stdoutArtifactRef": stdout_ref,
            "stderrArtifactRef": stderr_ref,
            "diagnosticsRef": diagnostics_ref,
            "errorMessage": error_message,
        }
        if canary_observation is not None:
            payload["metadata"] = {
                "codexConformanceCanary": canary_observation,
                "canaryEvidence": {
                    "codexConformanceCanary": canary_observation,
                    "marker": marker or {},
                },
            }
        return payload

    @staticmethod
    def _checkpoint_payload(
        *,
        record: CodexManagedSessionRecord,
        status: str,
        stdout_ref: str,
        stderr_ref: str,
        diagnostics_ref: str | None,
        canary_observation: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "sessionState": record.session_state().model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "status": status,
            "runtimeArtifacts": {
                "stdout": stdout_ref,
                "stderr": stderr_ref,
                "diagnostics": diagnostics_ref,
            },
            "recordUpdatedAt": datetime.now(tz=UTC).isoformat(),
        }
        if canary_observation is not None:
            payload["metadata"] = {"codexConformanceCanary": canary_observation}
        return payload

    async def _publish_record(
        self,
        record: CodexManagedSessionRecord,
        *,
        status: str,
        error_message: str | None,
    ) -> CodexManagedSessionRecord:
        stdout_bytes, stderr_bytes = self._read_spool_bytes(record)
        _, stdout_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name="stdout.log",
            data=stdout_bytes,
        )
        _, stderr_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name="stderr.log",
            data=stderr_bytes,
        )
        observability_events = self._log_streamer.consume_observability_events(record.agent_run_id)
        summary_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.summary.json",
            payload=self._summary_payload(
                record=record,
                status=status,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
                diagnostics_ref=None,
                error_message=error_message,
            ),
        )
        checkpoint_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.step_checkpoint.json",
            payload=self._checkpoint_payload(
                record=record,
                status=status,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
                diagnostics_ref=None,
            ),
        )
        self.emit_session_event(
            record=record,
            kind="summary_published",
            text=f"Session summary published for {record.session_id}.",
            metadata={"summaryRef": summary_ref, "status": status},
        )
        self.emit_session_event(
            record=record,
            kind="checkpoint_published",
            text=f"Session checkpoint published for {record.session_id}.",
            metadata={"checkpointRef": checkpoint_ref, "status": status},
        )
        observability_events.extend(
            self._log_streamer.consume_observability_events(record.agent_run_id)
        )
        canary_observation = self._publish_codex_canary_observation(
            record=record,
            status=status,
            observability_events=observability_events,
        )
        if canary_observation is not None:
            observability_events.append(
                {
                    "stream": "session",
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                    "text": "Codex conformance canary observation published.",
                    "kind": "codex_conformance_canary",
                    "sessionId": record.session_id,
                    "sessionEpoch": record.session_epoch,
                    "containerId": record.container_id,
                    "threadId": record.thread_id,
                    "turnId": canary_observation["turnId"],
                    "metadata": {
                        "codexConformanceCanary": canary_observation,
                    },
                }
            )
        diagnostics_ref = self._log_streamer.collect_diagnostics(
            run_id=record.session_id,
            exit_code=None,
            duration_seconds=0.0,
            log_refs={"stdout": stdout_ref, "stderr": stderr_ref},
            annotations=[],
            events=[],
            observability_events=observability_events,
        )
        summary_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.summary.json",
            payload=self._summary_payload(
                record=record,
                status=status,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
                diagnostics_ref=diagnostics_ref,
                error_message=error_message,
                canary_observation=canary_observation,
                marker=(
                    canary_observation.get("marker")
                    if isinstance(canary_observation, dict)
                    else None
                ),
            ),
        )
        checkpoint_ref = self._write_json_artifact(
            job_id=record.session_id,
            artifact_name="session.step_checkpoint.json",
            payload=self._checkpoint_payload(
                record=record,
                status=status,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
                diagnostics_ref=diagnostics_ref,
                canary_observation=canary_observation,
            ),
        )
        observability_events_ref = await asyncio.to_thread(
            self._log_streamer.persist_observability_events,
            run_id=record.agent_run_id,
            workspace_path=record.workspace_path,
            artifact_job_id=record.session_id,
        )
        now = datetime.now(tz=UTC)
        metadata = dict(record.metadata)
        if canary_observation is not None:
            metadata["codexConformanceCanary"] = canary_observation
            metadata["canaryEvidence"] = canary_observation
        return await self._store.update(
            record.session_id,
            status=status,
            stdout_artifact_ref=stdout_ref,
            stderr_artifact_ref=stderr_ref,
            diagnostics_ref=diagnostics_ref,
            observability_events_ref=observability_events_ref,
            latest_summary_ref=summary_ref,
            latest_checkpoint_ref=checkpoint_ref,
            last_log_offset=len(stdout_bytes) + len(stderr_bytes),
            stdout_log_offset=len(stdout_bytes),
            stderr_log_offset=len(stderr_bytes),
            last_log_at=now,
            updated_at=now,
            error_message=error_message,
            metadata=metadata,
        )

    async def publish_snapshot(self, session_id: str) -> CodexManagedSessionRecord:
        record = self._store.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        return await self._publish_record(
            record,
            status=record.status,
            error_message=record.error_message,
        )

    async def publish_reset_artifacts(
        self,
        *,
        previous_record: CodexManagedSessionRecord,
        record: CodexManagedSessionRecord,
        action: str,
        reason: str | None,
    ) -> CodexManagedSessionRecord:
        if record.session_id != previous_record.session_id:
            raise ValueError("reset artifact publication requires one session_id")

        epoch = record.session_epoch
        recorded_at = datetime.now(tz=UTC)
        control_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name=f"session.control_event.epoch-{epoch}.json",
            data=(
                json.dumps(
                    {
                        "linkType": "session.control_event",
                        "action": action,
                        "sessionId": record.session_id,
                        "agentRunId": record.agent_run_id,
                        "containerId": record.container_id,
                        "previousSessionEpoch": previous_record.session_epoch,
                        "newSessionEpoch": record.session_epoch,
                        "previousThreadId": previous_record.thread_id,
                        "newThreadId": record.thread_id,
                        "reason": reason,
                        "recordedAt": recorded_at.isoformat(),
                        "metadata": dict(record.metadata),
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )[1]
        boundary_ref = self._artifact_storage.write_artifact(
            job_id=record.session_id,
            artifact_name=f"session.reset_boundary.epoch-{epoch}.json",
            data=(
                json.dumps(
                    {
                        "linkType": "session.reset_boundary",
                        "boundaryKind": action,
                        "sessionId": record.session_id,
                        "agentRunId": record.agent_run_id,
                        "containerId": record.container_id,
                        "sessionEpoch": record.session_epoch,
                        "threadId": record.thread_id,
                        "previousSessionEpoch": previous_record.session_epoch,
                        "previousThreadId": previous_record.thread_id,
                        "recordedAt": recorded_at.isoformat(),
                        "metadata": dict(record.metadata),
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )[1]
        self.emit_session_event(
            record=record,
            kind="session_cleared",
            text=(
                f"Session cleared. Epoch {previous_record.session_epoch} -> "
                f"{record.session_epoch}; thread {previous_record.thread_id} -> {record.thread_id}."
            ),
            metadata={
                "action": action,
                "reason": reason,
                "previousSessionEpoch": previous_record.session_epoch,
                "newSessionEpoch": record.session_epoch,
                "previousThreadId": previous_record.thread_id,
                "newThreadId": record.thread_id,
                "controlEventRef": control_ref,
            },
        )
        self.emit_session_event(
            record=record,
            kind="session_reset_boundary",
            text=(
                f"Epoch boundary reached. Session {record.session_id} is now on "
                f"epoch {record.session_epoch} thread {record.thread_id}."
            ),
            metadata={
                "action": action,
                "reason": reason,
                "previousSessionEpoch": previous_record.session_epoch,
                "newSessionEpoch": record.session_epoch,
                "previousThreadId": previous_record.thread_id,
                "newThreadId": record.thread_id,
                "resetBoundaryRef": boundary_ref,
            },
        )
        return await self._store.update(
            record.session_id,
            latest_control_event_ref=control_ref,
            latest_reset_boundary_ref=boundary_ref,
            updated_at=recorded_at,
        )

    async def finalize(
        self,
        session_id: str,
        *,
        status: str,
        error_message: str | None = None,
    ) -> CodexManagedSessionRecord:
        stop_event = self._stop_events.pop(session_id, None)
        if stop_event is not None:
            stop_event.set()
        task = self._tasks.pop(session_id, None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

        record = self._store.load(session_id)
        if record is None:
            raise ValueError(f"managed session record not found: {session_id}")
        return await self._publish_record(
            record,
            status=status,
            error_message=error_message,
        )
