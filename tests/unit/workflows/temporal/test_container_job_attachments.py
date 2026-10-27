"""Immutable Container Job inputs and job-owned outputs (MoonLadderStudios/MoonMind#5036)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
)
from moonmind.workflows.temporal import container_job_attachments as attachments
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"
SIBLING_ID = "container-job:fedcba9876543210fedcba9876543210"
SOURCE = {"name": "source", "workspacePath": "."}


def _spec(**overrides) -> dict:
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "run-1"},
        "command": ["scan"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
        "inputAttachments": [SOURCE],
        "outputs": [{"name": "report", "relativePath": "reports/report.json"}],
    }
    spec.update(overrides)
    return spec


def _request(tmp_path, job_id=JOB_ID, **overrides) -> ContainerJobActivityRequest:
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True, exist_ok=True)
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": "issue-5036",
                "source": {"source": "workflow", "workflowId": "mm:5036"},
                "spec": _spec(**overrides),
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": "sha256:" + "a" * 64,
        }
    )


def _runner(commands, *, container_present=False):
    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            if container_present:
                return (
                    0,
                    b'{"moonmind.ownership":"' + f"{JOB_ID}:v1".encode() + b'"}',
                    b"",
                )
            return 1, b"", b"no such container"
        if args[0] == "logs":
            return 0, b"done\n", b""
        return 0, b"", b""

    return runner


class _Publisher:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.payloads: dict[str, bytes] = {}
        self.fail_on = fail_on

    async def __call__(self, request, name, payload):
        if self.fail_on is not None and self.fail_on in name:
            raise RuntimeError("artifact store unavailable")
        self.calls.append(name)
        ref = f"art_{len(self.calls):04d}"
        self.payloads[ref] = payload
        return ref


def _backend(tmp_path, commands, **kwargs) -> DockerContainerJobBackend:
    kwargs.setdefault("command_runner", _runner(commands))
    return DockerContainerJobBackend(workspace_root=tmp_path, **kwargs)


def _mounts(commands) -> list[str]:
    create = next(c for c in commands if c[0] == "create")
    return [create[i + 1] for i, item in enumerate(create) if item == "--mount"]


def _workspace(request) -> Path:
    return Path(request.resolved_workspace_ref)


# ------------------------------------------------------------------ contract


@pytest.mark.parametrize(
    "attachment",
    [
        {"name": "source"},
        {"name": "source", "workspacePath": ".", "artifactRef": "art_1"},
        {"name": "source", "workspacePath": "/etc"},
        {"name": "source", "workspacePath": "src/../.."},
        {"name": "source", "workspacePath": "./src"},
        {"name": "../source", "workspacePath": "."},
        {"name": "image", "workspacePath": ".", "fileName": "image.tar"},
        {"name": "image", "artifactRef": "/var/lib/docker/image.tar"},
        {"name": "source", "workspacePath": ".", "hostPath": "/srv/repo"},
        {"name": "source", "workspacePath": ".", "volume": "agent_workspaces"},
        {"name": "source", "workspacePath": ".", "dockerHost": "tcp://host"},
    ],
)
def test_attachments_refuse_host_paths_volumes_and_ambiguous_sources(attachment):
    with pytest.raises(ValueError):
        ContainerJobSubmitRequest.model_validate(
            {
                "idempotencyKey": "k",
                "source": {"source": "http"},
                "spec": _spec(inputAttachments=[attachment]),
            }
        )


def test_attachment_names_are_unique_and_caches_stay_outside_attachments():
    with pytest.raises(ValueError, match="unique"):
        ContainerJobSpec.model_validate(_spec(inputAttachments=[SOURCE, SOURCE]))
    for target in ("/outputs", "/inputs/source", "/tmp/cache"):
        with pytest.raises(ValueError, match="overlay"):
            ContainerJobSpec.model_validate(
                _spec(caches=[{"cacheRef": "pip", "target": target}])
            )


def test_workspace_only_jobs_keep_their_serialized_shape():
    spec = _spec()
    spec.pop("inputAttachments")
    validated = ContainerJobSpec.model_validate(spec)
    dumped = validated.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "inputAttachments" not in dumped
    assert ContainerJobSpec.model_validate(dumped) == validated


# ------------------------------------------------------------- input capture


@pytest.mark.asyncio
@pytest.mark.parametrize("volume", [None, "agent_workspaces"])
async def test_launch_mounts_captured_input_read_only_and_owned_output(
    tmp_path, volume
):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    (_workspace(request) / "app.py").write_text("print('hi')\n")
    backend = _backend(tmp_path, commands, workspace_volume_name=volume)

    await backend.create_container(request)

    mounts = _mounts(commands)
    assert not any(
        "dst=/workspace" in m for m in mounts
    ), "live source is never mounted"
    source = next(m for m in mounts if "dst=/inputs/source" in m)
    output = next(m for m in mounts if "dst=/outputs" in m)
    scratch = next(m for m in mounts if "dst=/tmp" in m)
    assert source.endswith(",readonly")
    assert not output.endswith(",readonly") and not scratch.endswith(",readonly")
    if volume:
        assert source == (
            "type=volume,src=agent_workspaces,dst=/inputs/source,"
            "volume-subpath=.container-job-attachments/"
            "0123456789abcdef0123456789abcdef/inputs/source,readonly"
        )
        assert all(str(tmp_path) not in m for m in mounts)
    storage = attachments.storage_for(tmp_path, JOB_ID)
    assert (storage.inputs / "source" / "app.py").read_text() == "print('hi')\n"
    # Writable locations are siblings of the input, never inside it.
    assert not storage.outputs.is_relative_to(storage.inputs)


@pytest.mark.asyncio
async def test_upstream_edits_and_retries_never_change_the_captured_input(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    source = _workspace(request) / "app.py"
    source.write_text("v1")
    backend = _backend(tmp_path, commands)
    storage = attachments.storage_for(tmp_path, JOB_ID)

    await backend.create_container(request)
    identity = attachments.load_manifest(storage)["inputs"]["source"]["identity"]
    source.write_text("v2 concurrent edit")
    (_workspace(request) / "new.py").write_text("added later")
    # A retried launch after a lost acknowledgment reuses the admitted capture.
    await backend.create_container(request)

    captured = storage.inputs / "source"
    assert (captured / "app.py").read_text() == "v1"
    assert not (captured / "new.py").exists()
    assert (
        attachments.load_manifest(storage)["inputs"]["source"]["identity"] == identity
    )
    expected = json.dumps(
        [["app.py", hashlib.sha256(b"v1").hexdigest()]], separators=(",", ":")
    )
    assert identity == "sha256:" + hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.asyncio
async def test_interrupted_capture_is_captured_afresh(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    (_workspace(request) / "app.py").write_text("current")
    storage = attachments.storage_for(tmp_path, JOB_ID)
    attachments.claim(storage, request.ownership_token)
    (storage.staging / "source").mkdir(parents=True)
    (storage.staging / "source" / "partial.py").write_text("half")

    await _backend(tmp_path, commands).create_container(request)

    captured = storage.inputs / "source"
    assert sorted(p.name for p in captured.iterdir()) == ["app.py"]
    assert not storage.staging.exists()


@pytest.mark.asyncio
async def test_capture_omits_links_and_records_git_coverage(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(
        tmp_path,
        inputAttachments=[
            {"name": "source", "workspacePath": "."},
            {"name": "tree", "workspacePath": ".", "includeGit": False},
        ],
    )
    workspace = _workspace(request)
    secret = tmp_path / "host-secret"
    secret.write_text("host only")
    (workspace / "escape").symlink_to(secret)
    (workspace / "escape-dir").symlink_to(tmp_path)
    os.mkfifo(workspace / "pipe")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / ".git" / "shallow").write_text("abc\n")
    (workspace / "app.py").write_text("ok")

    await _backend(tmp_path, commands).create_container(request)

    storage = attachments.storage_for(tmp_path, JOB_ID)
    manifest = attachments.load_manifest(storage)["inputs"]
    source = storage.inputs / "source"
    assert sorted(p.name for p in source.iterdir()) == [".git", "app.py"]
    assert manifest["source"]["coverage"]["git"] == "directory"
    assert manifest["source"]["coverage"]["shallow"] is True
    assert manifest["source"]["coverage"]["symlinksOmitted"] == 2
    assert manifest["source"]["coverage"]["specialFilesOmitted"] == 1
    assert "shallow_git_history" in manifest["source"]["coverage"]["limitations"]
    assert not (storage.inputs / "tree" / ".git").exists()
    assert manifest["tree"]["coverage"]["limitations"] == [
        "git_history_excluded",
        "special_files_omitted",
        "symlinks_omitted",
    ]


@pytest.mark.asyncio
async def test_capture_beyond_its_bound_fails_closed(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path, inputAttachments=[{**SOURCE, "maxFiles": 1}])
    (_workspace(request) / "a").write_text("a")
    (_workspace(request) / "b").write_text("b")

    with pytest.raises(ContainerJobBackendError) as raised:
        await _backend(tmp_path, commands).create_container(request)

    assert raised.value.failure_class is ContainerJobFailureClass.WORKSPACE
    assert "admitted bound" in str(raised.value)
    assert not any(c[0] == "create" for c in commands)


@pytest.mark.asyncio
async def test_input_cannot_escape_the_admitted_workspace(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(
        tmp_path, inputAttachments=[{"name": "other", "workspacePath": "link"}]
    )
    (tmp_path / "other-run").mkdir()
    (_workspace(request) / "link").symlink_to(tmp_path / "other-run")

    with pytest.raises(ContainerJobBackendError, match="escapes"):
        await _backend(tmp_path, commands).create_container(request)


def _swap_before_open(monkeypatch, name: str, swap) -> None:
    """Replace ``name`` right after capture classified it, before it is opened."""

    real_open_at = attachments._open_at
    swapped = []

    def open_at(entry, flags, dir_fd):
        if entry == name and not swapped:
            swapped.append(entry)
            swap()
        return real_open_at(entry, flags, dir_fd)

    monkeypatch.setattr(attachments, "_open_at", open_at)


def _stored_bytes(root: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["config.txt", "pkg"])
async def test_entries_swapped_for_links_during_capture_never_copy_host_bytes(
    tmp_path, monkeypatch, entry
):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    workspace = _workspace(request)
    outside = tmp_path / "host"
    outside.mkdir()
    (outside / "config.txt").write_text("host only")
    (outside / "mod.py").write_text("host only")
    (workspace / "app.py").write_text("ok")
    (workspace / "config.txt").write_text("workspace config")
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "mod.py").write_text("workspace module")
    target = outside / "config.txt" if entry == "config.txt" else outside

    def swap():
        os.rename(workspace / entry, tmp_path / f"moved-{entry}")
        (workspace / entry).symlink_to(target)

    _swap_before_open(monkeypatch, entry, swap)

    await _backend(tmp_path, commands).create_container(request)

    storage = attachments.storage_for(tmp_path, JOB_ID)
    coverage = attachments.load_manifest(storage)["inputs"]["source"]["coverage"]
    assert b"host only" not in _stored_bytes(storage.root)
    captured = {p.name for p in (storage.inputs / "source").iterdir()}
    assert captured == {"app.py", "config.txt", "pkg"} - {entry}
    assert coverage["filesChangedDuringCapture"] == 1
    assert "files_changed_during_capture" in coverage["limitations"]


@pytest.mark.asyncio
async def test_entries_removed_during_capture_are_counted_not_fatal(
    tmp_path, monkeypatch
):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    workspace = _workspace(request)
    (workspace / "app.py").write_text("ok")
    (workspace / "deleted.py").write_text("gone")
    real_scandir = os.scandir

    class _Listing:
        def __init__(self, fd):
            self._scan = real_scandir(fd)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._scan.close()

        def __iter__(self):
            for entry in self._scan:
                if entry.name == "deleted.py":
                    os.unlink(workspace / "deleted.py")
                yield entry

    monkeypatch.setattr(attachments.os, "scandir", _Listing)

    await _backend(tmp_path, commands).create_container(request)

    storage = attachments.storage_for(tmp_path, JOB_ID)
    coverage = attachments.load_manifest(storage)["inputs"]["source"]["coverage"]
    assert [p.name for p in (storage.inputs / "source").iterdir()] == ["app.py"]
    assert coverage["filesChangedDuringCapture"] == 1


@pytest.mark.asyncio
async def test_workspace_path_swapped_for_a_link_fails_closed(tmp_path, monkeypatch):
    commands: list[tuple[str, ...]] = []
    request = _request(
        tmp_path, inputAttachments=[{"name": "source", "workspacePath": "src/app"}]
    )
    workspace = _workspace(request)
    (workspace / "src" / "app").mkdir(parents=True)
    (tmp_path / "other-run").mkdir()
    (tmp_path / "other-run" / "secret").write_text("host only")

    def swap():
        os.rename(workspace / "src", tmp_path / "moved-src")
        (workspace / "src").symlink_to(tmp_path / "other-run")

    _swap_before_open(monkeypatch, "src", swap)

    with pytest.raises(ContainerJobBackendError, match="escapes"):
        await _backend(tmp_path, commands).create_container(request)

    storage = attachments.storage_for(tmp_path, JOB_ID)
    assert b"host only" not in _stored_bytes(storage.root)
    assert not any(c[0] == "create" for c in commands)


@pytest.mark.asyncio
async def test_another_jobs_storage_is_never_reused(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    storage = attachments.storage_for(tmp_path, JOB_ID)
    attachments.claim(storage, "another-token")

    with pytest.raises(ContainerJobBackendError, match="another job"):
        await _backend(tmp_path, commands).create_container(request)


@pytest.mark.asyncio
async def test_artifact_inputs_use_owner_authority(tmp_path):
    commands: list[tuple[str, ...]] = []
    reads: list[tuple[str, str]] = []

    async def reader(request, artifact_ref):
        reads.append((request.owner.principal_id, artifact_ref))
        return b"image-archive"

    request = _request(
        tmp_path,
        inputAttachments=[
            {"name": "image", "artifactRef": "art_01IMAGE", "fileName": "image.tar"}
        ],
    )
    await _backend(tmp_path, commands, artifact_reader=reader).create_container(request)

    storage = attachments.storage_for(tmp_path, JOB_ID)
    assert (storage.inputs / "image" / "image.tar").read_bytes() == b"image-archive"
    assert reads == [("container_job", "art_01IMAGE")]
    assert attachments.load_manifest(storage)["inputs"]["image"]["artifactRef"] == (
        "art_01IMAGE"
    )

    async def denied(request, artifact_ref):
        raise PermissionError("not readable")

    other = _request(
        tmp_path,
        job_id=SIBLING_ID,
        inputAttachments=[{"name": "image", "artifactRef": "art_01IMAGE"}],
    )
    with pytest.raises(ContainerJobBackendError) as raised:
        await _backend(tmp_path, commands, artifact_reader=denied).create_container(
            other
        )
    assert raised.value.failure_class is ContainerJobFailureClass.AUTHORIZATION


@pytest.mark.asyncio
async def test_attachment_storage_is_never_a_workspace(tmp_path):
    commands: list[tuple[str, ...]] = []
    await _backend(tmp_path, commands).create_container(_request(tmp_path))
    request = _request(
        tmp_path,
        workspaceRef={
            "kind": "external_state",
            "artifactRef": ".container-job-attachments",
        },
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        await _backend(tmp_path, commands).resolve_workspace(request)


# --------------------------------------------------------- output collection


async def _launched(tmp_path, commands, **overrides):
    request = _request(tmp_path, **overrides)
    (_workspace(request) / "app.py").write_text("source")
    backend = _backend(tmp_path, commands)
    await backend.create_container(request)
    request.container_ref = DockerContainerJobBackend._name(request)
    request.terminal_state = "succeeded"
    return request, attachments.storage_for(tmp_path, JOB_ID)


@pytest.mark.asyncio
async def test_outputs_are_collected_from_the_owned_root_with_matching_hashes(
    tmp_path,
):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    (storage.outputs / "reports").mkdir()
    (storage.outputs / "reports" / "report.json").write_text('{"findings":[]}')
    # A same-named file in the live workspace is not the job's output.
    (_workspace(request) / "reports").mkdir()
    (_workspace(request) / "reports" / "report.json").write_text("forged")
    publisher = _Publisher()
    backend = _backend(tmp_path, commands, evidence_publisher=publisher)

    result = await backend.publish_evidence(request)

    page = json.loads(publisher.payloads[result.artifacts_ref])
    report = page["artifacts"][0]
    assert report["collectionStatus"] == "collected"
    published = publisher.payloads[report["artifactRef"]]
    assert published == b'{"findings":[]}'
    assert report["sha256"] == hashlib.sha256(published).hexdigest()
    diagnostics = json.loads(publisher.payloads[result.diagnostics_ref])
    captured = diagnostics["inputAttachments"]["source"]
    assert captured["identity"].startswith("sha256:") and captured["files"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "status", "detail"),
    [
        (lambda s: None, "missing", "not produced"),
        (
            lambda s: (
                (s.outputs / "reports").mkdir(),
                (s.outputs / "reports" / "report.json").symlink_to(
                    "../../inputs/source/app.py"
                ),
            ),
            "rejected",
            "escapes",
        ),
        (
            lambda s: (
                (s.outputs / "reports").mkdir(),
                os.mkfifo(s.outputs / "reports" / "report.json"),
            ),
            "rejected",
            "unsupported file type",
        ),
        (
            lambda s: (
                (s.outputs / "reports").mkdir(),
                (s.outputs / "reports" / "report.json").write_bytes(b"x" * 4096),
            ),
            "rejected",
            "ceiling",
        ),
    ],
)
async def test_owned_output_edge_cases_report_truthful_status(
    tmp_path, prepare, status, detail
):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    prepare(storage)
    publisher = _Publisher()
    backend = _backend(
        tmp_path,
        commands,
        evidence_publisher=publisher,
        settings=resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_TOTAL_BYTES": "1024"}
        ),
    )

    result = await backend.publish_evidence(request)

    report = json.loads(publisher.payloads[result.artifacts_ref])["artifacts"][0]
    assert report["collectionStatus"] == status
    assert detail in report["detail"]
    assert "artifactRef" not in report
    # Undeclared and input bytes are never published.
    assert b"source" not in b"".join(
        v for k, v in publisher.payloads.items() if k != result.diagnostics_ref
    )


@pytest.mark.asyncio
async def test_partial_publication_retry_reuses_confirmed_artifacts(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(
        tmp_path,
        commands,
        outputs=[
            {"name": "report", "relativePath": "report.json"},
            {"name": "sbom", "relativePath": "sbom.json"},
        ],
    )
    (storage.outputs / "report.json").write_text("report")
    (storage.outputs / "sbom.json").write_text("sbom")
    failing = _Publisher(fail_on="output-sbom")

    with pytest.raises(RuntimeError, match="unavailable"):
        await _backend(tmp_path, commands, evidence_publisher=failing).publish_evidence(
            request
        )
    confirmed = list(failing.calls)
    assert f"{JOB_ID}-output-report.json" in confirmed

    retry = _Publisher()
    retry.calls = list(confirmed)  # continue numbering like one store
    result = await _backend(
        tmp_path, commands, evidence_publisher=retry
    ).publish_evidence(request)

    new = retry.calls[len(confirmed) :]
    assert f"{JOB_ID}-output-report.json" not in new
    assert f"{JOB_ID}-output-sbom.json" in new
    page = json.loads(retry.payloads[result.artifacts_ref])
    assert [a["collectionStatus"] for a in page["artifacts"]] == ["collected"] * 2


# -------------------------------------------------------------------- cleanup


@pytest.mark.asyncio
async def test_cleanup_removes_storage_after_publication(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    sibling = attachments.storage_for(tmp_path, SIBLING_ID)
    attachments.claim(sibling, f"{SIBLING_ID}:v1")
    request.publication = AuxiliaryOutcome(state="succeeded")

    result = await _backend(tmp_path, commands).cleanup(request)

    assert result.cleanup_succeeded is True
    assert not storage.root.exists()
    assert sibling.root.is_dir(), "another job's attachments are never removed"


@pytest.mark.asyncio
async def test_cleanup_retains_unpublished_outputs(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    (storage.outputs / "report.json").write_text("only copy")
    request.publication = AuxiliaryOutcome(state="failed")

    result = await _backend(tmp_path, commands).cleanup(request)

    assert result.cleanup_succeeded is False
    assert (storage.outputs / "report.json").read_text() == "only copy"
    assert not storage.inputs.exists() and not storage.scratch.exists()


@pytest.mark.asyncio
async def test_cleanup_keeps_storage_while_the_container_may_use_it(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    request.publication = AuxiliaryOutcome(state="succeeded")
    backend = _backend(
        tmp_path, commands, command_runner=_runner(commands, container_present=True)
    )

    result = await backend.cleanup(request)

    assert result.cleanup_succeeded is False
    assert (storage.inputs / "source" / "app.py").exists()


@pytest.mark.asyncio
async def test_interrupted_cleanup_completes_on_retry(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(tmp_path, commands)
    request.publication = AuxiliaryOutcome(state="succeeded")
    # A previous attempt removed inputs and scratch before it was interrupted.
    attachments.release(
        storage, ownership_token=request.ownership_token, retain_outputs=True
    )

    result = await _backend(tmp_path, commands).cleanup(request)

    assert result.cleanup_succeeded is True
    assert not storage.root.exists()
    # Cleanup of a job that never captured inputs is a no-op success.
    assert (await _backend(tmp_path, commands).cleanup(request)).cleanup_succeeded


@pytest.mark.asyncio
async def test_cleanup_refuses_storage_claimed_by_another_job(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    storage = attachments.storage_for(tmp_path, JOB_ID)
    attachments.claim(storage, "another-token")
    request.publication = AuxiliaryOutcome(state="succeeded")

    result = await _backend(tmp_path, commands).cleanup(request)

    assert result.cleanup_succeeded is False
    assert storage.root.is_dir()


@pytest.mark.asyncio
async def test_canceled_job_publishes_partial_outputs_truthfully(tmp_path):
    commands: list[tuple[str, ...]] = []
    request, storage = await _launched(
        tmp_path,
        commands,
        outputs=[
            {"name": "partial", "relativePath": "partial.json"},
            {"name": "final", "relativePath": "final.json"},
        ],
    )
    (storage.outputs / "partial.json").write_text("first findings")
    request.terminal_state = "canceled"
    request.failure_class = "canceled"
    publisher = _Publisher()

    result = await _backend(
        tmp_path, commands, evidence_publisher=publisher
    ).publish_evidence(request)
    request.publication = AuxiliaryOutcome(state="succeeded")
    cleaned = await _backend(tmp_path, commands).cleanup(request)

    page = json.loads(publisher.payloads[result.artifacts_ref])
    status = {a["name"]: a["collectionStatus"] for a in page["artifacts"]}
    assert status == {"partial": "collected", "final": "missing"}
    partial = next(a for a in page["artifacts"] if a["name"] == "partial")
    assert publisher.payloads[partial["artifactRef"]] == b"first findings"
    assert cleaned.cleanup_succeeded is True and not storage.root.exists()


@pytest.mark.asyncio
async def test_job_canceled_before_capture_needs_no_storage(tmp_path):
    commands: list[tuple[str, ...]] = []
    request = _request(tmp_path)
    request.terminal_state = "canceled"
    publisher = _Publisher()

    result = await _backend(
        tmp_path, commands, evidence_publisher=publisher
    ).publish_evidence(request)
    request.publication = AuxiliaryOutcome(state="succeeded")
    cleaned = await _backend(tmp_path, commands).cleanup(request)

    page = json.loads(publisher.payloads[result.artifacts_ref])
    assert page["artifacts"][0]["collectionStatus"] == "missing"
    diagnostics = json.loads(publisher.payloads[result.diagnostics_ref])
    assert diagnostics["inputAttachments"] is None
    assert cleaned.cleanup_succeeded is True
    assert not attachments.storage_for(tmp_path, JOB_ID).root.exists()
