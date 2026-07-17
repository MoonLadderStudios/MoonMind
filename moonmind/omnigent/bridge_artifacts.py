"""Artifact publication boundary for Omnigent bridge execution.

Owned by MM-1158: this module publishes bridge evidence as MoonMind artifact
refs and assembles the terminal ``AgentRunResult`` returned to workflows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from re import sub
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.failure_classification import (
    OmnigentFailureReason,
    classify_omnigent_failure,
    failure_class_for_terminal_status,
)
from moonmind.omnigent.settings import resolved_server_url
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

_MAX_OMNIGENT_HARVEST_ITEMS = 100
_CAPTURE_MANIFEST_SCHEMA_VERSION = 1


class OmnigentContractError(RuntimeError):
    """Raised when Omnigent emits an unsupported adapter contract value."""


class OmnigentArtifactError(RuntimeError):
    """Raised when Omnigent artifact evidence cannot be read or written."""


@dataclass(slots=True)
class OmnigentCaptureBundle:
    """MoonMind artifact refs captured for one Omnigent session."""

    output_refs: list[str] = field(default_factory=list)
    diagnostics_ref: str = ""
    capture_manifest_ref: str = ""
    external_state_ref: str = ""
    metadata_refs: dict[str, str] = field(default_factory=dict)
    optional_harvest_failed: bool = False
    resource_harvest_failure_class: str | None = None


class OmnigentArtifactGateway:
    """Minimal artifact boundary needed by the Omnigent activity."""

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        raise NotImplementedError

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        raise NotImplementedError

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        raise NotImplementedError

    async def read_text(self, artifact_ref: str) -> str:
        raise NotImplementedError

    async def read_bytes(self, artifact_ref: str) -> bytes:
        return (await self.read_text(artifact_ref)).encode("utf-8")


class LocalOmnigentArtifactGateway(OmnigentArtifactGateway):
    """Local MoonMind artifact gateway for Omnigent evidence capture."""

    def __init__(
        self,
        *,
        root: str | Path = "var/artifacts/omnigent",
        readable_refs: dict[str, str] | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._readable_refs = dict(readable_refs or {})

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        data = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return await self.write_text(
            request=request,
            name=name,
            payload=f"{data}\n",
            link_type=link_type,
            content_type="application/json",
        )

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        return await self.write_bytes(
            request=request,
            name=name,
            payload=payload.encode("utf-8"),
            link_type=link_type,
            content_type=content_type,
        )

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe_correlation = _safe_artifact_segment(request.correlation_id)
        safe_name = _safe_artifact_name(name)
        path = (self._root / safe_correlation / safe_name).resolve()
        if not path.is_relative_to(self._root):
            raise OmnigentArtifactError("Omnigent artifact path escapes artifact root")
        digest = hashlib.sha256(payload).hexdigest()
        metadata_path = path.with_suffix(f"{path.suffix}.metadata.json")
        metadata_payload = (
            json.dumps(
                {
                    "contentType": content_type,
                    "linkType": link_type,
                    "sha256": digest,
                    "sizeBytes": len(payload),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        # Surface filesystem persistence failures (disk full, permission,
        # missing directory) as OmnigentArtifactError so the §17 required
        # artifact-persistence handler classifies them instead of letting a
        # raw OSError escape the activity.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            metadata_path.write_text(metadata_payload, encoding="utf-8")
        except OSError as exc:
            raise OmnigentArtifactError(
                f"Unable to persist Omnigent artifact '{safe_name}': {exc}"
            ) from exc
        return f"artifact://omnigent/{safe_correlation}/{safe_name}"

    async def read_text(self, artifact_ref: str) -> str:
        if artifact_ref in self._readable_refs:
            return self._readable_refs[artifact_ref]
        prefix = "artifact://omnigent/"
        if artifact_ref.startswith(prefix):
            relative = artifact_ref[len(prefix) :]
            path = (self._root / relative).resolve()
            if not path.is_relative_to(self._root):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if path.is_file():
                return path.read_text(encoding="utf-8")
        raise OmnigentArtifactError(f"Unable to dereference artifact ref: {artifact_ref}")

    async def read_bytes(self, artifact_ref: str) -> bytes:
        if artifact_ref in self._readable_refs:
            return self._readable_refs[artifact_ref].encode("utf-8")
        prefix = "artifact://omnigent/"
        if artifact_ref.startswith(prefix):
            relative = artifact_ref[len(prefix) :]
            path = (self._root / relative).resolve()
            if path.is_relative_to(self._root) and path.is_file():
                return path.read_bytes()
        raise OmnigentArtifactError(f"Unable to dereference artifact ref: {artifact_ref}")


def _safe_artifact_segment(value: object) -> str:
    text = sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    if text in {".", ".."}:
        return "segment"
    return text[:120] or "run"


def _safe_artifact_name(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    parts = [_safe_artifact_segment(part) for part in text.split("/") if part.strip()]
    return "/".join(parts) or "artifact"


async def _capture_artifact_json(
    gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    refs: dict[str, str],
    *,
    key: str,
    name: str,
    payload: Any,
    link_type: str,
) -> str:
    ref = await gateway.write_json(
        request=request,
        name=name,
        payload=payload,
        link_type=link_type,
    )
    refs[key] = ref
    return ref


async def capture_artifact_json(
    gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    refs: dict[str, str],
    *,
    key: str,
    name: str,
    payload: Any,
    link_type: str,
) -> str:
    return await _capture_artifact_json(
        gateway,
        request,
        refs,
        key=key,
        name=name,
        payload=payload,
        link_type=link_type,
    )


def _compact_summary(value: object | None, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:4096]


# Diff/patch capture is capability-probed and never fatal on its own (§12.3), so
# `workspaceDiffsUnavailable`/`patchUnavailable` are intentionally excluded here.
_HARVEST_UNAVAILABLE_KEYS = (
    "changedFilesUnavailable",
    "workspaceFilesUnavailable",
    "sessionFilesUnavailable",
)


def _capture_requires_full_evidence(capture_policy: dict[str, Any] | None) -> bool:
    if not capture_policy:
        return False
    return bool(capture_policy.get("requireFullEvidence", False))


def _optional_resource_harvest_failed(manifest: dict[str, Any]) -> bool:
    """True when an optional resource-harvest step recorded an unavailable row.

    Diff/patch capability probes are excluded because §12.3 keeps them
    non-fatal; only changed-file, workspace-file, and session-file harvest
    failures count toward the §17 optional-resource-harvest outcome.
    """

    if any(manifest.get(key) for key in _HARVEST_UNAVAILABLE_KEYS):
        return True
    return any(
        isinstance(item, dict) and item.get("unavailable")
        for group in ("changedFiles", "workspaceFiles", "sessionFiles")
        for item in (manifest.get(group) or [])
    )


def build_omnigent_result(
    *,
    request: AgentExecutionRequest,
    terminal_status: str,
    session_id: str,
    agent_id: str | None,
    final_snapshot: dict[str, Any],
    event_count: int,
    capture_bundle: OmnigentCaptureBundle,
    failure_summary: str | None = None,
    provider_error_code: str | None = None,
    failure_reason: OmnigentFailureReason | None = None,
    require_full_evidence: bool = False,
) -> AgentRunResult:
    """Build compact terminal canonical result for Omnigent.

    ``failure_reason`` selects an explicit §17 classifier row (for example a
    first-message digest mismatch that must map to ``user_error`` even though
    the terminal status is ``failed``). When omitted, the failure class is
    derived from the terminal status via the same §17 classifier.
    """

    output_refs = list(capture_bundle.output_refs)
    diagnostics_ref = capture_bundle.diagnostics_ref
    if not output_refs:
        raise OmnigentContractError("Omnigent result requires MoonMind output artifact refs")
    if not diagnostics_ref:
        raise OmnigentContractError("Omnigent result requires a MoonMind diagnostics artifact ref")
    _assert_no_provider_native_refs(
        [*output_refs, diagnostics_ref, *capture_bundle.metadata_refs.values()]
    )
    failure_class = (
        classify_omnigent_failure(
            failure_reason,
            require_full_evidence=require_full_evidence,
        )
        if failure_reason is not None
        else failure_class_for_terminal_status(terminal_status)
    )

    # A classified failure must never be summarized with the provider's
    # success snapshot text (for example a full-evidence harvest escalation on
    # a "completed" session whose snapshot summary still says "done"). Prefer an
    # explicit failure summary so operators are not told a failed run succeeded.
    if failure_class is not None and failure_summary:
        summary = failure_summary
    else:
        summary = final_snapshot.get("summary") or failure_summary
    if not summary:
        summary = (
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        )

    metadata = {
        "providerName": "omnigent",
        "normalizedStatus": terminal_status,
        "omnigentSessionId": session_id,
        "idempotencyKey": request.idempotency_key,
        "sseEventsCaptured": event_count,
        "correlationId": request.correlation_id,
    }
    if agent_id:
        metadata["omnigentAgentId"] = agent_id
    if capture_bundle.capture_manifest_ref:
        metadata["captureManifestRef"] = capture_bundle.capture_manifest_ref
    if capture_bundle.external_state_ref:
        metadata["externalStateRef"] = capture_bundle.external_state_ref
        metadata["stateCheckpointRef"] = capture_bundle.external_state_ref
        metadata["checkpointKind"] = "external_state_ref"
    metadata.update(capture_bundle.metadata_refs)
    snapshot_metadata_keys = {
        "omnigentAgentName": "omnigent_agent_name",
        "hostType": "host_type",
        "workspace": "workspace",
        "githubPrUrl": "github_pr_url",
    }
    for metadata_key, snake_key in snapshot_metadata_keys.items():
        value = final_snapshot.get(metadata_key) or final_snapshot.get(snake_key)
        if value:
            metadata[metadata_key] = str(value)

    return AgentRunResult(
        outputRefs=output_refs,
        summary=_compact_summary(
            summary,
            fallback="Omnigent session reached a terminal status",
        ),
        diagnosticsRef=str(diagnostics_ref),
        failureClass=failure_class,
        providerErrorCode=provider_error_code,
        metadata=metadata,
    )


def build_omnigent_terminal_refs(
    capture_bundle: OmnigentCaptureBundle,
    *,
    terminal_status: str,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Build bridge-store terminal refs from published MoonMind artifacts."""

    output_refs = list(capture_bundle.output_refs)
    diagnostics_ref = capture_bundle.diagnostics_ref
    metadata_refs = dict(capture_bundle.metadata_refs)
    _assert_no_provider_native_refs(
        [*output_refs, diagnostics_ref, *metadata_refs.values()]
    )
    summary = _compact_summary(
        final_snapshot.get("summary"),
        fallback=(
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        ),
    )
    return {
        "outputRefs": output_refs,
        "diagnosticsRef": diagnostics_ref,
        "metadataRefs": metadata_refs,
        "failureClass": failure_class_for_terminal_status(terminal_status),
        "failureCode": final_snapshot.get("providerErrorCode")
        or final_snapshot.get("failureCode"),
        "summary": summary,
    }


