import enum

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
