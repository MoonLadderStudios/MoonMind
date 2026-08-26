import io
import json
import zipfile

import pytest

from api_service.services.omnigent_agent_bundle_service import (
    BundleValidationError,
    publish_validated_agent_bundle,
    validate_agent_bundle,
)


def bundle(files: dict[str, bytes], *, executable: set[str] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = ((0o100755 if name in (executable or set()) else 0o100644) << 16)
            archive.writestr(info, payload)
    return output.getvalue()


def manifest(**overrides) -> bytes:
    value = {
        "schemaVersion": "omnigent.agent-bundle.v1",
        "harness": "codex-native",
        "capabilities": ["session.start", "tools"],
        "license": "Apache-2.0",
        **overrides,
    }
    return json.dumps(value).encode()


def test_bundle_records_bounded_manifest_provenance():
    result = validate_agent_bundle(
        bundle({"omnigent-agent.json": manifest(), "README.md": b"safe"}),
        "application/zip",
    )
    assert result == {
        "schemaVersion": "omnigent.agent-bundle.v1",
        "harness": "codex-native",
        "capabilities": ["session.start", "tools"],
        "license": "Apache-2.0",
        "fileCount": 2,
        "expandedBytes": len(manifest()) + 4,
    }


@pytest.mark.parametrize(
    "files,match",
    [
        ({"../escape": b"x", "manifest.json": manifest()}, "unsafe bundle path"),
        ({"Dockerfile": b"FROM bad", "manifest.json": manifest()}, "host launch file"),
        ({"Dockerfile.prod": b"FROM bad", "manifest.json": manifest()}, "host launch file"),
        ({"Containerfile": b"FROM bad", "manifest.json": manifest()}, "host launch file"),
        ({"compose.yaml": b"services: {}", "manifest.json": manifest()}, "host launch file"),
        ({"run.sh": b"echo ok", "manifest.json": manifest()}, "executable files"),
        ({"token.txt": b"token=abcdefghijk", "manifest.json": manifest()}, "secret-like"),
        ({"manifest.json": manifest(privileged=True)}, "runtime authority"),
    ],
)
def test_bundle_rejects_unmanaged_or_sensitive_content(files, match):
    executable = {"run.sh"} if "run.sh" in files else set()
    with pytest.raises(BundleValidationError, match=match):
        validate_agent_bundle(bundle(files, executable=executable), "application/zip")


def test_bundle_requires_one_supported_manifest():
    with pytest.raises(BundleValidationError, match="exactly one"):
        validate_agent_bundle(bundle({"README.md": b"none"}), "application/zip")
    with pytest.raises(BundleValidationError, match="schemaVersion"):
        validate_agent_bundle(
            bundle({"manifest.json": manifest(schemaVersion="future")}),
            "application/zip",
        )


@pytest.mark.asyncio
async def test_publish_validated_bundle_returns_bounded_upstream_identity():
    payload = bundle({"manifest.json": manifest()})

    async def publish(**kwargs):
        assert kwargs["filename"] == "team-codex-v3.bundle"
        assert kwargs["content"] == payload
        return {
            "id": "agent-123",
            "name": "team-codex",
            "version": "v7",
            "ignored": {"credentials": "must-not-be-persisted"},
        }

    result = await publish_validated_agent_bundle(
        data=payload,
        content_type="application/zip",
        expected_digest="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
        filename="team-codex-v3.bundle",
        publish=publish,
    )

    assert result == {
        "schemaVersion": "moonmind.omnigent-agent-bundle-import.v1",
        "status": "succeeded",
        "upstreamAgent": {"id": "agent-123", "name": "team-codex", "version": "v7"},
    }


@pytest.mark.asyncio
async def test_publish_validated_bundle_rejects_digest_mismatch_before_side_effect():
    called = False

    async def publish(**kwargs):
        nonlocal called
        called = True
        return {}

    with pytest.raises(BundleValidationError, match="digest"):
        await publish_validated_agent_bundle(
            data=bundle({"manifest.json": manifest()}),
            content_type="application/zip",
            expected_digest="sha256:" + "0" * 64,
            filename="agent.zip",
            publish=publish,
        )

    assert called is False


@pytest.mark.asyncio
async def test_publish_validated_bundle_requires_stable_upstream_identity():
    payload = bundle({"manifest.json": manifest()})

    async def publish(**kwargs):
        return {"name": "display-name-is-not-identity"}

    with pytest.raises(BundleValidationError, match="stable agent id"):
        await publish_validated_agent_bundle(
            data=payload,
            content_type="application/zip",
            expected_digest="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
            filename="agent.zip",
            publish=publish,
        )