def _assert_no_provider_native_refs(refs: list[str]) -> None:
    bad = [ref for ref in refs if str(ref).startswith("omnigent://")]
    if bad:
        raise OmnigentContractError(
            "Omnigent terminal result cannot expose provider-native refs"
        )


class BridgeResourceHarvester:
    """Harvest Omnigent resources into MoonMind-owned artifact refs."""

    def __init__(
        self,
        *,
        client: Any,
        artifact_gateway: OmnigentArtifactGateway,
        request: AgentExecutionRequest,
        session_id: str,
        manifest: dict[str, Any],
        refs: dict[str, str],
    ) -> None:
        self._client = client
        self._artifact_gateway = artifact_gateway
        self._request = request
        self._session_id = session_id
        self._manifest = manifest
        self._refs = refs

    async def harvest_child_sessions(self, raw_events: list[dict[str, Any]]) -> None:
        child_session_ids = _child_session_ids(
            raw_events,
            parent_session_id=self._session_id,
        )
        self._manifest["childSessions"] = len(child_session_ids)
        if not child_session_ids:
            return
        child_ref = await self._artifact_gateway.write_text(
            request=self._request,
            name="runtime.omnigent.child_sessions.jsonl",
            payload=_jsonl(
                [
                    {"childSessionId": child_session_id}
                    for child_session_id in child_session_ids
                ]
            ),
            link_type="runtime.omnigent.child_sessions",
            content_type="application/x-ndjson",
        )
        self._refs["childSessionsRef"] = child_ref
        self._manifest["childSessionsRef"] = child_ref
        child_snapshots: list[dict[str, str]] = []
        if self._client is not None:
            for child_session_id in child_session_ids:
                try:
                    child_snapshot = await self._client.get_session(child_session_id)
                except Exception as exc:
                    child_snapshots.append(
                        {
                            "childSessionId": child_session_id,
                            "unavailable": _compact_summary(
                                exc,
                                fallback="child session snapshot unavailable",
                            ),
                        }
                    )
                    continue
                child_snapshot_ref = await capture_artifact_json(
                    self._artifact_gateway,
                    self._request,
                    self._refs,
                    key=f"childSessionSnapshotRef:{child_session_id}",
                    name=f"runtime.omnigent.child_sessions/{child_session_id}.json",
                    payload=child_snapshot,
                    link_type="runtime.omnigent.child_session.snapshot",
                )
                child_snapshots.append(
                    {
                        "childSessionId": child_session_id,
                        "snapshotRef": child_snapshot_ref,
                    }
                )
        self._manifest["childSessionEvidence"] = child_snapshots

    async def harvest_resources(
        self,
        *,
        capture_policy: dict[str, Any] | None,
    ) -> None:
        changed_items: list[dict[str, Any]] = []
        if _capture_enabled(capture_policy, "changedFiles"):
            changed_items = await self.harvest_changed_files()
        if _capture_enabled(capture_policy, "workspaceFiles"):
            await self.harvest_workspace_files()
        await self.harvest_workspace_diffs(changed_items=changed_items)
        if _capture_enabled(capture_policy, "sessionFiles"):
            await self.harvest_session_files()

    async def harvest_changed_files(self) -> list[dict[str, Any]]:
        try:
            changed = await self._client.list_changed_files(self._session_id)
        except Exception as exc:
            self._manifest["changedFilesUnavailable"] = _compact_summary(
                exc,
                fallback="changed files unavailable",
            )
            return []
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="changedFilesIndexRef",
            name="output.omnigent.changed_files.index.json",
            payload=changed,
            link_type="output.omnigent.changed_files.index",
        )
        self._manifest["changedFilesIndexRef"] = index_ref
        file_items = _resource_items(changed)[:_MAX_OMNIGENT_HARVEST_ITEMS]
        harvested: list[dict[str, Any]] = []
        for item in file_items:
            path = str(
                item.get("path")
                or item.get("file_path")
                or item.get("filePath")
                or item.get("name")
                or ""
            ).strip()
            if not path:
                continue
            try:
                content = await self._client.get_workspace_file(self._session_id, path)
            except Exception as exc:
                harvested.append(
                    {
                        "path": path,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="changed file content unavailable",
                        ),
                    }
                )
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.changed_files/{path}",
                payload=content,
                link_type="output.omnigent.changed_file",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["changedFiles"] = harvested
        self._manifest.setdefault("patchUnavailable", True)
        return file_items

    async def harvest_workspace_files(self) -> None:
        try:
            files = await self._client.list_workspace_files(self._session_id)
        except Exception as exc:
            self._manifest["workspaceFilesUnavailable"] = _compact_summary(
                exc,
                fallback="workspace files unavailable",
            )
            return
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="workspaceFilesIndexRef",
            name="output.omnigent.workspace_files.index.json",
            payload=files,
            link_type="output.omnigent.workspace_files.index",
        )
        self._manifest["workspaceFilesIndexRef"] = index_ref
        harvested: list[dict[str, Any]] = []
        for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
            path = _resource_path(item)
            if not path:
                continue
            if str(item.get("type") or item.get("kind") or "").strip().lower() in {
                "dir",
                "directory",
                "folder",
            }:
                harvested.append({"path": path, "skipped": "directory"})
                continue
            try:
                content = await self._client.get_workspace_file(self._session_id, path)
            except Exception as exc:
                harvested.append(
                    {
                        "path": path,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="workspace file content unavailable",
                        ),
                    }
                )
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.workspace_files/{path}",
                payload=content,
                link_type="output.omnigent.workspace_file",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["workspaceFiles"] = harvested

    async def harvest_workspace_diffs(
        self,
        *,
        changed_items: list[dict[str, Any]],
    ) -> None:
        paths = [
            path
            for path in (_resource_path(item) for item in changed_items)
            if path
        ][:_MAX_OMNIGENT_HARVEST_ITEMS]
        if not paths:
            self._manifest["workspaceDiffs"] = []
            self._manifest["patchUnavailable"] = True
            return
        harvested: list[dict[str, Any]] = []
        for path in paths:
            try:
                diff = await self._client.get_workspace_diff(self._session_id, path)
            except Exception as exc:
                self._manifest["workspaceDiffsUnavailable"] = _compact_summary(
                    exc,
                    fallback="workspace diff capability unavailable",
                )
                self._manifest["patchUnavailable"] = True
                return
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.workspace_diffs/{path}.diff",
                payload=diff,
                link_type="output.omnigent.workspace_diff",
                content_type="text/x-diff",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["workspaceDiffs"] = harvested
        self._manifest["patchUnavailable"] = not bool(harvested)

    async def harvest_session_files(self) -> None:
        try:
            files = await self._client.list_session_files(self._session_id)
        except Exception as exc:
            self._manifest["sessionFilesUnavailable"] = _compact_summary(
                exc,
                fallback="session files unavailable",
            )
            return
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="sessionFilesIndexRef",
            name="output.omnigent.session_files.index.json",
            payload=files,
            link_type="output.omnigent.session_files.index",
        )
        self._manifest["sessionFilesIndexRef"] = index_ref
        harvested: list[dict[str, Any]] = []
        for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
            file_id = str(
                item.get("id") or item.get("file_id") or item.get("fileId") or ""
            ).strip()
            filename = str(item.get("filename") or item.get("name") or file_id).strip()
            if not file_id:
                continue
            try:
                content = await self._client.get_session_file_content(
                    self._session_id,
                    file_id,
                )
            except Exception as exc:
                harvested.append(
                    {
                        "fileId": file_id,
                        "filename": filename,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="session file content unavailable",
                        ),
                    }
                )
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.session_files/{file_id}/{filename}",
                payload=content,
                link_type="output.omnigent.session_file",
            )
            metadata_ref = await capture_artifact_json(
                self._artifact_gateway,
                self._request,
                self._refs,
                key=f"sessionFileMetadataRef:{file_id}",
                name=f"output.omnigent.session_files/{file_id}/metadata.json",
                payload=item,
                link_type="output.omnigent.session_file.metadata",
            )
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "artifactRef": ref,
                    "metadataRef": metadata_ref,
                }
            )
        self._manifest["sessionFiles"] = harvested


