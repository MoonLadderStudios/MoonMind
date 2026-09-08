"""Docker documentation examples and their production authority boundaries.

The YAML contract is machine-readable; surrounding prose is freely editable.
Launch, authorization, image reuse, and cleanup behavior also have dedicated
tests in services/test_container_jobs.py and workflows/temporal/test_container_*.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobSpec
from tools.check_documentation_architecture import metadata_fields

ROOT = Path(__file__).resolve().parents[3]
BACKEND_DOC = ROOT / "docs/ManagedAgents/DockerBackendService.md"


def _contract(text: str) -> dict:
    blocks = [
        yaml.safe_load(block)
        for block in re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    ]
    contracts = [
        block["dockerBackendService"]
        for block in blocks
        if isinstance(block, dict) and "owner" in block.get("dockerBackendService", {})
    ]
    assert len(contracts) == 1
    return contracts[0]


def _assert_authority(contract: dict) -> None:
    assert contract["owner"] == "moonmind-api"
    assert contract["durability"] == "temporal"
    assert contract["tools"]["rawDockerCliExposedToAgents"] is False
    assert contract["tools"]["arbitraryBuildExposedToAgents"] is False
    assert contract["backend"]["selectedBy"] == "deployment"
    assert contract["backend"]["dedicatedMoonMindDaemonRequiredNow"] is False
    assert contract["workspaces"]["callerProvidesHostPath"] is False
    assert contract["workspaces"]["visibilityProbeBeforeProvisioning"] is True
    for key in (
        "dockerSocketInAgent",
        "dockerHostInAgent",
        "privilegedJobs",
        "arbitraryHostMounts",
        "arbitraryBuildContextFromCaller",
    ):
        assert contract["security"][key] is False


def test_documented_authority_and_backend_defaults() -> None:
    contract = _contract(BACKEND_DOC.read_text(encoding="utf-8"))
    _assert_authority(contract)
    settings = resolve_container_backend_settings({})
    assert contract["backend"]["currentKind"] == settings.kind == "docker-engine"
    assert contract["tools"]["rawDockerCliExposedToAgents"] == settings.raw_cli_enabled
    assert set(contract["tools"]["asynchronous"]) == {
        "container.submit",
        "container.status",
        "container.logs",
        "container.artifacts",
        "container.cancel",
    }
    assert {"omnigent-session", "moonmind-managed-session"} <= set(contract["callers"])


def test_documented_image_lifecycle_is_independent_of_jobs() -> None:
    contract = _contract(BACKEND_DOC.read_text(encoding="utf-8"))
    assert contract["startup"]["provisionsOptionalImages"] is False
    assert contract["images"] == {
        "registryAcquisition": "on-demand",
        "localRecipeAcquisition": "on-demand",
        "defaultPullPolicy": "if-missing",
        "localFreshness": "build-key-and-optional-max-age",
        "provisioningLock": "per-source-and-desired-key",
        "cacheScope": "selected-daemon",
        "reusableAcrossWorkflows": True,
        "removeOnJobEnd": False,
    }
    assert contract["cleanup"]["images"] == "deployment-retention"


def test_cosmetic_rewording_does_not_change_contract() -> None:
    text = BACKEND_DOC.read_text(encoding="utf-8")
    rewritten = re.sub(
        r"\A.*?(?=```yaml\ndockerBackendService:)",
        "# Container execution\n\n",
        text,
        flags=re.DOTALL,
    )
    assert _contract(rewritten) == _contract(text)
    _assert_authority(_contract(rewritten))


@pytest.mark.parametrize(
    "section,key",
    [
        ("tools", "rawDockerCliExposedToAgents"),
        ("tools", "arbitraryBuildExposedToAgents"),
        ("backend", "dedicatedMoonMindDaemonRequiredNow"),
        ("workspaces", "callerProvidesHostPath"),
        ("workspaces", "visibilityProbeBeforeProvisioning"),
        ("security", "dockerSocketInAgent"),
        ("security", "dockerHostInAgent"),
        ("security", "privilegedJobs"),
        ("security", "arbitraryHostMounts"),
        ("security", "arbitraryBuildContextFromCaller"),
    ],
)
def test_reversing_a_documented_security_decision_is_detected(section, key) -> None:
    contract = copy.deepcopy(_contract(BACKEND_DOC.read_text(encoding="utf-8")))
    contract[section][key] = not contract[section][key]
    with pytest.raises(AssertionError):
        _assert_authority(contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dockerHost", "tcp://caller:2375"),
        ("hostPath", "/host"),
        ("privileged", True),
        ("dockerfile", "FROM arbitrary"),
    ],
)
def test_public_job_schema_rejects_caller_daemon_authority(field, value) -> None:
    # Prove the real admission schema rejects forbidden input independently
    # of documentation. Validate the positive control before each mutation.
    payload = {
        "image": "python:3.12",
        "command": ["python", "--version"],
        "workspaceRef": {
            "kind": "sandbox",
            "workspaceId": "docs-test",
            "relativePath": "repo",
        },
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
    }
    ContainerJobSpec.model_validate(payload)
    with pytest.raises(ValidationError):
        ContainerJobSpec.model_validate({**payload, field: value})


@pytest.mark.parametrize(
    "filename,status",
    [
        ("DockerSidecarRuntime.md", "Removed from desired state"),
        ("DockerOutOfDocker.md", "Consolidated into"),
    ],
)
def test_tombstones_retain_status_and_canonical_replacement(filename, status) -> None:
    text = (BACKEND_DOC.parent / filename).read_text(encoding="utf-8")
    assert metadata_fields(text)["status"].startswith(status)
    assert "(./DockerBackendService.md)" in text


def test_canonical_backend_doc_has_no_migration_or_compatibility_checklist() -> None:
    # These are prohibited architecture claims, not incidental positive prose.
    text = BACKEND_DOC.read_text(encoding="utf-8").lower()
    for forbidden in (
        "## migration",
        "migration from per-session",
        "temporary compatibility",
        "while callers migrate",
    ):
        assert forbidden not in text


def test_related_architecture_docs_do_not_reinstate_sidecar_as_default() -> None:
    forbidden = (
        "per-session Docker sidecar",
        "ordinary managed-session Docker work uses a per-session sidecar",
        "ordinary repository test workloads use the sidecar",
        "default way to provide it is the per-session Docker sidecar",
    )
    for relative in (
        "ManagedAgents/ManagedAgentArchitecture.md",
        "ManagedAgents/CodexCliManagedSessions.md",
        "MoonMindArchitecture.md",
    ):
        text = (ROOT / "docs" / relative).read_text(encoding="utf-8")
        assert "DockerBackendService.md" in text
        for phrase in forbidden:
            assert phrase.lower() not in text.lower()
