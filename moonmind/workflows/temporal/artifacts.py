"""Temporal artifact service and storage helpers for local/dev runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import Select, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREVIEW_MAX_BYTES = 16 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024


class TemporalArtifactError(Exception):
    """Base class for Temporal artifact service errors."""


class TemporalArtifactNotFoundError(TemporalArtifactError):
    """Raised when a requested artifact does not exist."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"Artifact {artifact_id} was not found")
        self.artifact_id = artifact_id


class TemporalArtifactStateError(TemporalArtifactError):
    """Raised when an operation is invalid for current artifact state."""


class TemporalArtifactValidationError(TemporalArtifactError):
    """Raised when an artifact request is malformed or exceeds policy."""


class TemporalArtifactAuthorizationError(TemporalArtifactError):
    """Raised when principal is not permitted to access an artifact."""


@dataclass(slots=True, frozen=True)
class ExecutionRef:
    """Execution linkage used by Temporal artifacts."""

    namespace: str
    workflow_id: str
    run_id: str
    link_type: str
    label: str | None = None
    created_by_activity_type: str | None = None
    created_by_worker: str | None = None


@dataclass(slots=True, frozen=True)
class ArtifactRef:
    """Canonical ArtifactRef payload passed to workflows/activities."""

    artifact_ref_v: int
    artifact_id: str
    sha256: str | None
    size_bytes: int | None
    content_type: str | None
    encryption: str


@dataclass(slots=True, frozen=True)
class ArtifactUploadDescriptor:
    """Upload descriptor returned by create endpoint."""

    mode: str
    upload_url: str | None
    upload_id: str | None
    expires_at: datetime
    max_size_bytes: int
    required_headers: dict[str, str]


@dataclass(slots=True, frozen=True)
class ArtifactUploadPartDescriptor:
    """Presigned one-part multipart upload descriptor."""

    part_number: int
    url: str
    expires_at: datetime
    required_headers: dict[str, str]


@dataclass(slots=True, frozen=True)
class ArtifactReadPolicy:
    """Resolved read policy metadata for UI-safe reads."""

    raw_access_allowed: bool
    preview_artifact_ref: ArtifactRef | None
    default_read_ref: ArtifactRef


@dataclass(slots=True, frozen=True)
class LifecycleSweepSummary:
    """Lifecycle sweep results."""

    run_id: str
    expired_candidate_count: int
    soft_deleted_count: int
    hard_deleted_count: int


@dataclass(slots=True, frozen=True)
class _StorageLifecycleConfig:
    hard_delete_after: timedelta


def _encode_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_BASE32[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_artifact_id(now: datetime | None = None) -> str:
    """Return an opaque ``art_<ULID>`` identifier."""

    timestamp = int((now or datetime.now(UTC)).timestamp() * 1000)
    timestamp_bits = timestamp & ((1 << 48) - 1)
    random_bits = secrets.randbits(80)
    ulid_value = (timestamp_bits << 80) | random_bits
    ulid = _encode_base32(ulid_value, 26)
    return f"art_{ulid}"


def _validate_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise TemporalArtifactValidationError(
            "sha256 must be a 64-character hex string"
        )
    return normalized


def _derive_retention(
    explicit: db_models.TemporalArtifactRetentionClass | None,
    link_type: str | None,
) -> db_models.TemporalArtifactRetentionClass:
    if explicit is not None:
        return explicit
    link = (link_type or "").strip().lower()
    if link in {"output.logs", "debug.trace"}:
        return db_models.TemporalArtifactRetentionClass.EPHEMERAL
    if link in {"input.instructions", "input.plan", "input.manifest"}:
        return db_models.TemporalArtifactRetentionClass.STANDARD
    if link in {"output.primary", "output.patch", "output.summary"}:
        return db_models.TemporalArtifactRetentionClass.STANDARD
    return db_models.TemporalArtifactRetentionClass.STANDARD


def _expires_at_for_retention(
    retention_class: db_models.TemporalArtifactRetentionClass,
    now: datetime,
) -> datetime | None:
    if retention_class is db_models.TemporalArtifactRetentionClass.PINNED:
        return None
    if retention_class is db_models.TemporalArtifactRetentionClass.EPHEMERAL:
        return now + timedelta(days=7)
    if retention_class is db_models.TemporalArtifactRetentionClass.LONG:
        return now + timedelta(days=180)
    return now + timedelta(days=30)


def build_artifact_ref(artifact: db_models.TemporalArtifact) -> ArtifactRef:
    """Map DB metadata to ArtifactRef contract."""

    return ArtifactRef(
        artifact_ref_v=1,
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        content_type=artifact.content_type,
        encryption=artifact.encryption.value,
    )


class TemporalArtifactStore:
    """Storage adapter contract for artifact bytes."""

    @property
    def backend(self) -> db_models.TemporalArtifactStorageBackend:
        raise NotImplementedError

    @property
    def supports_multipart(self) -> bool:
        return False

    def build_storage_key(
        self, *, namespace: str, artifact_id: str, now: datetime
    ) -> str:
        raise NotImplementedError

    def write_bytes(
        self, storage_key: str, payload: bytes, *, content_type: str | None
    ) -> None:
        raise NotImplementedError

    def read_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError

    def read_chunks(
        self, storage_key: str, *, chunk_size: int = _STREAM_CHUNK_BYTES
    ) -> Iterable[bytes]:
        raise NotImplementedError

    def read_path(self, storage_key: str) -> Path:
        raise NotImplementedError

    def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    def presign_single_upload(
        self,
        *,
        storage_key: str,
        content_type: str | None,
        expires_in_seconds: int,
    ) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def create_multipart_upload(
        self,
        *,
        storage_key: str,
        content_type: str | None,
    ) -> str:
        raise TemporalArtifactValidationError(
            "multipart upload is not supported by storage backend"
        )

    def presign_upload_part(
        self,
        *,
        storage_key: str,
        upload_id: str,
        part_number: int,
        expires_in_seconds: int,
    ) -> tuple[str, dict[str, str]]:
        raise TemporalArtifactValidationError(
            "multipart upload is not supported by storage backend"
        )

    def complete_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> None:
        raise TemporalArtifactValidationError(
            "multipart upload is not supported by storage backend"
        )

    def abort_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
    ) -> None:
        raise TemporalArtifactValidationError(
            "multipart upload is not supported by storage backend"
        )

    def presign_download(
        self,
        *,
        storage_key: str,
        expires_in_seconds: int,
        download_filename: str | None = None,
    ) -> str:
        raise NotImplementedError


