"""Pydantic models for Temporal activity boundaries.

This module centralizes typed input/output models for Temporal activities,
ensuring strict boundaries and safe serialization of binary fields.

Phase 1 Policy Standards:
1. All new activity inputs should use Pydantic v2 models.
2. Activities should take a single structured argument (the input model) 
   to keep stubs and `execute_activity` call sites symmetrical.
3. Avoid raw `bytes` in dicts. Use `Base64Bytes` (or explicitly encode 
   to base64/utf-8 strings) so the JSON serializer does not accidentally 
   create a `list[int]`.
"""

from __future__ import annotations

import base64
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator


def _decode_b64(v: str | bytes | list[int]) -> bytes:
    """Safely decode base64 strings, raw bytes, or legacy list[int] to bytes.
    
    The list[int] fallback supports in-flight histories where the Temporal JSON
    serializer previously encoded raw bytes as an integer array.
    """
    if isinstance(v, bytes):
        return v
    if isinstance(v, list):
        return bytes(v)
    if isinstance(v, str):
        return base64.b64decode(v)
    raise ValueError(f"Expected base64 string, bytes, or list[int]; got {type(v)}")

def _encode_b64(b: bytes) -> str:
    """Encode bytes to base64 string for safe JSON serialization."""
    return base64.b64encode(b).decode("ascii")

# A specialized type for binary fields at the Temporal activity boundary.
# It guarantees that Pydantic will serialize the bytes to a base64 string,
# avoiding the 'accidental list of ints' issue with the default Temporal JSON codec.
Base64Bytes = Annotated[
    bytes,
    PlainValidator(_decode_b64),
    PlainSerializer(_encode_b64, return_type=str),
]


class ArtifactReadInput(BaseModel):
    """Input parameters for the artifact.read activity."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: str | dict[str, Any] = Field(
        ...,
        description="The artifact ID or an artifact reference dict/model.",
    )
    principal: str = Field(..., description="The principal requesting the read.")


class ArtifactReadOutput(BaseModel):
    """Output payload from the artifact.read activity."""

    model_config = ConfigDict(populate_by_name=True)

    payload: Base64Bytes = Field(
        ...,
        description="The binary payload returned as base64 on the wire.",
    )


class ArtifactWriteCompleteInput(BaseModel):
    """Input parameters for the artifact.write_complete activity."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str = Field(..., description="The ID of the artifact to complete.")
    principal: str = Field(..., description="The principal completing the write.")
    payload: Base64Bytes = Field(
        ...,
        description="The binary payload to write, serialized as base64 on the wire.",
    )
    content_type: str | None = Field(
        default=None,
        description="The optional MIME type of the content.",
    )
