
import pytest
from pydantic import BaseModel, ValidationError

from moonmind.schemas.temporal_activity_models import (
    ArtifactReadInput,
    ArtifactReadOutput,
    ArtifactWriteCompleteInput,
    Base64Bytes,
)
from moonmind.schemas.temporal_artifact_models import ArtifactRefModel


class DummyModel(BaseModel):
    data: Base64Bytes


def test_base64bytes_serialization():
    model = DummyModel(data=b"hello test")
    dump = model.model_dump()
    assert dump["data"] == "aGVsbG8gdGVzdA=="


def test_base64bytes_deserialization():
    model = DummyModel.model_validate({"data": "aGVsbG8gdGVzdA=="})
    assert model.data == b"hello test"


def test_base64bytes_legacy_list_int():
    model = DummyModel.model_validate({"data": [97, 98, 99]})
    assert model.data == b"abc"
    assert model.model_dump()["data"] == "YWJj"


def test_base64bytes_legacy_list_int_invalid():
    with pytest.raises(ValidationError) as exc_info:
        DummyModel.model_validate({"data": [97, 256, 99]})
    assert "Expected list[int] with values in range 0-255" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        DummyModel.model_validate({"data": [97, "x", 99]})
    assert "Expected list[int] with values in range 0-255" in str(exc_info.value)


def test_base64bytes_legacy_utf8_string():
    # If the string is not valid base64 (e.g. contains `{`), it falls back to UTF-8
    payload = '{"some": "json-string"}'
    model = DummyModel.model_validate({"data": payload})
    assert model.data == payload.encode("utf-8")


def test_artifact_read_input_accepts_model():
    ref = ArtifactRefModel(
        artifact_ref_v=1,
        artifact_id="test-id",
        encryption="NONE"
    )
    model = ArtifactReadInput(
        artifact_ref=ref,
        principal="user1"
    )
    assert model.artifact_ref == ref

def test_artifact_write_complete_input():
    model = ArtifactWriteCompleteInput(
        artifact_id="test",
        principal="user1",
        payload=b"test payload"
    )
    assert model.payload == b"test payload"
    dump = model.model_dump()
    assert dump["payload"] == "dGVzdCBwYXlsb2Fk"

def test_artifact_read_output():
    model = ArtifactReadOutput(
        payload=b"test payload"
    )
    assert model.payload == b"test payload"
    dump = model.model_dump()
    assert dump["payload"] == "dGVzdCBwYXlsb2Fk"
