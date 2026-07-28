import io
import json
import zipfile

import pytest

from api_service.services.omnigent_agent_bundle_service import (
    BundleValidationError,
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
