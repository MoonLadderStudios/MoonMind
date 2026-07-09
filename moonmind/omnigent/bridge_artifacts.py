"""MoonMind artifact publishing and resource harvesting for Omnigent bridge runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from re import sub
from typing import Any

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

_MAX_OMNIGENT_HARVEST_ITEMS = 100


class OmnigentArtifactError(RuntimeError):
    """Raised when Omnigent artifact evidence cannot be read or written."""


class OmnigentArtifactGateway:
    """Minimal artifact boundary needed by Omnigent bridge components."""

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
    ref = await gateway.write_json(
        request=request,
        name=name,
        payload=payload,
        link_type=link_type,
    )
    refs[key] = ref
    return ref


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
    if not isinstance(capture_policy, dict):
        return True
    value = capture_policy.get(key)
    return value is not False


def _compact_summary(value: object | None, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:4096]


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


def _jsonl(items: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(item, sort_keys=True, default=str) + "\n" for item in items)


def _safe_artifact_segment(value: object) -> str:
    text = sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    if text in {".", ".."}:
        return "segment"
    return text[:120] or "run"


def _safe_artifact_name(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    parts = [_safe_artifact_segment(part) for part in text.split("/") if part.strip()]
    return "/".join(parts) or "artifact"


__all__ = [
    "BridgeResourceHarvester",
    "LocalOmnigentArtifactGateway",
    "OmnigentArtifactError",
    "OmnigentArtifactGateway",
    "capture_artifact_json",
]