class LocalTemporalArtifactStore(TemporalArtifactStore):
    """Filesystem-backed blob store for local development fallback mode."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def backend(self) -> db_models.TemporalArtifactStorageBackend:
        return db_models.TemporalArtifactStorageBackend.LOCAL_FS

    def build_storage_key(
        self, *, namespace: str, artifact_id: str, now: datetime
    ) -> str:
        safe_namespace = namespace.strip().replace("\\", "/").strip("/") or "moonmind"
        if ".." in safe_namespace.split("/"):
            raise TemporalArtifactValidationError(
                "namespace must not contain traversal"
            )
        return f"{safe_namespace}/artifacts/{now:%Y/%m/%d}/{artifact_id}"

    def resolve_storage_key(self, storage_key: str) -> Path:
        relative = Path(storage_key)
        if not relative.parts:
            raise TemporalArtifactValidationError("storage key must not be empty")
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise TemporalArtifactValidationError(
                "storage key must be relative and traversal-safe"
            )
        destination = (self.root / relative).resolve()
        resolved_root = self.root.resolve()
        if not destination.is_relative_to(resolved_root):
            raise TemporalArtifactValidationError(
                "storage key resolves outside artifact root"
            )
        return destination

    def write_bytes(
        self, storage_key: str, payload: bytes, *, content_type: str | None
    ) -> None:
        _ = content_type
        destination = self.resolve_storage_key(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    def read_bytes(self, storage_key: str) -> bytes:
        return self.resolve_storage_key(storage_key).read_bytes()

    def read_chunks(
        self, storage_key: str, *, chunk_size: int = _STREAM_CHUNK_BYTES
    ) -> Iterable[bytes]:
        path = self.resolve_storage_key(storage_key)
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def read_path(self, storage_key: str) -> Path:
        return self.resolve_storage_key(storage_key)

    def delete(self, storage_key: str) -> None:
        self.resolve_storage_key(storage_key).unlink(missing_ok=True)

    def presign_single_upload(
        self,
        *,
        storage_key: str,
        content_type: str | None,
        expires_in_seconds: int,
    ) -> tuple[str, dict[str, str]]:
        _ = storage_key, content_type, expires_in_seconds
        return "", {}

    def presign_download(
        self,
        *,
        storage_key: str,
        expires_in_seconds: int,
        download_filename: str | None = None,
    ) -> str:
        _ = storage_key, expires_in_seconds, download_filename
        return ""


class S3TemporalArtifactStore(TemporalArtifactStore):
    """S3-compatible store adapter used by MinIO-first local/dev runtime."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str,
        use_ssl: bool,
    ) -> None:
        if not endpoint_url.strip() or not bucket.strip():
            raise TemporalArtifactValidationError(
                "S3 backend requires endpoint URL and bucket"
            )
        if not access_key_id.strip() or not secret_access_key.strip():
            raise TemporalArtifactValidationError(
                "S3 backend requires access key and secret"
            )

        self._bucket = bucket.strip()
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url.strip(),
            aws_access_key_id=access_key_id.strip(),
            aws_secret_access_key=secret_access_key.strip(),
            region_name=region_name.strip() or "us-east-1",
            use_ssl=bool(use_ssl),
        )
        self._ensure_bucket_exists()

    @property
    def backend(self) -> db_models.TemporalArtifactStorageBackend:
        return db_models.TemporalArtifactStorageBackend.S3

    @property
    def supports_multipart(self) -> bool:
        return True

    def _ensure_bucket_exists(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        self._client.create_bucket(Bucket=self._bucket)

    def build_storage_key(
        self, *, namespace: str, artifact_id: str, now: datetime
    ) -> str:
        safe_namespace = namespace.strip().replace("\\", "/").strip("/") or "moonmind"
        if ".." in safe_namespace.split("/"):
            raise TemporalArtifactValidationError(
                "namespace must not contain traversal"
            )
        return f"{safe_namespace}/artifacts/{now:%Y/%m/%d}/{artifact_id}"

    def write_bytes(
        self, storage_key: str, payload: bytes, *, content_type: str | None
    ) -> None:
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": storage_key,
            "Body": payload,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        self._client.put_object(**kwargs)

    def read_bytes(self, storage_key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
        return response["Body"].read()

    def read_chunks(
        self, storage_key: str, *, chunk_size: int = _STREAM_CHUNK_BYTES
    ) -> Iterable[bytes]:
        response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
        stream = response["Body"]
        try:
            for chunk in stream.iter_chunks(chunk_size):
                if chunk:
                    yield chunk
        finally:
            stream.close()

    def read_path(self, storage_key: str) -> Path:
        raise TemporalArtifactValidationError(
            "read_path is unavailable for S3-backed artifacts"
        )

    def delete(self, storage_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=storage_key)

    def presign_single_upload(
        self,
        *,
        storage_key: str,
        content_type: str | None,
        expires_in_seconds: int,
    ) -> tuple[str, dict[str, str]]:
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": storage_key,
        }
        required_headers: dict[str, str] = {}
        if content_type:
            params["ContentType"] = content_type
            required_headers["content-type"] = content_type
        url = self._client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires_in_seconds,
            HttpMethod="PUT",
        )
        return url, required_headers

    def create_multipart_upload(
        self,
        *,
        storage_key: str,
        content_type: str | None,
    ) -> str:
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": storage_key,
        }
        if content_type:
            params["ContentType"] = content_type
        response = self._client.create_multipart_upload(**params)
        upload_id = response.get("UploadId")
        if not upload_id:
            raise TemporalArtifactStateError("multipart upload initialization failed")
        return str(upload_id)

    def presign_upload_part(
        self,
        *,
        storage_key: str,
        upload_id: str,
        part_number: int,
        expires_in_seconds: int,
    ) -> tuple[str, dict[str, str]]:
        url = self._client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": storage_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires_in_seconds,
            HttpMethod="PUT",
        )
        return url, {}

    def complete_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> None:
        if not parts:
            raise TemporalArtifactValidationError(
                "parts are required to complete multipart upload"
            )
        normalized_parts: list[dict[str, Any]] = []
        for item in parts:
            etag = str(item.get("etag") or "").strip()
            part_number = int(item.get("part_number"))
            if not etag:
                raise TemporalArtifactValidationError("multipart part etag is required")
            normalized_parts.append({"ETag": etag, "PartNumber": part_number})
        normalized_parts.sort(key=lambda row: row["PartNumber"])

        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=storage_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": normalized_parts},
        )

    def abort_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
    ) -> None:
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=storage_key,
            UploadId=upload_id,
        )

    def presign_download(
        self,
        *,
        storage_key: str,
        expires_in_seconds: int,
        download_filename: str | None = None,
    ) -> str:
        params = {"Bucket": self._bucket, "Key": storage_key}
        if download_filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{download_filename}"'
            )

        return self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in_seconds,
            HttpMethod="GET",
        )