def _capture_enabled(capture_policy: dict[str, Any] | None, key: str) -> bool:
    if capture_policy is None:
        return True
    return bool(capture_policy.get(key, True))


async def _harvest_changed_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> list[dict[str, Any]]:
    try:
        changed = await client.list_changed_files(session_id)
    except Exception as exc:
        manifest["changedFilesUnavailable"] = _compact_summary(
            exc,
            fallback="changed files unavailable",
        )
        return []
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="changedFilesIndexRef",
        name="output.omnigent.changed_files.index.json",
        payload=changed,
        link_type="output.omnigent.changed_files.index",
    )
    manifest["changedFilesIndexRef"] = index_ref
    file_items = _resource_items(changed)[:_MAX_OMNIGENT_HARVEST_ITEMS]
    harvested: list[dict[str, Any]] = []
    for item in file_items:
        path = str(
            item.get("path")
            or item.get("file_path")
            or item.get("filePath")
            or item.get("name")
            or ""
        ).strip()
        if not path:
            continue
        try:
            content = await client.get_workspace_file(session_id, path)
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="changed file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.changed_files/{path}",
            payload=content,
            link_type="output.omnigent.changed_file",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["changedFiles"] = harvested
    manifest.setdefault("patchUnavailable", True)
    return file_items


async def _harvest_workspace_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    try:
        files = await client.list_workspace_files(session_id)
    except Exception as exc:
        manifest["workspaceFilesUnavailable"] = _compact_summary(
            exc,
            fallback="workspace files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="workspaceFilesIndexRef",
        name="output.omnigent.workspace_files.index.json",
        payload=files,
        link_type="output.omnigent.workspace_files.index",
    )
    manifest["workspaceFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        path = _resource_path(item)
        if not path:
            continue
        if str(item.get("type") or item.get("kind") or "").strip().lower() in {
            "dir",
            "directory",
            "folder",
        }:
            harvested.append({"path": path, "skipped": "directory"})
            continue
        try:
            content = await client.get_workspace_file(session_id, path)
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="workspace file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_files/{path}",
            payload=content,
            link_type="output.omnigent.workspace_file",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["workspaceFiles"] = harvested


async def _harvest_workspace_diffs(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    changed_items: list[dict[str, Any]],
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    paths = [
        path
        for path in (_resource_path(item) for item in changed_items)
        if path
    ][:_MAX_OMNIGENT_HARVEST_ITEMS]
    if not paths:
        manifest["workspaceDiffs"] = []
        manifest["patchUnavailable"] = True
        return
    harvested: list[dict[str, Any]] = []
    for path in paths:
        try:
            diff = await client.get_workspace_diff(session_id, path)
        except Exception as exc:
            manifest["workspaceDiffsUnavailable"] = _compact_summary(
                exc,
                fallback="workspace diff capability unavailable",
            )
            manifest["patchUnavailable"] = True
            return
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_diffs/{path}.diff",
            payload=diff,
            link_type="output.omnigent.workspace_diff",
            content_type="text/x-diff",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["workspaceDiffs"] = harvested
    manifest["patchUnavailable"] = not bool(harvested)


async def _harvest_session_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    try:
        files = await client.list_session_files(session_id)
    except Exception as exc:
        manifest["sessionFilesUnavailable"] = _compact_summary(
            exc,
            fallback="session files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="sessionFilesIndexRef",
        name="output.omnigent.session_files.index.json",
        payload=files,
        link_type="output.omnigent.session_files.index",
    )
    manifest["sessionFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        file_id = str(item.get("id") or item.get("file_id") or item.get("fileId") or "").strip()
        filename = str(item.get("filename") or item.get("name") or file_id).strip()
        if not file_id:
            continue
        try:
            content = await client.get_session_file_content(session_id, file_id)
        except Exception as exc:
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="session file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.session_files/{file_id}/{filename}",
            payload=content,
            link_type="output.omnigent.session_file",
        )
        metadata_ref = await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key=f"sessionFileMetadataRef:{file_id}",
            name=f"output.omnigent.session_files/{file_id}/metadata.json",
            payload=item,
            link_type="output.omnigent.session_file.metadata",
        )
        harvested.append(
            {
                "fileId": file_id,
                "filename": filename,
                "artifactRef": ref,
                "metadataRef": metadata_ref,
            }
        )
    manifest["sessionFiles"] = harvested


def _resource_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "files", "changes", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _resource_items(value)
            if nested:
                return nested
    return []


def _resource_path(item: dict[str, Any]) -> str:
    return str(
        item.get("path")
        or item.get("file_path")
        or item.get("filePath")
        or item.get("relativePath")
        or item.get("name")
        or ""
    ).strip().strip("/")


def _child_session_ids(
    events: list[dict[str, Any]],
    *,
    parent_session_id: str,
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        event_type = str(event.get("type") or event.get("eventType") or "").lower()
        if "child" not in event_type:
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized_key = key.replace("_", "").lower()
                    if normalized_key in {"sessionid", "childsessionid"}:
                        candidate = str(nested or "").strip()
                        if (
                            candidate
                            and candidate != parent_session_id
                            and candidate not in seen
                        ):
                            ids.append(candidate)
                            seen.add(candidate)
                    else:
                        stack.append(nested)
            elif isinstance(value, list):
                stack.extend(value)
    return ids


def _redacted_endpoint_url(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return "redacted"
    if not parsed.scheme or not parsed.hostname:
        return "redacted"
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _omnigent_endpoint_ref(request: AgentExecutionRequest) -> str:
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = parameters.get("omnigent")
    if isinstance(omnigent, dict):
        endpoint_ref = str(omnigent.get("endpointRef") or "").strip()
        if endpoint_ref:
            return endpoint_ref
    return "default"


def _payload_digest(payload: Any) -> str | None:
    if payload is None:
        return None
    encoded = json.dumps(
        payload,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_ref_items(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    refs: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artifact_ref = str(item.get("artifactRef") or "").strip()
        if not artifact_ref:
            continue
        path = str(item.get("path") or item.get("filename") or "").strip()
        compact = {"artifactRef": artifact_ref}
        if path:
            compact["path"] = path
        refs.append(compact)
    return refs


def _patch_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    diff_refs = _artifact_ref_items(manifest.get("workspaceDiffs"))
    evidence: dict[str, Any] = {
        "diffRefs": diff_refs,
        "patchUnavailable": bool(manifest.get("patchUnavailable", not diff_refs)),
    }
    diagnostics: list[dict[str, str]] = []
    if evidence["patchUnavailable"]:
        diagnostics.append(
            {
                "code": "omnigent_patch_unavailable",
                "message": (
                    "Omnigent patch evidence is unavailable; "
                    "see captured diff refs or diagnostics."
                ),
            }
        )
    unavailable = str(manifest.get("workspaceDiffsUnavailable") or "").strip()
    if unavailable:
        diagnostics.append(
            {
                "code": "omnigent_workspace_diffs_unavailable",
                "message": unavailable,
            }
        )
    if diagnostics:
        evidence["diagnostics"] = diagnostics
    return evidence


def _reconcile_changed_file_evidence(manifest: dict[str, Any]) -> None:
    """Durably associate each changed file with its harvested diff outcome."""

    diffs_by_path = {
        str(item.get("path") or ""): str(item.get("artifactRef") or "")
        for item in manifest.get("workspaceDiffs", [])
        if isinstance(item, dict) and item.get("path") and item.get("artifactRef")
    }
    unavailable = str(
        manifest.get("workspaceDiffsUnavailable")
        or "No diff artifact was published for this changed file."
    )
    for changed_file in manifest.get("changedFiles", []):
        if not isinstance(changed_file, dict) or not changed_file.get("path"):
            continue
        diff_ref = diffs_by_path.get(str(changed_file["path"]))
        if diff_ref:
            changed_file["diffArtifactRef"] = diff_ref
            changed_file.pop("diffUnavailable", None)
        else:
            changed_file["diffUnavailable"] = unavailable


def _associate_resource_events(
    manifest: dict[str, Any], normalized_events: list[dict[str, Any]]
) -> None:
    """Attach harvested changed files to the durable announcing event sequence."""

    sequence_by_path: dict[str, int] = {}
    for event in normalized_events:
        event_type = str(event.get("type") or event.get("eventType") or "")
        if event_type != "resource.changed_file":
            continue
        path = _resource_path(event)
        sequence = event.get("sequence")
        if path and isinstance(sequence, int):
            sequence_by_path.setdefault(path, sequence)
    for changed_file in manifest.get("changedFiles", []):
        if not isinstance(changed_file, dict):
            continue
        sequence = sequence_by_path.get(str(changed_file.get("path") or ""))
        if sequence is not None:
            changed_file["sourceEventSequence"] = sequence


def _capture_resource_groups(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the manifest into stable UI-oriented evidence groups."""

    definitions = (
        ("changed_files", "Changed files", "changedFiles"),
        ("diffs", "Diffs", "workspaceDiffs"),
        ("workspace_files", "Workspace files", "workspaceFiles"),
        ("session_files", "Session files", "sessionFiles"),
        ("snapshots", "Snapshots", "snapshotEvidence"),
        ("logs_and_journals", "Logs and event journals", "journalEvidence"),
        ("diagnostics", "Diagnostics", "diagnosticEvidence"),
        ("manifests", "Capture and checkpoint manifests", "manifestEvidence"),
    )
    groups: list[dict[str, Any]] = []
    for key, title, manifest_key in definitions:
        raw_items = manifest.get(manifest_key, [])
        items = raw_items if isinstance(raw_items, list) else []
        groups.append({"groupKey": key, "title": title, "items": items})
    return groups


async def _build_capture_bundle(
    *,
    client: OmnigentHttpClient | None,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    initial_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
    first_message_request: dict[str, Any] | None,
    first_message_response: dict[str, Any] | None,
    first_message_posted: bool,
    first_message_response_identifiers: dict[str, str] | None,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    terminal_status: str,
    diagnostics: dict[str, Any],
    harvest_resources: bool,
    external_state: dict[str, Any] | None = None,
    capture_policy: dict[str, Any] | None = None,
) -> OmnigentCaptureBundle:
    refs: dict[str, str] = {}
    if first_message_request is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageRequestRef",
            name="input.omnigent.first_message.request.json",
            payload=first_message_request,
            link_type="input.omnigent.first_message.request",
        )
    if first_message_response is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageResponseRef",
            name="input.omnigent.first_message.response.json",
            payload=first_message_response,
            link_type="input.omnigent.first_message.response",
        )
    if initial_snapshot is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="initialSnapshotRef",
            name="runtime.omnigent.snapshot.initial.json",
            payload=initial_snapshot,
            link_type="runtime.omnigent.snapshot.initial",
        )
    # §16 rule 5: redact secret-like fields on the raw-event persistence path
    # so the artifact system stays a safe evidence boundary.
    raw_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.raw.jsonl",
        payload=_jsonl(redact_raw_events(raw_events)),
        link_type="runtime.omnigent.sse.raw",
        content_type="application/x-ndjson",
    )
    refs["rawSseStreamRef"] = raw_ref
    normalized_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.normalized.jsonl",
        payload=_jsonl(normalized_events),
        link_type="runtime.omnigent.sse.normalized",
        content_type="application/x-ndjson",
    )
    refs["normalizedEventStreamRef"] = normalized_ref
    final_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="finalSnapshotRef",
        name="output.omnigent.snapshot.final.json",
        payload=final_snapshot,
        link_type="output.omnigent.snapshot.final",
    )
    manifest: dict[str, Any] = {
        "schemaVersion": _CAPTURE_MANIFEST_SCHEMA_VERSION,
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "omnigentAgentId": agent_id,
        "terminalStatus": terminal_status,
        "artifactRefs": refs,
        "patchUnavailable": True,
        "capturePolicy": {
            "requested": dict(capture_policy or {}),
            "limits": {
                "maxListEntries": _MAX_OMNIGENT_HARVEST_ITEMS,
                "maxHarvestedFiles": _MAX_OMNIGENT_HARVEST_ITEMS,
            },
            "optionalEvidenceFailureIsFatal": _capture_requires_full_evidence(
                capture_policy
            ),
        },
    }
    child_session_ids = _child_session_ids(raw_events, parent_session_id=session_id)
    manifest["childSessions"] = len(child_session_ids)
    if child_session_ids:
        child_ref = await artifact_gateway.write_text(
            request=request,
            name="runtime.omnigent.child_sessions.jsonl",
            payload=_jsonl(
                [
                    {"childSessionId": child_session_id}
                    for child_session_id in child_session_ids
                ]
            ),
            link_type="runtime.omnigent.child_sessions",
            content_type="application/x-ndjson",
        )
        refs["childSessionsRef"] = child_ref
        manifest["childSessionsRef"] = child_ref
        child_snapshots: list[dict[str, str]] = []
        if client is not None:
            for child_session_id in child_session_ids:
                try:
                    child_snapshot = await client.get_session(child_session_id)
                except Exception as exc:
                    child_snapshots.append(
                        {
                            "childSessionId": child_session_id,
                            "unavailable": _compact_summary(
                                exc,
                                fallback="child session snapshot unavailable",
                            ),
                        }
                    )
                    continue
                child_snapshot_ref = await _capture_artifact_json(
                    artifact_gateway,
                    request,
                    refs,
                    key=f"childSessionSnapshotRef:{child_session_id}",
                    name=f"runtime.omnigent.child_sessions/{child_session_id}.json",
                    payload=child_snapshot,
                    link_type="runtime.omnigent.child_session.snapshot",
                )
                child_snapshots.append(
                    {
                        "childSessionId": child_session_id,
                        "snapshotRef": child_snapshot_ref,
                    }
                )
        manifest["childSessionEvidence"] = child_snapshots
    if harvest_resources and client is not None and session_id:
        changed_items: list[dict[str, Any]] = []
        if _capture_enabled(capture_policy, "changedFiles"):
            changed_items = await _harvest_changed_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
        if _capture_enabled(capture_policy, "workspaceFiles"):
            await _harvest_workspace_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
        await _harvest_workspace_diffs(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            changed_items=changed_items,
            manifest=manifest,
            refs=refs,
        )
        if _capture_enabled(capture_policy, "sessionFiles"):
            await _harvest_session_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
    _associate_resource_events(manifest, normalized_events)
    _reconcile_changed_file_evidence(manifest)
    optional_harvest_failed = _optional_resource_harvest_failed(manifest)
    require_full_evidence = _capture_requires_full_evidence(capture_policy)
    resource_harvest_failure_class: str | None = None
    if optional_harvest_failed:
        resource_harvest_failure_class = classify_omnigent_failure(
            OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED,
            require_full_evidence=require_full_evidence,
        )
        manifest["optionalResourceHarvest"] = {
            "failed": True,
            "requireFullEvidence": require_full_evidence,
            "outcome": (
                "required_evidence_missing"
                if resource_harvest_failure_class
                else "completed_with_diagnostics"
            ),
            "failureClass": resource_harvest_failure_class,
        }
    unavailable_reasons = {
        key: value
        for key, value in manifest.items()
        if key.endswith("Unavailable") and value
    }
    manifest["evidenceCompleteness"] = {
        "status": (
            "required_missing"
            if resource_harvest_failure_class
            else "degraded"
            if optional_harvest_failed
            else "complete"
        ),
        "unavailableReasons": unavailable_reasons,
    }
    diagnostics_payload = {
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "terminalStatus": terminal_status,
        "diagnostics": diagnostics,
        "captureManifest": manifest,
    }
    diagnostics_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="diagnosticsRef",
        name="diagnostics.omnigent.json",
        payload=diagnostics_payload,
        link_type="diagnostics.omnigent",
    )
    manifest["snapshotEvidence"] = [
        {"label": label, "artifactRef": refs[ref_key]}
        for label, ref_key in (
            ("Initial session snapshot", "initialSnapshotRef"),
            ("Final session snapshot", "finalSnapshotRef"),
        )
        if ref_key in refs
    ] + list(manifest.get("childSessionEvidence", []))
    manifest["journalEvidence"] = [
        {"label": label, "artifactRef": refs[ref_key]}
        for label, ref_key in (
            ("Raw event journal", "rawSseStreamRef"),
            ("Normalized event journal", "normalizedEventStreamRef"),
            ("Child-session journal", "childSessionsRef"),
        )
        if ref_key in refs
    ]
    manifest["diagnosticEvidence"] = [
        {"label": "Capture diagnostics", "artifactRef": diagnostics_ref}
    ]
    if external_state is not None:
        first_message_state = dict(external_state.get("firstMessage", {}))
        first_message_state.setdefault("requestRef", refs.get("firstMessageRequestRef"))
        first_message_state.setdefault(
            "responseRef", refs.get("firstMessageResponseRef")
        )
        first_message_state["posted"] = (
            first_message_posted or first_message_response is not None
        )
        if first_message_response_identifiers:
            first_message_state["responseIdentifiers"] = dict(
                first_message_response_identifiers
            )
        external_state_payload = {
            "sourceIssue": "MM-1077",
            "provider": "omnigent",
            "checkpointKind": "external_state_ref",
            "endpointRef": external_state.get("endpointRef"),
            "endpoint": {
                "endpointRef": _omnigent_endpoint_ref(request),
                "serverUrl": _redacted_endpoint_url(resolved_server_url()),
            },
            "correlation": {
                "correlationId": request.correlation_id,
                "idempotencyKey": request.idempotency_key,
                "omnigentSessionId": session_id,
                "omnigentAgentId": agent_id,
            },
            "omnigentSessionId": session_id,
            "providerProfileId": external_state.get("providerProfileId"),
            "credentialGeneration": external_state.get("credentialGeneration"),
            "providerLeaseRef": external_state.get("providerLeaseRef"),
            "hostBindingRef": external_state.get("hostBindingRef"),
            "hostLeaseRef": external_state.get("hostLeaseRef"),
            "omnigentHostId": external_state.get("omnigentHostId"),
            "bridgeSessionId": external_state.get("bridgeSessionId"),
            "omnigentAgentId": agent_id,
            "terminalStatus": terminal_status,
            "firstMessage": first_message_state,
            "retry": external_state.get("retry", {}),
            "reattachState": {
                "idempotencyKey": request.idempotency_key,
                "initialSnapshotRef": refs.get("initialSnapshotRef"),
                "initialSnapshotObserved": initial_snapshot is not None,
            },
            "streamRefs": {
                "rawSseStreamRef": refs.get("rawSseStreamRef"),
                "normalizedEventStreamRef": refs.get("normalizedEventStreamRef"),
            },
            "snapshotRefs": {
                "initialSnapshotRef": refs.get("initialSnapshotRef"),
                "finalSnapshotRef": refs.get("finalSnapshotRef"),
            },
            "terminalResultRefs": {
                "outputRefs": [
                    ref
                    for ref in (
                        refs.get("finalSnapshotRef"),
                        refs.get("normalizedEventStreamRef"),
                    )
                    if ref
                ],
                "finalSnapshotRef": refs.get("finalSnapshotRef"),
                "diagnosticsRef": diagnostics_ref,
                "terminalStatus": terminal_status,
            },
            "patchEvidence": _patch_evidence(manifest),
            "artifactRefs": {
                key: refs[key]
                for key in (
                    "initialSnapshotRef",
                    "finalSnapshotRef",
                    "rawSseStreamRef",
                    "normalizedEventStreamRef",
                    "diagnosticsRef",
                )
                if key in refs
            },
        }
        external_state_payload = {
            key: value
            for key, value in external_state_payload.items()
            if value is not None
        }
        external_state_ref = await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="externalStateRef",
            name="checkpoint.omnigent.external_state.json",
            payload=external_state_payload,
            link_type="checkpoint.omnigent.external_state_ref",
        )
        manifest["externalStateRef"] = external_state_ref
    manifest["manifestEvidence"] = [
        {
            "label": "External-state checkpoint",
            "artifactRef": refs["externalStateRef"],
        }
        for _ in (0,)
        if "externalStateRef" in refs
    ]
    manifest["resourceGroups"] = _capture_resource_groups(manifest)
    manifest_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="captureManifestRef",
        name="output.omnigent.capture_manifest.json",
        payload=manifest,
        link_type="output.omnigent.capture_manifest",
    )
    metadata_refs = {
        "captureManifestRef": manifest_ref,
        "rawSseStreamRef": raw_ref,
        "normalizedEventStreamRef": normalized_ref,
        "finalSnapshotRef": final_ref,
    }
    if "externalStateRef" in refs:
        metadata_refs["externalStateRef"] = refs["externalStateRef"]
        metadata_refs["checkpointKind"] = "external_state_ref"
    for optional_key in (
        "firstMessageRequestRef",
        "firstMessageResponseRef",
        "initialSnapshotRef",
        "changedFilesIndexRef",
        "workspaceFilesIndexRef",
        "sessionFilesIndexRef",
        "childSessionsRef",
        "externalStateRef",
    ):
        if optional_key in refs:
            metadata_refs[optional_key] = refs[optional_key]
    output_refs = [final_ref, normalized_ref, manifest_ref]
    return OmnigentCaptureBundle(
        output_refs=output_refs,
        diagnostics_ref=diagnostics_ref,
        capture_manifest_ref=manifest_ref,
        external_state_ref=refs.get("externalStateRef", ""),
        metadata_refs=metadata_refs,
        optional_harvest_failed=optional_harvest_failed,
        resource_harvest_failure_class=resource_harvest_failure_class,
    )


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(event, sort_keys=True, default=str, separators=(",", ":")) + "\n"
        for event in events
    )


__all__ = [
    "BridgeResourceHarvester",
    "LocalOmnigentArtifactGateway",
    "OmnigentArtifactError",
    "OmnigentArtifactGateway",
    "OmnigentCaptureBundle",
    "OmnigentContractError",
    "build_omnigent_terminal_refs",
    "build_omnigent_result",
    "capture_artifact_json",
    "_build_capture_bundle",
    "_compact_summary",
]
