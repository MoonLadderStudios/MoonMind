"""Unit tests for manifest queue contract helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.agent_queue.manifest_contract import (
    ManifestContractError,
    normalize_manifest_job_payload,
)

pytestmark = [pytest.mark.speckit]


def _tests_root() -> Path:
    path = Path(__file__).resolve()
    try:
        idx = path.parts.index("tests")
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("tests directory not found") from exc
    return Path(*path.parts[: idx + 1])


FIXTURE_ROOT = _tests_root() / "fixtures" / "manifests" / "phase0"
INLINE_MANIFEST = (FIXTURE_ROOT / "inline.yaml").read_text()


def _payload(source_kind: str = "inline", *, yaml: str = INLINE_MANIFEST) -> dict:
    source = {"kind": source_kind, "content": yaml}
    if source_kind == "registry":
        source["name"] = "demo-manifest"
    return {
        "manifest": {
            "name": "demo-manifest",
            "action": "run",
            "source": source,
            "options": {"dryRun": True, "maxDocs": 10},
        }
    }


def test_inline_manifest_normalization_derives_capabilities() -> None:
    """Inline manifests should include content and derived capability tokens."""

    normalized = normalize_manifest_job_payload(_payload())

    assert normalized["manifestVersion"] == "v0"
    assert normalized["manifestHash"].startswith("sha256:")
    assert normalized["manifest"]["source"]["kind"] == "inline"
    assert "content" in normalized["manifest"]["source"]
    assert normalized["manifest"]["options"] == {"dryRun": True, "maxDocs": 10}
    assert normalized["effectiveRunConfig"]["dryRun"] is True
    assert normalized["requiredCapabilities"] == [
        "manifest",
        "embeddings",
        "openai",
        "qdrant",
        "github",
    ]


def test_registry_manifest_drops_content_keeps_hash() -> None:
    """Registry submissions should omit inline content but retain audit hash."""

    normalized = normalize_manifest_job_payload(_payload(source_kind="registry"))

    source = normalized["manifest"]["source"]
    assert source["kind"] == "registry"
    assert "content" not in source
    assert source["contentHash"] == normalized["manifestHash"]
    assert source["name"] == "demo-manifest"


def test_manifest_name_mismatch_rejected() -> None:
    """Manifest name must match metadata.name."""

    bad_payload = _payload()
    bad_payload["manifest"]["name"] = "other"

    with pytest.raises(ManifestContractError, match="must match metadata.name"):
        normalize_manifest_job_payload(bad_payload)


def test_unknown_data_source_type_raises_error() -> None:
    """Capability derivation should fail for unsupported adapter types."""

    bad_yaml = INLINE_MANIFEST.replace("GithubRepositoryReader", "UnknownReader")
    with pytest.raises(
        ManifestContractError,
        match="unsupported data source type",
    ):
        normalize_manifest_job_payload(_payload(yaml=bad_yaml))


def test_manifest_options_reject_unsupported_keys() -> None:
    """manifest.options must only allow documented override keys."""

    payload = _payload()
    payload["manifest"]["options"] = {"dryRun": True, "forceFull": False, "bad": True}

    with pytest.raises(
        ManifestContractError,
        match=r"manifest\.options only supports",
    ):
        normalize_manifest_job_payload(payload)


def test_manifest_path_source_disabled_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path sources should be rejected when guard flag is disabled."""

    monkeypatch.setattr(settings.spec_workflow, "allow_manifest_path_source", False)
    payload = _payload(source_kind="path")
    payload["manifest"]["source"]["path"] = "/opt/manifests/demo.yaml"

    with pytest.raises(
        ManifestContractError,
        match="manifest.source.kind must be one of",
    ):
        normalize_manifest_job_payload(payload)


def test_manifest_path_source_preserves_path_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path sources should include the requested path when enabled."""

    monkeypatch.setattr(settings.spec_workflow, "allow_manifest_path_source", True)
    payload = _payload(source_kind="path")
    payload["manifest"]["source"]["path"] = "/opt/manifests/demo.yaml"

    normalized = normalize_manifest_job_payload(payload)
    source = normalized["manifest"]["source"]
    assert source["kind"] == "path"
    assert source["path"] == "/opt/manifests/demo.yaml"
    assert source["contentHash"] == normalized["manifestHash"]
    assert "content" in source


def test_manifest_secret_scanner_blocks_raw_tokens() -> None:
    """Secret detection should reject obvious access tokens."""

    secret_yaml = INLINE_MANIFEST + '\nauth:\n  apiKey: "sk_live_super_secret"\n'
    with pytest.raises(ManifestContractError, match="raw secret material"):
        normalize_manifest_job_payload(_payload(yaml=secret_yaml))


def test_manifest_secret_scanner_allows_vault_reference() -> None:
    """Secret detection should allow sanctioned vault references."""

    safe_yaml = INLINE_MANIFEST + (
        '\nauth:\n  apiKey: "vault://manifests/demo#api_key"\n'
        '  token: "${ENV_TOKEN}"\n'
    )
    normalized = normalize_manifest_job_payload(_payload(yaml=safe_yaml))
    assert normalized["manifestHash"].startswith("sha256:")


def test_manifest_secret_refs_capture_profile_and_vault() -> None:
    """Secret reference metadata should be collected in normalized payloads."""

    manifest_yaml = """
version: "v0"
metadata:
  name: "demo-manifest"
embeddings:
  provider: "openai"
vectorStore:
  type: "qdrant"
dataSources:
  - id: "repo-docs"
    type: "GithubRepositoryReader"
    params:
      owner: "moon"
      repo: "mind"
    auth:
      token: "profile://OpenAI#API_KEY"
      secondary: "vault://kv/manifests/demo#token"
"""
    normalized = normalize_manifest_job_payload(_payload(yaml=manifest_yaml))
    refs = normalized["manifestSecretRefs"]
    assert refs["profile"][0]["envKey"] == "OPENAI_API_KEY"
    assert refs["profile"][0]["normalized"] == "profile://openai#api_key"
    assert refs["vault"][0]["ref"] == "vault://kv/manifests/demo#token"


def test_manifest_capability_flags_extend_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured capability flags should be prepended to derived capability lists."""

    monkeypatch.setattr(
        settings.spec_workflow,
        "manifest_required_capabilities",
        ("manifest", "phase0", "beta"),
    )
    normalized = normalize_manifest_job_payload(_payload())

    assert normalized["requiredCapabilities"][:3] == ["manifest", "phase0", "beta"]
