"""Resolve an explicitly authored local source through its existing owners.

Path containment is not permission. No Git/source bytes are opened until the
same-run ledger, immutable manifest and cleaned runtime owner agree.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from sqlalchemy import select

from api_service.db.models import (
    OmnigentHostLeaseRecordV2,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.temporal_models import (
    StepExecutionManifestModel,
    StepLedgerSnapshotModel,
)
from moonmind.workflows.temporal.runtime.initial_repository_source import (
    LocalRepositorySourceUnavailable,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
)

# Resolve from the worker process, never an authored Profile or source setting.
_WORKER_GIT_EXECUTABLE = shutil.which("git")


async def resolve_source_authority(
    *,
    request,
    intent,
    workspace_root,
    temporal_client,
    artifact_service,
    session,
    access,
):
    """Return exact owner evidence, or None for a source not safely admitted.

    Transport/storage failures propagate: they are not evidence of unsupported
    input and must not silently change a recorded shared execution's route.
    """
    identity = request.step_execution
    if identity is None or any(
        intent.get(key) != value
        for key, value in {
            "workflowId": identity.workflow_id,
            "runId": identity.run_id,
            "logicalStepId": identity.logical_step_id,
        }.items()
    ):
        return None
    root = Path(workspace_root).absolute()
    source = Path(str(intent.get("sourcePath") or ""))
    try:
        parts = source.relative_to(root / "temporal_sandbox").parts
    except ValueError:
        return None
    if (
        len(parts) != 2
        or not re.fullmatch(r"[a-f0-9]{24}", parts[0])
        or parts[1] != "repo"
    ):
        return None
    store = SandboxWorkspaceRecordStore(root)
    record = store.load(parts[0])
    if (
        record is None
        or record.workflow_id != identity.workflow_id
        or record.relative_path != "repo"
    ):
        return None
    if (
        record.workspace_id
        != hashlib.sha256(
            f"{record.workflow_id}:{record.step_execution_id}".encode()
        ).hexdigest()[:24]
    ):
        return None
    rows = (
        (
            await session.execute(
                select(OmnigentRuntimeBindingRecord).where(
                    OmnigentRuntimeBindingRecord.phase_results_json["workspace"][
                        "stepExecution"
                    ]["stepExecutionId"].as_string()
                    == record.step_execution_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != 1:
        return None
    row = rows[0]
    binding = DbRuntimeBindingStore._from_row(row)
    if binding.state is not RuntimeBindingState.cleaned or not binding.hostLeaseRef:
        return None
    saved = AgentExecutionRequest.model_validate(binding.phaseResults["workspace"])
    step = saved.step_execution
    if (
        step is None
        or (step.workflow_id, step.run_id) != (identity.workflow_id, identity.run_id)
        or step.logical_step_id == identity.logical_step_id
    ):
        return None
    client = (
        await temporal_client.get_client()
        if hasattr(temporal_client, "get_client")
        else temporal_client
    )
    handle = client.get_workflow_handle(identity.workflow_id, run_id=identity.run_id)
    ledger = StepLedgerSnapshotModel.model_validate(
        await handle.query("get_step_ledger")
    )
    if (ledger.workflow_id, ledger.run_id) != (identity.workflow_id, identity.run_id):
        return None
    matched = None
    for row in ledger.steps:
        if row.status != "completed" or row.logical_step_id != step.logical_step_id:
            continue
        refs = row.refs
        manifest_ref = refs.latest_step_execution_manifest_ref if refs else None
        if not manifest_ref:
            continue
        artifact_id = (
            str(manifest_ref)
            .removeprefix("artifact:")
            .removeprefix("//")
            .split("#", 1)[0]
        )
        _artifact, body = await artifact_service.read(
            artifact_id=artifact_id, access=access
        )
        manifest = StepExecutionManifestModel.model_validate(json.loads(body))
        if (
            manifest.workflow_id == identity.workflow_id
            and manifest.run_id == identity.run_id
            and manifest.logical_step_id == row.logical_step_id
            and manifest.step_execution_id == record.step_execution_id
            and manifest.status == "succeeded"
        ):
            if matched is not None:
                return None
            matched = (
                manifest,
                str(manifest_ref),
                "sha256:" + hashlib.sha256(body).hexdigest(),
            )
    if matched is None:
        return None
    manifest, manifest_ref, manifest_digest = matched
    if step is None or (
        step.workflow_id,
        step.run_id,
        step.logical_step_id,
        step.execution_ordinal,
        step.step_execution_id,
    ) != (
        manifest.workflow_id,
        manifest.run_id,
        manifest.logical_step_id,
        manifest.execution_ordinal,
        manifest.step_execution_id,
    ):
        return None
    if saved.workspace_spec.get("workspaceLocator") != {
        "kind": "sandbox",
        "workspaceId": record.workspace_id,
        "relativePath": "repo",
    }:
        return None
    host = await session.get(OmnigentHostLeaseRecordV2, binding.hostLeaseRef)
    if (
        host is None
        or host.status != "cleaned"
        or host.runtime_binding_id != binding.bindingId
    ):
        return None
    return {
        "workflowId": manifest.workflow_id,
        "runId": manifest.run_id,
        "logicalStepId": manifest.logical_step_id,
        "executionOrdinal": manifest.execution_ordinal,
        "stepExecutionId": manifest.step_execution_id,
        "workspaceId": record.workspace_id,
        "sourcePath": str(source),
        "manifestRef": manifest_ref,
        "manifestDigest": manifest_digest,
        "runtimeBindingRef": binding.bindingId,
        "runtimeBindingSnapshotRef": binding.latestSnapshotRef,
        "hostLeaseRef": binding.hostLeaseRef,
        "hostLeaseGeneration": host.generation,
    }


def _source_git_layout(source):
    """Refuse configuration that gives Git authority outside the owned source."""
    import configparser
    import os
    import stat

    from moonmind.schemas.saved_work_models import SAVED_WORK_FORMAT_SIZE_LIMITS

    for parent in (source, *source.parents):
        if parent.is_symlink():
            raise LocalRepositorySourceUnavailable(
                "local source has a symbolic path component"
            )
    git_dir = source / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise LocalRepositorySourceUnavailable(
            "local source requires an ordinary private Git directory"
        )
    for forbidden in (
        "objects/info/alternates",
        "objects/info/http-alternates",
        "shallow",
        "commondir",
        "info/grafts",
        "refs/replace",
    ):
        if (git_dir / forbidden).exists() or (git_dir / forbidden).is_symlink():
            raise LocalRepositorySourceUnavailable(
                "local source has external or rewritten Git object authority"
            )
    count = total = 0
    for directory, dirs, files in os.walk(git_dir, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise LocalRepositorySourceUnavailable(
                    "local source Git metadata has an unsafe file type"
                )
            count += 1
            total += info.st_size
            if (
                count > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]
                or total > SAVED_WORK_FORMAT_SIZE_LIMITS["max_total_bytes"]
            ):
                raise ValueError("local source Git metadata exceeds export bounds")
    config = configparser.RawConfigParser(strict=True)
    try:
        config.read_string((git_dir / "config").read_text())
    except (configparser.Error, UnicodeError):
        # Config diagnostics may contain credential-bearing remote URLs.
        raise LocalRepositorySourceUnavailable(
            "local source Git configuration is not safely parseable"
        ) from None
    for section in config.sections():
        allowed = {
            "core": {
                "repositoryformatversion",
                "filemode",
                "bare",
                "logallrefupdates",
                "ignorecase",
                "precomposeunicode",
            },
            "user": {"name", "email"},
        }.get(section)
        if re.fullmatch(r'remote "[^"\r\n]+"', section):
            allowed = {"url", "fetch"}
        if re.fullmatch(r'branch "[^"\r\n]+"', section):
            allowed = {"remote", "merge"}
        if allowed is None or not set(config[section]).issubset(allowed):
            raise LocalRepositorySourceUnavailable(
                "local source contains unsupported Git execution configuration"
            )
    if config.get("core", "bare", fallback="false").lower() != "false":
        raise LocalRepositorySourceUnavailable(
            "local source must be a non-bare worktree"
        )


def _capture_source_bytes(source, intent):
    """Read only committed objects through a fresh credential-free Git clone."""
    import os
    import tempfile
    import time
    import shlex

    from moonmind.schemas.saved_work_models import (
        SAVED_WORK_FORMAT_SIZE_LIMITS,
        saved_work_path_exclusion,
    )
    from moonmind.workflows.temporal.runtime.checkpoint_history import (
        _git,
        capture_git_history,
    )
    from moonmind.workflows.temporal.runtime.workspace_archive import (
        build_workspace_archive,
    )

    if not _WORKER_GIT_EXECUTABLE:
        raise RuntimeError("worker Git executable is unavailable")
    _source_git_layout(source)
    branch = str(intent.get("branch") or "")
    target_branch = intent.get("targetBranch")
    if not branch or branch.startswith("-"):
        raise LocalRepositorySourceUnavailable(
            "local source requires an explicit branch"
        )
    with tempfile.TemporaryDirectory(prefix="moonmind-initial-source-") as temporary:
        # The worker's configured temporary root can itself be a platform
        # alias (for example, /var on macOS). Archive containment and Git's
        # exact safe.directory check both compare against canonical paths.
        private = Path(temporary).resolve()
        env = {
            "PATH": os.defpath,
            "HOME": str(private),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        deadline = time.monotonic() + 120

        def git(path, args, **kwargs):
            return _git(
                path,
                args,
                env=env,
                deadline=deadline,
                executable=_WORKER_GIT_EXECUTABLE,
                **kwargs,
            )

        git(private, ["check-ref-format", "--branch", branch])
        if git(source, ["for-each-ref", "--count=1", "refs/replace"]):
            raise LocalRepositorySourceUnavailable(
                "local source requires replacement object semantics"
            )
        destination = private / "clone"
        git(
            private,
            [
                "-c",
                "core.hooksPath=" + os.devnull,
                "clone",
                "--no-local",
                "--no-checkout",
                # Git clears parent configuration before invoking upload-pack.
                # Quote every fixed executable/validated source argument rather
                # than granting wildcard ownership trust or ambient shell input.
                "--upload-pack="
                + shlex.join(
                    [
                        _WORKER_GIT_EXECUTABLE,
                        "-c",
                        f"safe.directory={source / '.git'}",
                        "upload-pack",
                    ]
                ),
                "--template=",
                "--branch",
                branch,
                "--single-branch",
                "--",
                str(source),
                str(destination),
            ],
        )
        if git(destination, ["for-each-ref", "--count=1", "refs/tags"]):
            raise LocalRepositorySourceUnavailable(
                "local source requires Git tag preservation"
            )
        tip = git(destination, ["rev-parse", "HEAD"]).decode().strip()
        pin = intent.get("commitSha")
        if pin is not None:
            if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{40}", pin):
                raise LocalRepositorySourceUnavailable(
                    "local source pin must be a full commit identity"
                )
            if pin != tip:
                exact = git(
                    destination, ["rev-parse", "--quiet", "--verify", pin + "^{commit}"],
                    allow_unrelated=True,
                ).decode().strip()
                if exact != pin or git(
                    destination, ["merge-base", pin, tip], allow_unrelated=True
                ).decode().strip() != pin:
                    raise LocalRepositorySourceUnavailable(
                        "local source pin is not an ancestor of the selected branch"
                    )
        head = pin or tip
        tree = git(destination, ["ls-tree", "-r", "-l", "-z", head])
        members, total = set(), 0
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, _oid, size = metadata.split()
            name = raw_path.decode("utf-8")
            if (
                kind != b"blob"
                or mode not in {b"100644", b"100755"}
                or name == ".lfsconfig"
                or saved_work_path_exclusion(name)
            ):
                raise LocalRepositorySourceUnavailable(
                    "local source tree needs unsupported file or excluded-path semantics"
                )
            count = int(size)
            total += count
            if (
                count > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_bytes"]
                or total > SAVED_WORK_FORMAT_SIZE_LIMITS["max_total_bytes"]
            ):
                raise ValueError("local source committed tree exceeds export bounds")
            members.add(name)
            if len(members) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
                raise ValueError("local source committed tree exceeds path bounds")
        if target_branch is not None:
            if not isinstance(target_branch, str):
                raise LocalRepositorySourceUnavailable("local source target branch is invalid")
            target_ref = "refs/heads/" + target_branch
            if (
                pin is not None
                or not target_branch
                or len(target_branch) > 400
                or target_branch == "HEAD"
                or target_branch.startswith(("-", "origin/", "refs/"))
                or target_branch == branch
                or target_branch.startswith(branch + "/")
                or branch.startswith(target_branch + "/")
                or any(
                    name == target_branch or name.startswith(target_branch + "/")
                    for name in members
                )
                or git(
                    destination,
                    ["check-ref-format", "--normalize", target_ref],
                    allow_unrelated=True,
                ).decode().strip() != target_ref
                or git(
                    destination,
                    ["rev-parse", "--quiet", "--verify", target_branch],
                    allow_unrelated=True,
                )
            ):
                raise LocalRepositorySourceUnavailable(
                    "local source target branch is not a distinct literal checkout"
                )
            if target_ref in git(
                source, ["for-each-ref", "--format=%(refname)", target_ref]
            ).decode().splitlines():
                # The isolated clone only has the selected branch. Absence
                # there does not prove a target would be created by native.
                raise LocalRepositorySourceUnavailable(
                    "local source target already names an existing source branch"
                )
        git(destination, ["read-tree", head])
        attributes = git(
            destination,
            ["check-attr", "--cached", "-z", "filter", "--stdin"],
            input_bytes=b"\0".join(name.encode() for name in sorted(members)) + b"\0",
        )
        fields = attributes.split(b"\0")
        if any(
            fields[index] not in {b"unspecified", b"unset"}
            for index in range(2, len(fields) - 1, 3)
        ):
            raise LocalRepositorySourceUnavailable(
                "local source needs external Git filter semantics"
            )
        git(
            destination,
            [
                "-c",
                "core.hooksPath=" + os.devnull,
                "checkout",
                "--force",
                *(
                    ["--detach", head] if pin
                    else ["--no-track", "-b", target_branch] if target_branch
                    else [branch]
                ),
            ],
        )
        refs = (
            git(
                destination,
                [
                    "for-each-ref",
                    "--format=%(refname) %(objectname) %(symref)",
                    "refs/heads/" + branch,
                    "refs/remotes/origin",
                ],
            )
            .decode()
            .splitlines()
        )
        safe_refs = {}
        for ref in refs:
            fields = ref.split()
            name, oid = fields[:2]
            if (
                name
                not in {
                    "refs/heads/" + branch,
                    "refs/remotes/origin/" + branch,
                    "refs/remotes/origin/HEAD",
                }
                or oid != tip
            ):
                raise ValueError("local clone produced unexpected reference authority")
            safe_refs[name] = {
                "commit": oid,
                **({"symbolicTarget": fields[2]} if len(fields) == 3 else {}),
            }
        # Source path is never durable remote authority. The private clone is
        # only an object reader; neither its config nor original .git is saved.
        git(destination, ["config", "--unset-all", "remote.origin.url"])
        history_payload, history = capture_git_history(
            destination,
            head,
            self_contained=True,
            full_ancestry=True,
            # The captured tree/index belongs to detached HEAD; the selected
            # branch and tracking refs still need their own verified objects.
            extra_heads=(tip,) if tip != head else (),
            env=env,
            executable=_WORKER_GIT_EXECUTABLE,
        )
        archive, entries = build_workspace_archive(destination, members=members)
        return head, branch, safe_refs, history_payload, history, archive, entries


async def capture_initial_source(
    *,
    request,
    intent,
    authority,
    workspace_root,
    artifact_service,
    access,
    revalidate_authority,
):
    """Export under the existing workspace claim fence, then seal dependencies."""
    import asyncio
    from types import SimpleNamespace

    from moonmind.schemas.saved_work_models import (
        saved_work_artifact_metadata,
        verify_captured_artifact_evidence,
        workspace_content_digest,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        _saved_work_execution_link,
        _seal_saved_work_artifact,
    )
    from moonmind.workflows.temporal.runtime.initial_repository_source import (
        CONTENT_TYPE,
        intent_digest,
        local_source_uses_default_access,
        source_intent,
        supports_detached_local_pin,
    )

    pin = intent.get("commitSha")
    if pin and not supports_detached_local_pin(request, intent):
        raise LocalRepositorySourceUnavailable(
            "local source pin requires a detached read without a selected connection"
        )
    target_branch = intent.get("targetBranch")
    if target_branch and (
        source_intent(request, bind_target_branch=True) != intent
        or not local_source_uses_default_access(request)
    ):
        raise LocalRepositorySourceUnavailable(
            "local source target branch requires a matching default-connection read"
        )
    if intent.get("sourcePath") != authority.get("sourcePath") or any(
        intent.get(key) != authority.get(key) for key in ("workflowId", "runId")
    ):
        raise ValueError("local source intent differs from admitted owner")
    store = SandboxWorkspaceRecordStore(Path(workspace_root))
    async with store.materialization_locked(authority["workspaceId"]):
        if store.active_claim_grantees(authority["workspaceId"]):
            raise ValueError("local source has an active workspace user")
        record = store.load(authority["workspaceId"])
        if record is None or (record.workflow_id, record.step_execution_id) != (
            authority["workflowId"],
            authority["stepExecutionId"],
        ):
            raise ValueError("local source owner changed")
        await revalidate_authority()
        task = asyncio.create_task(
            asyncio.to_thread(
                _capture_source_bytes, Path(authority["sourcePath"]), intent
            )
        )
        try:
            captured = await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancelled activity must not release the source-use fence while
            # its bounded reader is still running in a thread.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001 - preserve cancellation after draining
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise
    head, branch, refs, history_payload, history, archive, entries = captured

    async def put(payload, content_type, kind):
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if kind in {"checkpoint_manifest", "initial_repository_source"}:
            from moonmind.schemas.saved_work_models import scan_saved_work_export_stream

            scan = scan_saved_work_export_stream(
                iter([payload]),
                export_digest=digest,
                location=kind,
                high_security_mode=True,
            )
            if scan["disposition"] == "blocked":
                raise ValueError("local source metadata failed export scanning")
        artifact, _ = await artifact_service.put_content_addressed_payload_complete(
            principal="system",
            payload=payload,
            content_type=content_type,
            scope=kind,
            link=_saved_work_execution_link(
                SimpleNamespace(identity=request.step_execution)
            ),
            metadata_json={
                "artifact_kind": kind,
                **saved_work_artifact_metadata(kind, payload),
            },
        )
        verify_captured_artifact_evidence(
            expected_digest=digest, expected_size_bytes=len(payload), artifact=artifact
        )
        await _seal_saved_work_artifact(
            artifact_service,
            artifact,
            artifact_kind=kind,
            payload=payload,
        )
        return "artifact:" + artifact.artifact_id, digest

    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    history["ref"], _ = await put(
        history_payload, history["contentType"], "checkpoint_history"
    )
    archive_ref, archive_digest = await put(
        archive, "application/vnd.moonmind.worktree-archive", "checkpoint_archive"
    )
    manifest = {
        "schemaVersion": "v1",
        "kind": "worktree_archive",
        "baseCommit": head,
        "archiveRef": archive_ref,
        "archiveDigest": archive_digest,
        "workspaceDigest": workspace_content_digest(entries),
        "entries": entries,
        "pathCount": len(entries),
        "excludedPaths": [],
        "gitHistory": history,
        "git": {"branch": "" if pin else target_branch or branch},
    }
    manifest_ref, manifest_digest = await put(
        encode(manifest), "application/json", "checkpoint_manifest"
    )
    workspace = {
        "kind": "worktree_archive",
        "baseCommit": head,
        "archiveRef": archive_ref,
        "archiveDigest": archive_digest,
        "manifestRef": manifest_ref,
        "manifestDigest": manifest_digest,
    }
    descriptor = {
        "schemaVersion": "v1",
        "kind": "initial_repository_source",
        "source": authority,
        "sourceIntentDigest": intent_digest(intent),
        "workspace": workspace,
        "gitRefs": refs,
        **({"selectedBranch": branch} if pin or target_branch else {}),
        **({"targetBranch": target_branch} if target_branch else {}),
    }
    ref, digest = await put(
        encode(descriptor), CONTENT_TYPE, "initial_repository_source"
    )
    return {
        "artifactRef": ref,
        "contentDigest": digest,
        "sourceIntentDigest": intent_digest(intent),
    }