class TemporalArtifactRepository:
    """Persistence helper for Temporal artifacts and execution links."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def create_artifact(
        self,
        *,
        artifact_id: str,
        created_by_principal: str,
        content_type: str | None,
        size_bytes: int | None,
        sha256: str | None,
        storage_backend: db_models.TemporalArtifactStorageBackend,
        storage_key: str,
        encryption: db_models.TemporalArtifactEncryption,
        retention_class: db_models.TemporalArtifactRetentionClass,
        redaction_level: db_models.TemporalArtifactRedactionLevel,
        metadata_json: dict[str, Any] | None,
        expires_at: datetime | None,
        upload_mode: db_models.TemporalArtifactUploadMode,
        upload_id: str | None,
        upload_expires_at: datetime | None,
    ) -> db_models.TemporalArtifact:
        artifact = db_models.TemporalArtifact(
            artifact_id=artifact_id,
            created_by_principal=created_by_principal,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_backend=storage_backend,
            storage_key=storage_key,
            encryption=encryption,
            status=db_models.TemporalArtifactStatus.PENDING_UPLOAD,
            retention_class=retention_class,
            expires_at=expires_at,
            redaction_level=redaction_level,
            metadata_json=dict(metadata_json or {}),
            upload_mode=upload_mode,
            upload_id=upload_id,
            upload_expires_at=upload_expires_at,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def get_artifact(self, artifact_id: str) -> db_models.TemporalArtifact:
        artifact = await self._session.get(db_models.TemporalArtifact, artifact_id)
        if artifact is None:
            raise TemporalArtifactNotFoundError(artifact_id)
        return artifact

    async def add_link(
        self,
        *,
        artifact_id: str,
        execution: ExecutionRef,
    ) -> db_models.TemporalArtifactLink:
        link = db_models.TemporalArtifactLink(
            id=uuid4(),
            artifact_id=artifact_id,
            namespace=execution.namespace,
            workflow_id=execution.workflow_id,
            run_id=execution.run_id,
            link_type=execution.link_type,
            label=execution.label,
            created_by_activity_type=execution.created_by_activity_type,
            created_by_worker=execution.created_by_worker,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def list_links(
        self, artifact_id: str
    ) -> list[db_models.TemporalArtifactLink]:
        stmt: Select[tuple[db_models.TemporalArtifactLink]] = (
            select(db_models.TemporalArtifactLink)
            .where(db_models.TemporalArtifactLink.artifact_id == artifact_id)
            .order_by(
                db_models.TemporalArtifactLink.created_at.desc(),
                db_models.TemporalArtifactLink.id.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_execution(
        self,
        *,
        namespace: str,
        workflow_id: str,
        run_id: str,
        link_type: str | None = None,
    ) -> list[db_models.TemporalArtifact]:
        stmt: Select[tuple[db_models.TemporalArtifact]] = (
            select(db_models.TemporalArtifact)
            .join(
                db_models.TemporalArtifactLink,
                db_models.TemporalArtifactLink.artifact_id
                == db_models.TemporalArtifact.artifact_id,
            )
            .where(
                db_models.TemporalArtifactLink.namespace == namespace,
                db_models.TemporalArtifactLink.workflow_id == workflow_id,
                db_models.TemporalArtifactLink.run_id == run_id,
            )
            .order_by(
                db_models.TemporalArtifactLink.created_at.desc(),
                db_models.TemporalArtifact.created_at.desc(),
                db_models.TemporalArtifact.artifact_id.desc(),
            )
        )
        if link_type:
            stmt = stmt.where(db_models.TemporalArtifactLink.link_type == link_type)

        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def latest_for_execution_link(
        self,
        *,
        namespace: str,
        workflow_id: str,
        run_id: str,
        link_type: str,
    ) -> db_models.TemporalArtifact | None:
        stmt: Select[tuple[db_models.TemporalArtifact]] = (
            select(db_models.TemporalArtifact)
            .join(
                db_models.TemporalArtifactLink,
                db_models.TemporalArtifactLink.artifact_id
                == db_models.TemporalArtifact.artifact_id,
            )
            .where(
                db_models.TemporalArtifactLink.namespace == namespace,
                db_models.TemporalArtifactLink.workflow_id == workflow_id,
                db_models.TemporalArtifactLink.run_id == run_id,
                db_models.TemporalArtifactLink.link_type == link_type,
            )
            .order_by(
                db_models.TemporalArtifactLink.created_at.desc(),
                db_models.TemporalArtifact.created_at.desc(),
                db_models.TemporalArtifact.artifact_id.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def pin_artifact(
        self,
        *,
        artifact_id: str,
        principal: str,
        reason: str | None,
    ) -> db_models.TemporalArtifactPin:
        await self.unpin_artifact(artifact_id)
        pin = db_models.TemporalArtifactPin(
            id=uuid4(),
            artifact_id=artifact_id,
            pinned_by_principal=principal,
            reason=reason,
        )
        self._session.add(pin)
        await self._session.flush()
        return pin

    async def unpin_artifact(self, artifact_id: str) -> None:
        stmt = delete(db_models.TemporalArtifactPin).where(
            db_models.TemporalArtifactPin.artifact_id == artifact_id
        )
        await self._session.execute(stmt)

    async def get_pin(self, artifact_id: str) -> db_models.TemporalArtifactPin | None:
        stmt: Select[tuple[db_models.TemporalArtifactPin]] = (
            select(db_models.TemporalArtifactPin)
            .where(db_models.TemporalArtifactPin.artifact_id == artifact_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_expired_artifacts(
        self,
        *,
        now: datetime,
    ) -> list[db_models.TemporalArtifact]:
        pinned_exists = (
            exists()
            .where(
                db_models.TemporalArtifactPin.artifact_id
                == db_models.TemporalArtifact.artifact_id
            )
            .correlate(db_models.TemporalArtifact)
        )

        stmt: Select[tuple[db_models.TemporalArtifact]] = (
            select(db_models.TemporalArtifact)
            .where(
                db_models.TemporalArtifact.expires_at.is_not(None),
                db_models.TemporalArtifact.expires_at <= now,
                db_models.TemporalArtifact.retention_class
                != db_models.TemporalArtifactRetentionClass.PINNED,
                ~pinned_exists,
            )
            .order_by(
                db_models.TemporalArtifact.expires_at.asc(),
                db_models.TemporalArtifact.created_at.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_deleted_for_hard_delete(
        self,
        *,
        cutoff: datetime,
    ) -> list[db_models.TemporalArtifact]:
        stmt: Select[tuple[db_models.TemporalArtifact]] = (
            select(db_models.TemporalArtifact)
            .where(
                db_models.TemporalArtifact.status
                == db_models.TemporalArtifactStatus.DELETED,
                db_models.TemporalArtifact.deleted_at.is_not(None),
                db_models.TemporalArtifact.deleted_at <= cutoff,
                db_models.TemporalArtifact.hard_deleted_at.is_(None),
            )
            .order_by(db_models.TemporalArtifact.deleted_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class TemporalArtifactService:
    """Service implementing Temporal artifact semantics for local/dev runtime."""

    def __init__(
        self,
        repository: TemporalArtifactRepository,
        *,
        store: TemporalArtifactStore | None = None,
        default_namespace: str | None = None,
        presign_ttl_seconds: int | None = None,
        direct_upload_max_bytes: int | None = None,
        lifecycle_hard_delete_after_seconds: int | None = None,
    ) -> None:
        self._repository = repository
        self._store: TemporalArtifactStore = store or self._build_store_from_settings()
        self._default_namespace = (
            default_namespace
            or settings.workflow.temporal_artifact_default_namespace
            or "moonmind"
        )
        self._presign_ttl_seconds = max(
            1,
            int(
                presign_ttl_seconds
                if presign_ttl_seconds is not None
                else settings.workflow.temporal_artifact_presign_ttl_seconds
            ),
        )
        configured_limit = (
            direct_upload_max_bytes
            if direct_upload_max_bytes is not None
            else settings.workflow.temporal_artifact_direct_upload_max_bytes
        )
        self._direct_upload_max_bytes = max(1, int(configured_limit))
        hard_delete_after_seconds = (
            lifecycle_hard_delete_after_seconds
            if lifecycle_hard_delete_after_seconds is not None
            else settings.workflow.temporal_artifact_lifecycle_hard_delete_after_seconds
        )
        self._lifecycle = _StorageLifecycleConfig(
            hard_delete_after=timedelta(seconds=max(0, int(hard_delete_after_seconds))),
        )

    @staticmethod
    def _build_store_from_settings() -> TemporalArtifactStore:
        backend = settings.workflow.temporal_artifact_backend
        if backend == db_models.TemporalArtifactStorageBackend.LOCAL_FS.value:
            return LocalTemporalArtifactStore(settings.workflow.temporal_artifact_root)
        if backend == db_models.TemporalArtifactStorageBackend.S3.value:
            return S3TemporalArtifactStore(
                endpoint_url=settings.workflow.temporal_artifact_s3_endpoint,
                bucket=settings.workflow.temporal_artifact_s3_bucket,
                access_key_id=settings.workflow.temporal_artifact_s3_access_key_id,
                secret_access_key=settings.workflow.temporal_artifact_s3_secret_access_key,
                region_name=settings.workflow.temporal_artifact_s3_region,
                use_ssl=settings.workflow.temporal_artifact_s3_use_ssl,
            )
        raise TemporalArtifactValidationError(
            f"Unsupported temporal artifact backend '{backend}'"
        )

    @staticmethod
    def _coerce_execution_ref(
        link: dict[str, Any] | ExecutionRef | None,
    ) -> ExecutionRef | None:
        if link is None:
            return None
        if isinstance(link, ExecutionRef):
            return link
        namespace = str(link.get("namespace") or "").strip()
        workflow_id = str(link.get("workflow_id") or "").strip()
        run_id = str(link.get("run_id") or "").strip()
        link_type = str(link.get("link_type") or "").strip()
        if not namespace or not workflow_id or not run_id or not link_type:
            raise TemporalArtifactValidationError(
                "link requires namespace, workflow_id, run_id, and link_type"
            )
        return ExecutionRef(
            namespace=namespace,
            workflow_id=workflow_id,
            run_id=run_id,
            link_type=link_type,
            label=(link.get("label") or None),
            created_by_activity_type=(link.get("created_by_activity_type") or None),
            created_by_worker=(link.get("created_by_worker") or None),
        )

    @staticmethod
    def _owner_principal(artifact: db_models.TemporalArtifact) -> str:
        return (artifact.created_by_principal or "").strip()

    @staticmethod
    def _is_service_principal(principal: str) -> bool:
        return principal.startswith("service:")

    def _assert_read_access(
        self,
        artifact: db_models.TemporalArtifact,
        *,
        principal: str,
    ) -> None:
        if settings.oidc.AUTH_PROVIDER == "disabled":
            return
        owner = self._owner_principal(artifact)
        if owner and owner != principal and not self._is_service_principal(principal):
            raise TemporalArtifactAuthorizationError(
                f"principal '{principal}' cannot read artifact {artifact.artifact_id}"
            )

    def _assert_mutation_access(
        self,
        artifact: db_models.TemporalArtifact,
        *,
        principal: str,
    ) -> None:
        if settings.oidc.AUTH_PROVIDER == "disabled":
            return
        owner = self._owner_principal(artifact)
        if owner and owner != principal and not self._is_service_principal(principal):
            raise TemporalArtifactAuthorizationError(
                f"principal '{principal}' cannot mutate artifact {artifact.artifact_id}"
            )

    def _raw_access_allowed(
        self,
        artifact: db_models.TemporalArtifact,
        *,
        principal: str,
    ) -> bool:
        if (
            artifact.redaction_level
            is not db_models.TemporalArtifactRedactionLevel.RESTRICTED
        ):
            return True
        owner = self._owner_principal(artifact)
        if owner and owner == principal:
            return True
        if self._is_service_principal(principal):
            return True
        return False

    def _assert_raw_access(
        self,
        artifact: db_models.TemporalArtifact,
        *,
        principal: str,
    ) -> None:
        if self._raw_access_allowed(artifact, principal=principal):
            return
        raise TemporalArtifactAuthorizationError(
            f"principal '{principal}' cannot access restricted raw artifact {artifact.artifact_id}"
        )

    @staticmethod
    def _normalize_parts(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not parts:
            return []
        normalized: list[dict[str, Any]] = []
        for item in parts:
            normalized.append(
                {
                    "part_number": int(item.get("part_number")),
                    "etag": str(item.get("etag") or "").strip(),
                }
            )
        return normalized

    @staticmethod
    def _compute_digest_and_size(payload: bytes) -> tuple[str, int]:
        return hashlib.sha256(payload).hexdigest(), len(payload)

    def _validate_integrity_declarations(
        self,
        artifact: db_models.TemporalArtifact,
        *,
        digest: str,
        size_bytes: int,
    ) -> None:
        expected_sha = _validate_sha256(artifact.sha256)
        if expected_sha is not None and digest != expected_sha:
            raise TemporalArtifactValidationError(
                "sha256 mismatch during upload completion"
            )
        if artifact.size_bytes is not None and artifact.size_bytes != size_bytes:
            raise TemporalArtifactValidationError(
                "size_bytes mismatch during upload completion"
            )

    async def _create_preview_if_required(
        self,
        *,
        artifact: db_models.TemporalArtifact,
        principal: str,
        payload: bytes,
        policy: str,
    ) -> None:
        if (
            artifact.redaction_level
            is not db_models.TemporalArtifactRedactionLevel.RESTRICTED
        ):
            return
        metadata = dict(artifact.metadata_json or {})
        if metadata.get("preview_artifact_id"):
            return

        text = payload.decode("utf-8", errors="ignore")
        text = re.sub(
            r"(?i)(token|password|secret)\s*[:=]\s*[^\s]+", r"\1=[REDACTED]", text
        )
        text = text[:_PREVIEW_MAX_BYTES]

        preview_artifact, _upload = await self.create(
            principal=principal,
            content_type="text/plain",
            retention_class=db_models.TemporalArtifactRetentionClass.EPHEMERAL,
            metadata_json={
                "preview_of": artifact.artifact_id,
                "policy": policy,
            },
            redaction_level=db_models.TemporalArtifactRedactionLevel.PREVIEW_ONLY,
            encryption=db_models.TemporalArtifactEncryption.NONE,
        )
        await self.write_complete(
            artifact_id=preview_artifact.artifact_id,
            principal=principal,
            payload=text.encode("utf-8"),
            content_type="text/plain",
        )

        metadata["preview_artifact_id"] = preview_artifact.artifact_id
        artifact.metadata_json = metadata
        await self._repository.commit()

    async def create(
        self,
        *,
        principal: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        retention_class: db_models.TemporalArtifactRetentionClass | None = None,
        link: dict[str, Any] | ExecutionRef | None = None,
        metadata_json: dict[str, Any] | None = None,
        encryption: db_models.TemporalArtifactEncryption = db_models.TemporalArtifactEncryption.NONE,
        redaction_level: db_models.TemporalArtifactRedactionLevel = db_models.TemporalArtifactRedactionLevel.NONE,
    ) -> tuple[db_models.TemporalArtifact, ArtifactUploadDescriptor]:
        now = datetime.now(UTC)
        declared_size: int | None = None
        if size_bytes is not None:
            declared_size = int(size_bytes)
            if declared_size < 0:
                raise TemporalArtifactValidationError("size_bytes must be non-negative")

        execution_ref = self._coerce_execution_ref(link)
        derived_retention = _derive_retention(
            retention_class,
            execution_ref.link_type if execution_ref else None,
        )
        expires_at = _expires_at_for_retention(derived_retention, now)
        artifact_id = generate_artifact_id(now)
        namespace = (
            execution_ref.namespace if execution_ref else self._default_namespace
        )
        storage_key = self._store.build_storage_key(
            namespace=namespace,
            artifact_id=artifact_id,
            now=now,
        )
        upload_expires_at = now + timedelta(seconds=self._presign_ttl_seconds)

        mode = db_models.TemporalArtifactUploadMode.SINGLE_PUT
        upload_id: str | None = None
        upload_url: str | None = None
        required_headers: dict[str, str] = {}

        if declared_size is not None and declared_size > self._direct_upload_max_bytes:
            if not self._store.supports_multipart:
                raise TemporalArtifactValidationError(
                    "artifact exceeds max bytes "
                    f"({self._direct_upload_max_bytes}) for direct uploads"
                )
            mode = db_models.TemporalArtifactUploadMode.MULTIPART
            upload_id = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._store.create_multipart_upload(
                    storage_key=storage_key,
                    content_type=content_type,
                ),
            )
        else:
            if self._store.backend is db_models.TemporalArtifactStorageBackend.S3:
                upload_url, required_headers = self._store.presign_single_upload(
                    storage_key=storage_key,
                    content_type=content_type,
                    expires_in_seconds=self._presign_ttl_seconds,
                )
            else:
                upload_url = f"/api/artifacts/{artifact_id}/content"

        artifact = await self._repository.create_artifact(
            artifact_id=artifact_id,
            created_by_principal=principal,
            content_type=(content_type or None),
            size_bytes=declared_size,
            sha256=_validate_sha256(sha256),
            storage_backend=self._store.backend,
            storage_key=storage_key,
            encryption=encryption,
            retention_class=derived_retention,
            redaction_level=redaction_level,
            metadata_json=metadata_json,
            expires_at=expires_at,
            upload_mode=mode,
            upload_id=upload_id,
            upload_expires_at=upload_expires_at,
        )
        if execution_ref is not None:
            await self._repository.add_link(
                artifact_id=artifact.artifact_id,
                execution=execution_ref,
            )

        upload_descriptor = ArtifactUploadDescriptor(
            mode=mode.value,
            upload_url=upload_url,
            upload_id=upload_id,
            expires_at=upload_expires_at,
            max_size_bytes=self._direct_upload_max_bytes,
            required_headers=required_headers,
        )
        logger.info(
            "Temporal artifact create operation principal=%s artifact_id=%s mode=%s backend=%s",
            principal,
            artifact.artifact_id,
            mode.value,
            self._store.backend.value,
        )
        await self._repository.commit()
        return artifact, upload_descriptor

    async def write_complete(
        self,
        *,
        artifact_id: str,
        principal: str,
        payload: bytes,
        content_type: str | None = None,
    ) -> db_models.TemporalArtifact:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)

        if artifact.status is db_models.TemporalArtifactStatus.DELETED:
            raise TemporalArtifactStateError("artifact is deleted")
        if artifact.status is db_models.TemporalArtifactStatus.COMPLETE:
            return artifact

        if artifact.upload_mode is db_models.TemporalArtifactUploadMode.MULTIPART:
            raise TemporalArtifactStateError(
                "artifact expects multipart completion; use /complete with parts"
            )
        if len(payload) > self._direct_upload_max_bytes:
            raise TemporalArtifactValidationError(
                f"artifact exceeds max bytes ({self._direct_upload_max_bytes})"
            )

        digest, actual_size = self._compute_digest_and_size(payload)
        if (
            artifact.upload_mode is db_models.TemporalArtifactUploadMode.SINGLE_PUT
            and actual_size > self._direct_upload_max_bytes
        ):
            artifact.status = db_models.TemporalArtifactStatus.FAILED
            await self._repository.commit()
            raise TemporalArtifactValidationError(
                f"artifact exceeds max bytes ({self._direct_upload_max_bytes})"
            )
        try:
            self._validate_integrity_declarations(
                artifact,
                digest=digest,
                size_bytes=actual_size,
            )
        except TemporalArtifactValidationError:
            artifact.status = db_models.TemporalArtifactStatus.FAILED
            await self._repository.commit()
            raise

        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._store.write_bytes(
                artifact.storage_key,
                payload,
                content_type=content_type or artifact.content_type,
            ),
        )
        artifact.sha256 = digest
        artifact.size_bytes = actual_size
        artifact.content_type = content_type or artifact.content_type or None
        artifact.status = db_models.TemporalArtifactStatus.COMPLETE
        artifact.upload_id = None
        artifact.upload_expires_at = None
        await self._repository.commit()

        await self._create_preview_if_required(
            artifact=artifact,
            principal=principal,
            payload=payload,
            policy="auto-generated",
        )

        logger.info(
            "Temporal artifact write_complete operation principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact

    async def presign_upload_part(
        self,
        *,
        artifact_id: str,
        principal: str,
        part_number: int,
    ) -> ArtifactUploadPartDescriptor:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)

        if artifact.status is not db_models.TemporalArtifactStatus.PENDING_UPLOAD:
            raise TemporalArtifactStateError("artifact upload is already finalized")
        if artifact.upload_mode is not db_models.TemporalArtifactUploadMode.MULTIPART:
            raise TemporalArtifactStateError("artifact is not in multipart upload mode")
        if not artifact.upload_id:
            raise TemporalArtifactStateError("multipart upload session is missing")
        if part_number < 1:
            raise TemporalArtifactValidationError("part_number must be >= 1")

        url, required_headers = self._store.presign_upload_part(
            storage_key=artifact.storage_key,
            upload_id=artifact.upload_id,
            part_number=part_number,
            expires_in_seconds=self._presign_ttl_seconds,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=self._presign_ttl_seconds)
        logger.info(
            "Temporal artifact presign_upload_part principal=%s artifact_id=%s part=%s",
            principal,
            artifact_id,
            part_number,
        )
        return ArtifactUploadPartDescriptor(
            part_number=part_number,
            url=url,
            expires_at=expires_at,
            required_headers=required_headers,
        )

    async def complete(
        self,
        *,
        artifact_id: str,
        principal: str,
        parts: list[dict[str, Any]] | None = None,
    ) -> db_models.TemporalArtifact:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)

        if artifact.status is db_models.TemporalArtifactStatus.COMPLETE:
            return artifact
        if artifact.status is db_models.TemporalArtifactStatus.DELETED:
            raise TemporalArtifactStateError("artifact is deleted")

        payload: bytes
        if artifact.upload_mode is db_models.TemporalArtifactUploadMode.MULTIPART:
            if not artifact.upload_id:
                raise TemporalArtifactStateError("multipart upload session is missing")
            normalized_parts = self._normalize_parts(parts)
            if not normalized_parts:
                raise TemporalArtifactValidationError(
                    "parts are required for multipart completion"
                )
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._store.complete_multipart_upload(
                        storage_key=artifact.storage_key,
                        upload_id=artifact.upload_id,
                        parts=normalized_parts,
                    ),
                )
            except Exception:
                artifact.status = db_models.TemporalArtifactStatus.FAILED
                await self._repository.commit()
                raise
            payload = await asyncio.get_running_loop().run_in_executor(
                None, self._store.read_bytes, artifact.storage_key
            )
        else:
            try:
                payload = await asyncio.get_running_loop().run_in_executor(
                    None, self._store.read_bytes, artifact.storage_key
                )
            except Exception as exc:
                raise TemporalArtifactStateError(
                    "artifact upload is not complete"
                ) from exc

        digest, actual_size = self._compute_digest_and_size(payload)
        if (
            artifact.upload_mode is db_models.TemporalArtifactUploadMode.SINGLE_PUT
            and actual_size > self._direct_upload_max_bytes
        ):
            artifact.status = db_models.TemporalArtifactStatus.FAILED
            await self._repository.commit()
            raise TemporalArtifactValidationError(
                f"artifact exceeds max bytes ({self._direct_upload_max_bytes})"
            )
        try:
            self._validate_integrity_declarations(
                artifact,
                digest=digest,
                size_bytes=actual_size,
            )
        except TemporalArtifactValidationError:
            artifact.status = db_models.TemporalArtifactStatus.FAILED
            await self._repository.commit()
            raise

        artifact.sha256 = digest
        artifact.size_bytes = actual_size
        artifact.status = db_models.TemporalArtifactStatus.COMPLETE
        artifact.upload_id = None
        artifact.upload_expires_at = None
        await self._repository.commit()

        await self._create_preview_if_required(
            artifact=artifact,
            principal=principal,
            payload=payload,
            policy="auto-generated",
        )

        logger.info(
            "Temporal artifact complete operation principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
    ) -> tuple[db_models.TemporalArtifact, bytes]:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        if artifact.status is not db_models.TemporalArtifactStatus.COMPLETE:
            raise TemporalArtifactStateError("artifact is not readable")
        if not allow_restricted_raw:
            self._assert_raw_access(artifact, principal=principal)
        try:
            data = await asyncio.get_running_loop().run_in_executor(
                None, self._store.read_bytes, artifact.storage_key
            )
        except Exception as exc:
            raise TemporalArtifactStateError("artifact bytes are missing") from exc
        logger.info(
            "Temporal artifact read operation principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact, data

    async def read_chunks(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
        chunk_size: int = _STREAM_CHUNK_BYTES,
    ) -> tuple[db_models.TemporalArtifact, Iterable[bytes]]:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        if artifact.status is not db_models.TemporalArtifactStatus.COMPLETE:
            raise TemporalArtifactStateError("artifact is not readable")
        if not allow_restricted_raw:
            self._assert_raw_access(artifact, principal=principal)
        return artifact, self._store.read_chunks(
            artifact.storage_key, chunk_size=chunk_size
        )

    async def read_path(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
    ) -> tuple[db_models.TemporalArtifact, Path]:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        if artifact.status is not db_models.TemporalArtifactStatus.COMPLETE:
            raise TemporalArtifactStateError("artifact is not readable")
        if not allow_restricted_raw:
            self._assert_raw_access(artifact, principal=principal)
        try:
            path = await asyncio.get_running_loop().run_in_executor(
                None, self._store.read_path, artifact.storage_key
            )
        except TemporalArtifactValidationError:
            raise
        except Exception as exc:
            raise TemporalArtifactStateError("artifact bytes are missing") from exc
        if not path.exists():
            raise TemporalArtifactStateError("artifact bytes are missing")
        return artifact, path

    async def get_read_policy(
        self,
        *,
        artifact: db_models.TemporalArtifact,
        principal: str,
    ) -> ArtifactReadPolicy:
        metadata = dict(artifact.metadata_json or {})
        preview_ref: ArtifactRef | None = None
        preview_artifact_id = metadata.get("preview_artifact_id")
        if preview_artifact_id:
            try:
                preview_artifact = await self._repository.get_artifact(
                    str(preview_artifact_id)
                )
            except TemporalArtifactNotFoundError:
                preview_artifact = None
            if preview_artifact is not None:
                preview_ref = build_artifact_ref(preview_artifact)

        raw_access_allowed = self._raw_access_allowed(artifact, principal=principal)
        default_read_ref = (
            preview_ref
            if preview_ref is not None and not raw_access_allowed
            else build_artifact_ref(artifact)
        )
        return ArtifactReadPolicy(
            raw_access_allowed=raw_access_allowed,
            preview_artifact_ref=preview_ref,
            default_read_ref=default_read_ref,
        )

    async def get_metadata(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> tuple[
        db_models.TemporalArtifact,
        list[db_models.TemporalArtifactLink],
        bool,
        ArtifactReadPolicy,
    ]:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        links = await self._repository.list_links(artifact.artifact_id)
        pinned = await self._repository.get_pin(artifact.artifact_id)
        read_policy = await self.get_read_policy(artifact=artifact, principal=principal)
        return artifact, links, pinned is not None, read_policy

    async def presign_download(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> tuple[db_models.TemporalArtifact, datetime, str]:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        if artifact.status is not db_models.TemporalArtifactStatus.COMPLETE:
            raise TemporalArtifactStateError("artifact is not readable")
        self._assert_raw_access(artifact, principal=principal)

        expires_at = datetime.now(UTC) + timedelta(seconds=self._presign_ttl_seconds)
        if artifact.storage_backend is db_models.TemporalArtifactStorageBackend.S3:
            is_json = (artifact.content_type or "").split(";", 1)[0].strip().lower() == "application/json"
            download_filename = f"{artifact.artifact_id}.json" if is_json else artifact.artifact_id
            url = self._store.presign_download(
                storage_key=artifact.storage_key,
                expires_in_seconds=self._presign_ttl_seconds,
                download_filename=download_filename,
            )
        else:
            url = f"/api/artifacts/{artifact.artifact_id}/download"
        logger.info(
            "Temporal artifact presign_download principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact, expires_at, url

    async def link_artifact(
        self,
        *,
        artifact_id: str,
        principal: str,
        execution_ref: dict[str, Any] | ExecutionRef,
    ) -> db_models.TemporalArtifactLink:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)
        link = await self._repository.add_link(
            artifact_id=artifact_id,
            execution=self._coerce_execution_ref(execution_ref),
        )
        if (
            artifact.retention_class
            is db_models.TemporalArtifactRetentionClass.STANDARD
        ):
            derived = _derive_retention(None, link.link_type)
            artifact.retention_class = derived
            artifact.expires_at = _expires_at_for_retention(derived, datetime.now(UTC))
        await self._repository.commit()
        logger.info(
            "Temporal artifact link operation principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return link

    async def list_for_execution(
        self,
        *,
        namespace: str,
        workflow_id: str,
        run_id: str,
        principal: str,
        link_type: str | None = None,
        latest_only: bool = False,
    ) -> list[db_models.TemporalArtifact]:
        if settings.oidc.AUTH_PROVIDER != "disabled" and not principal:
            raise TemporalArtifactAuthorizationError("principal required")
        if latest_only and link_type:
            latest = await self._repository.latest_for_execution_link(
                namespace=namespace,
                workflow_id=workflow_id,
                run_id=run_id,
                link_type=link_type,
            )
            artifacts = [latest] if latest else []
        else:
            artifacts = await self._repository.list_for_execution(
                namespace=namespace,
                workflow_id=workflow_id,
                run_id=run_id,
                link_type=link_type,
            )

        if settings.oidc.AUTH_PROVIDER == "disabled":
            return artifacts

        visible: list[db_models.TemporalArtifact] = []
        for artifact in artifacts:
            owner = self._owner_principal(artifact)
            if not owner or owner == principal or self._is_service_principal(principal):
                visible.append(artifact)
        return visible

    async def pin(
        self,
        *,
        artifact_id: str,
        principal: str,
        reason: str | None,
    ) -> db_models.TemporalArtifactPin:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)
        pin = await self._repository.pin_artifact(
            artifact_id=artifact_id,
            principal=principal,
            reason=reason,
        )
        artifact.retention_class = db_models.TemporalArtifactRetentionClass.PINNED
        artifact.expires_at = None
        await self._repository.commit()
        return pin

    async def unpin(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> None:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)
        await self._repository.unpin_artifact(artifact_id)
        if artifact.retention_class is db_models.TemporalArtifactRetentionClass.PINNED:
            artifact.retention_class = db_models.TemporalArtifactRetentionClass.STANDARD
            artifact.expires_at = _expires_at_for_retention(
                db_models.TemporalArtifactRetentionClass.STANDARD,
                datetime.now(UTC),
            )
        await self._repository.commit()

    async def soft_delete(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> db_models.TemporalArtifact:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)
        if artifact.status is db_models.TemporalArtifactStatus.DELETED:
            return artifact
        artifact.status = db_models.TemporalArtifactStatus.DELETED
        artifact.deleted_at = datetime.now(UTC)
        await self._repository.unpin_artifact(artifact.artifact_id)
        await self._repository.commit()
        logger.info(
            "Temporal artifact soft_delete principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact

    async def hard_delete(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> db_models.TemporalArtifact:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_mutation_access(artifact, principal=principal)
        if artifact.hard_deleted_at is not None:
            return artifact
        if artifact.status is not db_models.TemporalArtifactStatus.DELETED:
            raise TemporalArtifactStateError(
                "artifact must be soft-deleted before hard delete"
            )

        await asyncio.get_running_loop().run_in_executor(
            None, self._store.delete, artifact.storage_key
        )
        now = datetime.now(UTC)
        artifact.hard_deleted_at = now
        artifact.tombstoned_at = now
        await self._repository.commit()
        logger.info(
            "Temporal artifact hard_delete principal=%s artifact_id=%s",
            principal,
            artifact.artifact_id,
        )
        return artifact

    async def sweep_lifecycle(
        self,
        *,
        principal: str,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> LifecycleSweepSummary:
        sweep_now = now or datetime.now(UTC)
        lifecycle_run_id = run_id or str(uuid4())
        expired = await self._repository.list_expired_artifacts(now=sweep_now)

        soft_deleted = 0
        for artifact in expired:
            if artifact.status is not db_models.TemporalArtifactStatus.DELETED:
                artifact.status = db_models.TemporalArtifactStatus.DELETED
                artifact.deleted_at = sweep_now
                soft_deleted += 1
            artifact.last_lifecycle_run_id = lifecycle_run_id

        cutoff = sweep_now - self._lifecycle.hard_delete_after
        hard_candidates = await self._repository.list_deleted_for_hard_delete(
            cutoff=cutoff
        )
        hard_deleted = 0
        for artifact in hard_candidates:
            await asyncio.get_running_loop().run_in_executor(
                None, self._store.delete, artifact.storage_key
            )
            artifact.hard_deleted_at = sweep_now
            artifact.tombstoned_at = sweep_now
            artifact.last_lifecycle_run_id = lifecycle_run_id
            hard_deleted += 1

        await self._repository.commit()
        logger.info(
            "Temporal artifact sweep_lifecycle principal=%s run_id=%s soft_deleted=%s hard_deleted=%s",
            principal,
            lifecycle_run_id,
            soft_deleted,
            hard_deleted,
        )
        return LifecycleSweepSummary(
            run_id=lifecycle_run_id,
            expired_candidate_count=len(expired),
            soft_deleted_count=soft_deleted,
            hard_deleted_count=hard_deleted,
        )

    async def compute_preview(
        self,
        *,
        artifact_id: str,
        principal: str,
        policy: str | None = None,
    ) -> ArtifactRef:
        artifact = await self._repository.get_artifact(artifact_id)
        self._assert_read_access(artifact, principal=principal)
        _artifact, payload = await self.read(
            artifact_id=artifact_id,
            principal=principal,
            allow_restricted_raw=True,
        )
        await self._create_preview_if_required(
            artifact=artifact,
            principal=principal,
            payload=payload,
            policy=policy or "manual",
        )
        preview_id = (artifact.metadata_json or {}).get("preview_artifact_id")
        if not preview_id:
            raise TemporalArtifactStateError("preview artifact is unavailable")
        preview = await self._repository.get_artifact(str(preview_id))
        return build_artifact_ref(preview)

    async def write_integration_event_artifact(
        self,
        *,
        principal: str,
        execution: ExecutionRef,
        integration_name: str,
        correlation_id: str,
        payload: bytes,
        content_type: str = "application/json",
        event_type: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        redaction_level: db_models.TemporalArtifactRedactionLevel = db_models.TemporalArtifactRedactionLevel.RESTRICTED,
    ) -> ArtifactRef:
        """Persist one raw integration callback/event payload as an artifact."""

        metadata = dict(metadata_json or {})
        metadata.update(
            {
                "integration_name": integration_name,
                "correlation_id": correlation_id,
                "event_type": event_type,
                "artifact_kind": "integration_event",
            }
        )
        artifact, _upload = await self.create(
            principal=principal,
            content_type=content_type,
            size_bytes=len(payload),
            link=ExecutionRef(
                namespace=execution.namespace,
                workflow_id=execution.workflow_id,
                run_id=execution.run_id,
                link_type="debug.trace",
                label=execution.label or f"{integration_name}:callback",
            ),
            metadata_json=metadata,
            redaction_level=redaction_level,
        )
        artifact = await self.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload,
            content_type=content_type,
        )
        return build_artifact_ref(artifact)

    async def write_integration_result_artifact(
        self,
        *,
        principal: str,
        execution: ExecutionRef,
        integration_name: str,
        correlation_id: str,
        payload: dict[str, Any] | bytes,
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Persist one integration result envelope using standard output retention."""

        encoded = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, sort_keys=True).encode("utf-8")
        )
        metadata = {
            "integration_name": integration_name,
            "correlation_id": correlation_id,
            "artifact_kind": "integration_result",
        }
        metadata.update(metadata_json or {})
        artifact, _upload = await self.create(
            principal=principal,
            content_type=content_type,
            size_bytes=len(encoded),
            link=ExecutionRef(
                namespace=execution.namespace,
                workflow_id=execution.workflow_id,
                run_id=execution.run_id,
                link_type="output.primary",
                label=execution.label or f"{integration_name}:result",
            ),
            metadata_json=metadata,
            redaction_level=db_models.TemporalArtifactRedactionLevel.RESTRICTED,
        )
        artifact = await self.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=encoded,
            content_type=content_type,
        )
        return build_artifact_ref(artifact)

    async def write_integration_failure_artifact(
        self,
        *,
        principal: str,
        execution: ExecutionRef,
        integration_name: str,
        correlation_id: str,
        external_operation_id: str,
        normalized_status: str,
        provider_status: str | None,
        summary: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Persist a compact provider failure summary with restricted preview semantics."""

        payload = {
            "integrationName": integration_name,
            "correlationId": correlation_id,
            "externalOperationId": external_operation_id,
            "normalizedStatus": normalized_status,
            "providerStatus": provider_status,
            "summary": summary,
            "diagnostics": diagnostics or {},
        }
        return await self.write_integration_result_artifact(
            principal=principal,
            execution=ExecutionRef(
                namespace=execution.namespace,
                workflow_id=execution.workflow_id,
                run_id=execution.run_id,
                link_type="output.summary",
                label=execution.label or f"{integration_name}:failure",
            ),
            integration_name=integration_name,
            correlation_id=correlation_id,
            payload=payload,
            metadata_json={"artifact_kind": "integration_failure"},
        )


class TemporalArtifactActivities:
    """Activity-friendly facade used by Temporal workflow/activity code."""

    def __init__(self, service: TemporalArtifactService) -> None:
        self._service = service

    @staticmethod
    def _normalize_activity_artifact_id(
        artifact_ref: ArtifactRef | Mapping[str, Any] | str,
    ) -> str:
        if isinstance(artifact_ref, ArtifactRef):
            return artifact_ref.artifact_id
        if isinstance(artifact_ref, Mapping):
            raw_artifact_id = artifact_ref.get("artifact_id") or artifact_ref.get(
                "artifactId"
            )
            normalized = str(raw_artifact_id or "").strip()
            if not normalized:
                raise TemporalArtifactValidationError(
                    "artifact_ref.artifact_id is required"
                )
            return normalized
        normalized = str(artifact_ref or "").strip()
        if not normalized:
            raise TemporalArtifactValidationError("artifact_ref is required")
        return normalized

    async def artifact_create(
        self, *, principal: str, **kwargs: Any
    ) -> tuple[ArtifactRef, ArtifactUploadDescriptor]:
        normalized_kwargs = dict(kwargs)
        legacy_name = str(normalized_kwargs.pop("name", "") or "").strip()
        if legacy_name:
            metadata_json = normalized_kwargs.get("metadata_json")
            if isinstance(metadata_json, Mapping):
                metadata = dict(metadata_json)
            else:
                metadata = {}
            metadata.setdefault("name", legacy_name)
            normalized_kwargs["metadata_json"] = metadata
        artifact, upload = await self._service.create(
            principal=principal,
            **normalized_kwargs,
        )
        return build_artifact_ref(artifact), upload

    async def artifact_read(
        self,
        *,
        artifact_ref: ArtifactRef | Mapping[str, Any] | str,
        principal: str,
    ) -> bytes:
        _artifact, payload = await self._service.read(
            artifact_id=self._normalize_activity_artifact_id(artifact_ref),
            principal=principal,
        )
        return payload

    async def artifact_write_complete(
        self,
        *,
        artifact_id: str,
        payload: bytes | str | list[int],
        principal: str,
        content_type: str | None = None,
    ) -> ArtifactRef:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif isinstance(payload, list):
            payload = bytes(payload)
        elif not isinstance(payload, bytes):
            payload = bytes(payload)

        artifact = await self._service.write_complete(
            artifact_id=artifact_id,
            principal=principal,
            payload=payload,
            content_type=content_type,
        )
        return build_artifact_ref(artifact)

    async def artifact_list_for_execution(
        self,
        *,
        namespace: str,
        workflow_id: str,
        run_id: str,
        principal: str,
        link_type: str | None = None,
        latest_only: bool = False,
    ) -> list[ArtifactRef]:
        artifacts = await self._service.list_for_execution(
            namespace=namespace,
            workflow_id=workflow_id,
            run_id=run_id,
            principal=principal,
            link_type=link_type,
            latest_only=latest_only,
        )
        return [build_artifact_ref(item) for item in artifacts]

    async def artifact_compute_preview(
        self,
        *,
        artifact_ref: ArtifactRef,
        principal: str,
        policy: str | None = None,
    ) -> ArtifactRef:
        return await self._service.compute_preview(
            artifact_id=artifact_ref.artifact_id,
            principal=principal,
            policy=policy,
        )

    async def artifact_lifecycle_sweep(
        self,
        *,
        principal: str,
        run_id: str | None = None,
    ) -> LifecycleSweepSummary:
        return await self._service.sweep_lifecycle(principal=principal, run_id=run_id)

    async def artifact_sweep_lifecycle(
        self,
        *,
        principal: str,
        run_id: str | None = None,
    ) -> LifecycleSweepSummary:
        """Backward-compatible alias for the canonical lifecycle sweep activity."""

        return await self.artifact_lifecycle_sweep(
            principal=principal,
            run_id=run_id,
        )

    async def artifact_link(
        self,
        *,
        artifact_id: str,
        principal: str,
        execution_ref: dict[str, Any] | ExecutionRef,
    ) -> str:
        link = await self._service.link_artifact(
            artifact_id=artifact_id,
            principal=principal,
            execution_ref=execution_ref,
        )
        return str(link.id)

    async def artifact_pin(
        self,
        *,
        artifact_id: str,
        principal: str,
        reason: str | None = None,
    ) -> str:
        pin = await self._service.pin(
            artifact_id=artifact_id,
            principal=principal,
            reason=reason,
        )
        return str(pin.id)

    async def artifact_unpin(
        self,
        *,
        artifact_id: str,
        principal: str,
    ) -> None:
        await self._service.unpin(
            artifact_id=artifact_id,
            principal=principal,
        )

    async def auth_profile_list(
        self,
        *,
        runtime_id: str,
    ) -> dict[str, Any]:
        """List enabled auth profiles for a runtime family.

        Returns a dict with a ``profiles`` key containing a list of profile
        dicts suitable for the AuthProfileManager workflow.

        Uses the existing DB session from the repository.  The caller
        (AuthProfileManager workflow) expects the keys defined in
        ``ManagedAgentAuthProfile`` schema (DOC-REQ-010).
        """
        from sqlalchemy import select

        from api_service.db.models import ManagedAgentAuthProfile
        from api_service.db.base import get_async_session_context

        async with get_async_session_context() as session:
            stmt = select(ManagedAgentAuthProfile).where(
                ManagedAgentAuthProfile.runtime_id == runtime_id,
                ManagedAgentAuthProfile.enabled.is_(True),
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        profiles = []
        for row in rows:
            profiles.append(
                {
                    "profile_id": row.profile_id,
                    "runtime_id": row.runtime_id,
                    "auth_mode": row.auth_mode.value,
                    "volume_ref": row.volume_ref,
                    "volume_mount_path": row.volume_mount_path,
                    "account_label": row.account_label,
                    "api_key_ref": row.api_key_ref,
                    "runtime_env_overrides": row.runtime_env_overrides or {},
                    "api_key_env_var": row.api_key_env_var,
                    "max_parallel_runs": row.max_parallel_runs,
                    "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
                    "rate_limit_policy": row.rate_limit_policy.value,
                    "max_lease_duration_seconds": row.max_lease_duration_seconds,
                    "enabled": row.enabled,
                }
            )

        return {"profiles": profiles}

    async def auth_profile_ensure_manager(
        self,
        *,
        runtime_id: str,
    ) -> dict[str, Any]:
        """Ensure the AuthProfileManager workflow is running for *runtime_id*.

        Starts the singleton ``auth-profile-manager:<runtime_id>`` workflow if
        it is not already running.  Handles ``WorkflowAlreadyStartedError``
        gracefully so this activity is safe to call repeatedly.
        """
        from temporalio.exceptions import WorkflowAlreadyStartedError

        from moonmind.workflows.temporal.client import TemporalClientAdapter
        from moonmind.workflows.temporal.workflows.auth_profile_manager import (
            WORKFLOW_NAME as AUTH_PROFILE_MANAGER_WF,
            WORKFLOW_TASK_QUEUE as AUTH_PROFILE_MANAGER_QUEUE,
        )

        workflow_id = f"auth-profile-manager:{runtime_id}"
        adapter = TemporalClientAdapter()
        client = await adapter.get_client()

        try:
            await client.start_workflow(
                AUTH_PROFILE_MANAGER_WF,
                {"runtime_id": runtime_id},
                id=workflow_id,
                task_queue=AUTH_PROFILE_MANAGER_QUEUE,
            )
            logger.info(
                "auth_profile.ensure_manager started manager for runtime=%s",
                runtime_id,
            )
            return {"started": True, "workflow_id": workflow_id}
        except WorkflowAlreadyStartedError:
            logger.debug(
                "auth_profile.ensure_manager manager already running for runtime=%s",
                runtime_id,
            )
            return {"started": False, "workflow_id": workflow_id}

    async def auth_profile_verify_lease_holders(
        self,
        *,
        workflow_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Check whether each lease-holding workflow is still running.

        Uses the Temporal client to describe each workflow and determine if it
        is in a terminal state. This allows the AuthProfileManager to reclaim
        slots from cancelled/terminated workflows without waiting for the
        2-hour lease timeout.

        Returns a dict mapping workflow_id -> {"running": bool, "status": str}.
        Non-found workflows are counted as not running.
        """
        from temporalio.client import RPCError

        from moonmind.workflows.temporal.client import TemporalClientAdapter

        adapter = TemporalClientAdapter()
        client = await adapter.get_client()

        results: dict[str, dict[str, Any]] = {}
        for wf_id in workflow_ids:
            try:
                handle = client.get_workflow_handle(wf_id)
                desc = await handle.describe()
                status_name = desc.status.name
                results[wf_id] = {
                    "running": status_name == "RUNNING",
                    "status": status_name,
                }
            except RPCError as exc:
                # Workflow does not exist or is not reachable
                if exc.status.name == "NOT_FOUND":
                    results[wf_id] = {"running": False, "status": "NOT_FOUND"}
                else:
                    results[wf_id] = {"running": False, "status": f"RPC_ERROR_{exc.status.name}"}
            except Exception as exc:
                logger.warning(
                    "auth_profile.verify_lease_holders failed to describe %s: %s",
                    wf_id,
                    exc,
                )
                results[wf_id] = {"running": False, "status": "ERROR"}

        return results

    async def oauth_session_ensure_volume(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.ensure_volume`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_ensure_volume as _ensure_volume,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _ensure_volume(payload)

    async def oauth_session_start_auth_runner(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.start_auth_runner`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_start_auth_runner as _start_auth_runner,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _start_auth_runner(payload)

    async def oauth_session_stop_auth_runner(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.stop_auth_runner`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_stop_auth_runner as _stop_auth_runner,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _stop_auth_runner(payload)

    async def oauth_session_update_status(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.update_status`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_update_status as _update_status,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _update_status(payload)

    async def oauth_session_mark_failed(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.mark_failed`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_mark_failed as _mark_failed,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _mark_failed(payload)

    async def oauth_session_update_session_urls(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.update_session_urls`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_update_session_urls as _update_session_urls,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _update_session_urls(payload)

    async def oauth_session_verify_volume(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.verify_volume`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_verify_volume as _verify_volume,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _verify_volume(payload)

    async def oauth_session_register_profile(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.register_profile`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_register_profile as _register_profile,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _register_profile(payload)

    async def oauth_session_cleanup_stale(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate to standalone ``oauth_session.cleanup_stale`` activity."""
        from moonmind.workflows.temporal.activities.oauth_session_cleanup import (
            oauth_session_cleanup_stale as _cleanup_stale,
        )

        payload = request if isinstance(request, dict) else dict(kwargs)
        return await _cleanup_stale(payload)
