import hashlib
import json

import pytest

from services.omnigent.tools.init_omnigent_tools import _validate_completed


def _completed_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    executable = bundle / "bin" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o555)
    manifest = {
        "schemaVersion": 1,
        "bundleVersion": "test",
        "tools": [
            {
                "name": "tool",
                "version": "1",
                "platform": "linux/amd64",
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "path": "bin/tool",
                "versionProbe": [],
            }
        ],
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle, executable, manifest


def test_completed_bundle_rechecks_executable_digest(tmp_path) -> None:
    bundle, executable, manifest = _completed_bundle(tmp_path)
    executable.chmod(0o755)
    executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    executable.chmod(0o555)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        _validate_completed(bundle, manifest)


def test_completed_bundle_reports_missing_executable(tmp_path) -> None:
    bundle, executable, manifest = _completed_bundle(tmp_path)
    executable.unlink()

    with pytest.raises(RuntimeError, match="executable missing"):
        _validate_completed(bundle, manifest)
