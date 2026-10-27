"""Scanner authority is enforced beyond its CLI helper (GitHub issue #5040)."""

import pytest

from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import ContainerJobSpec, OwnerIdentity
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.unit.workflows.temporal.test_container_job_backend import _recording_runner
from tests.unit.workflows.temporal.test_container_job_evidence import _request


@pytest.mark.parametrize(
    "override",
    [
        {"workspaceReadOnly": False},
        {"networkMode": "bridge"},
        {"command": ["sh", "-c", "gitleaks dir ."]},
        {"entrypoint": ["sh"]},
        {"outputs": [{"name": "raw", "relativePath": "source.txt"}]},
        {
            "environment": [
                {"name": "GITLEAKS_CONFIG", "value": "/workspace/.gitleaks.toml"}
            ]
        },
        {
            "caches": [
                {"cacheRef": "other", "target": "/workspace/src", "readOnly": False}
            ]
        },
        {"timeoutSeconds": 9000},
    ],
)
def test_direct_scanner_spec_cannot_weaken_admission(override):
    scan = RepositoryScan(scanner="gitleaks", outputDirectory="artifacts/scan")
    spec = {
        **build_scanner_job(scan),
        "workspaceRef": {"kind": "sandbox", "workspaceId": "repo"},
        **override,
    }
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(spec)


@pytest.mark.asyncio
async def test_service_and_worker_reject_forged_model_before_side_effects(tmp_path):
    scan = RepositoryScan(scanner="gitleaks", outputDirectory="artifacts/scan")
    request = _request(tmp_path, image=None, **build_scanner_job(scan))
    request.request.spec.command = ["gitleaks", "dir", "."]
    # This exercises the admission owner directly without a schema reparse.
    service = ContainerJobService.__new__(ContainerJobService)
    with pytest.raises(ValueError, match="admitted invocation"):
        await service.submit(
            owner=OwnerIdentity(principalType="user", principalId="operator"),
            request=request.request,
        )
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    request.resolved_image_ref = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="admitted invocation"):
        await backend.create_container(request)
    assert not commands


@pytest.mark.asyncio
@pytest.mark.parametrize("volume", [None, "workspaces"])
async def test_worker_mounts_source_read_only_and_only_output_writable(
    tmp_path, volume
):
    scan = RepositoryScan(scanner="gitleaks", outputDirectory="artifacts/scan")
    request = _request(tmp_path, image=None, **build_scanner_job(scan))
    request.resolved_image_ref = "sha256:" + "a" * 64
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name=volume,
        command_runner=_recording_runner(commands),
    )
    await backend.create_container(request)
    argv = next(argv for argv in commands if argv[0] == "create")
    mounts = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"]
    assert next(m for m in mounts if "dst=/workspace" in m).endswith(",readonly")
    assert not next(m for m in mounts if "dst=/scan-output" in m).endswith(",readonly")
    assert "--read-only" in argv
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=1g,mode=1777"
    assert argv[-4:-1] == ("-I", "-m", "moonmind.security.repository_scanner_runner")


@pytest.mark.parametrize(
    "overrides",
    [
        {"scanner": "opengrep", "rules": []},
        {"scanner": "trivy", "trivyScanners": ["secret"]},
        {"scanner": "trivy", "trivyScanners": ["vuln"]},
        {"inputPath": "../other"},
        {"outputDirectory": "src/../output"},
        {
            "rules": [
                {
                    "id": "remote",
                    "path": "https://rules.example/rules",
                    "sha256": "a" * 64,
                    "license": "MIT",
                }
            ]
        },
    ],
)
def test_unadmitted_config_rules_and_scopes_are_refused(overrides):
    with pytest.raises(ValueError):
        RepositoryScan.model_validate(
            {"scanner": "gitleaks", "outputDirectory": "artifacts/scan", **overrides}
        )


@pytest.mark.asyncio
async def test_output_mount_cannot_make_existing_source_writable(tmp_path):
    scan = RepositoryScan(scanner="gitleaks", outputDirectory="src")
    request = _request(tmp_path, image=None, **build_scanner_job(scan))
    request.resolved_image_ref = "sha256:" + "a" * 64
    source = tmp_path / "art_workspace" / "src"
    source.mkdir()
    (source / "app.py").write_text("valuable source\n")
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    with pytest.raises(ValueError, match="empty"):
        await backend.create_container(request)
    assert (source / "app.py").read_text() == "valuable source\n"
    assert not any(argv[0] == "create" for argv in commands)
