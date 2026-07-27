from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.omnigent.agent_profiles import (
    OmnigentAgentProfileDocument,
    OmnigentAgentProfileVersion,
)


def _document(**overrides: object) -> OmnigentAgentProfileDocument:
    payload = {
        "displayName": "Codex review",
        "ownerRef": "user:operator",
        "endpointRef": "default",
        "bridgeMode": "proxy",
        "upstream": {
            "agentId": "agent-123",
            "agentName": "codex",
            "agentVersion": "2026.07",
        },
        "harness": "codex-native",
        "requiredCapabilities": ["session.start"],
        "executionProfileRef": "omnigent-codex@1",
        "allowedLaunchPolicyRefs": ["codex-static@1"],
        "defaultLaunchPolicyRef": "codex-static@1",
        "providerCompatibility": {"runtimes": ["codex_cli"]},
        "policyRef": "omnigent-default@1",
    }
    payload.update(overrides)
    return OmnigentAgentProfileDocument.model_validate(payload)


def test_profile_document_digest_is_normalized_and_stable() -> None:
    first = _document()
    second = OmnigentAgentProfileDocument.model_validate(
        dict(reversed(list(first.model_dump(by_alias=True).items())))
    )
    assert first.digest == second.digest
    assert first.digest.startswith("sha256:")


def test_profile_version_rejects_mutated_document() -> None:
    document = _document()
    with pytest.raises(ValidationError, match="documentDigest"):
        OmnigentAgentProfileVersion.model_validate(
            {
                "profileId": "codex-review",
                "version": 1,
                "document": document,
                "documentDigest": "sha256:" + "0" * 64,
                "state": "active",
                "createdBy": "user:operator",
                "createdAt": "2026-07-27T00:00:00Z",
            }
        )


@pytest.mark.parametrize(
    "override",
    [
        {"modelSettings": {"apiToken": "not-allowed"}},
        {"workspaceDefaults": {"bindSource": "/srv/repository"}},
    ],
)
def test_profile_document_rejects_secret_and_host_authority(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="authority"):
        _document(**override)


def test_upstream_display_name_is_not_a_stable_identity() -> None:
    with pytest.raises(ValidationError, match="versioned upstream agent"):
        _document(upstream={"agentName": "codex"})


def test_bundle_identity_requires_digest() -> None:
    with pytest.raises(ValidationError, match="versioned upstream agent"):
        _document(upstream={"bundleArtifactRef": "artifact:bundle"})

