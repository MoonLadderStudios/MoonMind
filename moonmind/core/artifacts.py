import enum
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

class TemporalArtifactStorageBackend(str, enum.Enum):
    """Supported backing stores for Temporal artifact bytes."""
    S3 = "s3"
    LOCAL_FS = "local_fs"

class TemporalArtifactEncryption(str, enum.Enum):
    """Encryption mode metadata recorded for each artifact."""
    SSE_KMS = "sse-kms"
    SSE_S3 = "sse-s3"
    NONE = "none"
    ENVELOPE = "envelope"

class TemporalArtifactStatus(str, enum.Enum):
    """Lifecycle status for immutable Temporal artifacts."""
    PENDING_UPLOAD = "pending_upload"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETED = "deleted"

class TemporalArtifactRetentionClass(str, enum.Enum):
    """Retention policy classes for Temporal artifact lifecycle management."""
    EPHEMERAL = "ephemeral"
    STANDARD = "standard"
    LONG = "long"
    PINNED = "pinned"

class TemporalArtifactRedactionLevel(str, enum.Enum):
    """Sensitivity/redaction classification for artifact reads."""
    NONE = "none"
    PREVIEW_ONLY = "preview_only"
    RESTRICTED = "restricted"

class TemporalArtifactUploadMode(str, enum.Enum):
    """Upload mode selected when creating an artifact session."""
    SINGLE_PUT = "single_put"
    MULTIPART = "multipart"


_MODEL_PROVENANCE_KEY_TERMS = frozenset({"model", "provider"})


def assert_model_agnostic_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    field_name: str = "metadata",
) -> None:
    """Reject model/provider identity keys in provenance-like metadata."""

    if not metadata:
        return
    for path, _value in _walk_metadata(metadata):
        key = path[-1]
        normalized = "".join(
            character
            for character in str(key).lower()
            if character.isalnum()
        )
        if any(term in normalized for term in _MODEL_PROVENANCE_KEY_TERMS):
            dotted_path = ".".join(str(part) for part in path)
            raise ValueError(
                f"{field_name} must be model-agnostic; "
                f"model/provider provenance key {dotted_path!r} is not allowed"
            )


def _walk_metadata(
    value: Any,
    *,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = (*prefix, str(key))
            yield path, child
            yield from _walk_metadata(child, prefix=path)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, child in enumerate(value):
            path = (*prefix, str(index))
            yield path, child
            yield from _walk_metadata(child, prefix=path)
