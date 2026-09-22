"""Portable host entrypoint for the MoonMind deployment controller operation.

Only standard-library Python, Git and Compose are required on the host. The
selected image owns deployment semantics, including canary, promotion and drain.

This entrypoint is a client of the same controller operation observed by
Settings Operations (MoonMind#4502): every submission or resume attaches to
the durable ``depupd_`` operation in ``deploy/state/update-operations/``.
Duplicate submission and lost acknowledgment reattach to the live operation
instead of recording a second owner; ``--resume`` reattaches to the recorded
operation, and ``--retry-operation`` requests the controller's fresh bounded
attempt while preserving the first failure. The file format mirrors
``api_service/services/deployment_controller.py`` (standard library only, so
this script stays portable) and never carries secret material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


_MAX_DIAGNOSTIC_CHARS = 4000
_MAX_COMMAND_CHARS = 1000
_MAX_PUBLISHED_ANCESTOR_SEARCH = 20

# Bounded wait for the fetched tip's in-flight image publish, tried before
# selection falls back to a published ancestor so the exact requested commit
# still wins when its publish is merely late. ~10 minutes covers normal
# publish latency.
_PULL_RETRY_INTERVAL_SECONDS = 30
_PULL_RETRY_MAX_ATTEMPTS = 20

_URL_USERINFO_RE = re.compile(r"(://)[^/\s:]+:[^/\s@]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)(\s*[:=]\s*)(\S+)"
)

# Shared controller-operation identity (MoonMind#4502). Mirrors
# api_service/services/deployment_controller.py using only the standard
# library so this host entrypoint stays portable.
_CONTROLLER_OPERATIONS_SUBDIR = "update-operations"
_CONTROLLER_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED")
_CONTROLLER_MAX_ATTEMPTS = 3
_CONTROLLER_MAX_LOG_CHARS = 4000


class DockerPullError(RuntimeError):
    """A `docker pull` failure with a classified cause for retry decisions."""

    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


def _redact_diagnostics(text):
    """Redact likely credential material while keeping registry diagnostics."""
    redacted = _URL_USERINFO_RE.sub(r"\1***@", text or "")
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", redacted)


def _sleep(seconds):
    time.sleep(seconds)


def _controller_state_dir(repo):
    return Path(repo) / "deploy" / "state" / _CONTROLLER_OPERATIONS_SUBDIR


def _controller_dedupe_key(*, stack, repository, reference, mode, operation_kind, reason):
    material = "|".join(
        [
            "deployment-operation",
            stack,
            repository,
            reference,
            mode,
            operation_kind or "update",
            reason or "",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _controller_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _controller_atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _controller_observe(state_dir, operation_id):
    try:
        record = json.loads((state_dir / f"{operation_id}.json").read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _controller_find_live(state_dir, dedupe_key):
    try:
        names = sorted(
            name
            for name in os.listdir(state_dir)
            if name.startswith("depupd_") and name.endswith(".json")
        )
    except OSError:
        return None
    for name in names:
        try:
            record = json.loads((state_dir / name).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("dedupeKey") != dedupe_key:
            continue
        if str(record.get("status") or "") not in _CONTROLLER_TERMINAL_STATUSES:
            return record
    return None


def _controller_submit(state_dir, *, intent):
    """Attach to the live operation for identical intent or record a new one.

    Returns ``(record, created)``. A duplicate submission or lost
    acknowledgment reattaches (``created`` is False) so exactly one mutation
    owner exists per live intent. A changed target is explicit new intent and
    records a new operation.
    """

    dedupe = _controller_dedupe_key(
        stack=intent["stack"],
        repository=intent["repository"],
        reference=intent["reference"],
        mode=intent.get("mode", "changed_services"),
        operation_kind=intent.get("operation_kind", "update"),
        reason=intent.get("reason", ""),
    )
    live = _controller_find_live(state_dir, dedupe)
    if live is not None:
        return live, False
    now = _controller_now_iso()
    operation_id = f"depupd_{uuid.uuid4().hex[:24]}"
    record = {
        "operationId": operation_id,
        "stack": intent["stack"],
        "repository": intent["repository"],
        "reference": intent["reference"],
        "mode": intent.get("mode", "changed_services"),
        "operationKind": intent.get("operation_kind", "update"),
        "status": "QUEUED",
        "requestedImage": intent.get("requested_image"),
        "resolvedDigest": intent.get("resolved_digest"),
        "reason": intent.get("reason"),
        "operator": intent.get("operator", "local-operator"),
        "retryOf": None,
        "attempt": 1,
        "firstError": None,
        "error": None,
        "verificationPending": False,
        "logsText": "",
        "createdAt": now,
        "updatedAt": now,
        "historyImport": None,
        "dedupeKey": dedupe,
    }
    _controller_atomic_write_json(state_dir / f"{operation_id}.json", record)
    return record, True


def _controller_record_result(state_dir, operation_id, *, status, error=None):
    record = _controller_observe(state_dir, operation_id)
    if record is None:
        raise ValueError(f"Deployment operation {operation_id} is not known")
    redacted = _redact_diagnostics(str(error or "").strip()) or None
    record.update(
        {
            "status": status,
            "error": redacted,
            "firstError": record.get("firstError") or redacted,
            "updatedAt": _controller_now_iso(),
        }
    )
    _controller_atomic_write_json(state_dir / f"{operation_id}.json", record)
    return record


def _controller_retry(state_dir, operation_id, *, operator="local-operator"):
    """Request a fresh bounded attempt, preserving the first failure.

    A still-running operation reattaches instead of duplicating mutation.
    """

    record = _controller_observe(state_dir, operation_id)
    if record is None:
        raise ValueError(f"Deployment operation {operation_id} is not known")
    if str(record.get("status") or "") not in _CONTROLLER_TERMINAL_STATUSES:
        return record, False
    attempt = int(record.get("attempt") or 1)
    if attempt >= _CONTROLLER_MAX_ATTEMPTS:
        raise ValueError(
            f"Deployment operation {operation_id} exhausted its bounded retry "
            f"budget ({_CONTROLLER_MAX_ATTEMPTS} attempts); a changed target "
            "is explicit new intent"
        )
    now = _controller_now_iso()
    new_id = f"depupd_{uuid.uuid4().hex[:24]}"
    new_record = {
        "operationId": new_id,
        "stack": record.get("stack"),
        "repository": record.get("repository"),
        "reference": record.get("reference"),
        "mode": record.get("mode"),
        "operationKind": record.get("operationKind", "update"),
        "status": "QUEUED",
        "requestedImage": record.get("requestedImage"),
        "resolvedDigest": record.get("resolvedDigest"),
        "reason": record.get("reason"),
        "operator": operator,
        "retryOf": operation_id,
        "attempt": attempt + 1,
        "firstError": record.get("firstError") or record.get("error"),
        "error": None,
        "verificationPending": False,
        "logsText": "",
        "createdAt": now,
        "updatedAt": now,
        "historyImport": None,
        "dedupeKey": f"{record.get('dedupeKey')}|retry:{new_id}",
    }
    _controller_atomic_write_json(state_dir / f"{new_id}.json", new_record)
    return new_record, True


def _submission_intent(*, image_repository, reference, reason):
    return {
        "stack": "moonmind",
        "repository": image_repository,
        "reference": reference,
        "mode": "changed_services",
        "operation_kind": "update",
        "reason": reason or "",
    }


def _find_submission_for_operation(submissions, operation_id):
    try:
        names = sorted(
            name
            for name in os.listdir(submissions)
            if name.endswith(".json")
        )
    except OSError:
        return None, None
    for name in names:
        path = submissions / name
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("controllerOperationId") == operation_id:
            return path, record
    return None, None


def _attach_controller_operation(*, state_dir, submissions, record, submission_path=None):
    """Attach a host submission to the shared controller operation.

    A live identical intent reattaches (no second owner). A terminal linked
    operation starts a fresh bounded attempt preserving the first failure.
    Returns the operation record the adapter run belongs to.
    """

    inputs = record.get("inputs", {})
    image = inputs.get("image", {})
    intent = _submission_intent(
        image_repository=image.get("repository", ""),
        reference=image.get("reference", ""),
        reason=inputs.get("reason", ""),
    )
    intent["requested_image"] = record.get("image")
    digest = ""
    if "@" in str(record.get("image") or ""):
        digest = str(record["image"]).split("@", 1)[1]
    intent["resolved_digest"] = digest or None
    linked_id = record.get("controllerOperationId")
    if linked_id:
        linked = _controller_observe(state_dir, linked_id)
        if linked is not None:
            if str(linked.get("status") or "") not in _CONTROLLER_TERMINAL_STATUSES:
                print(
                    f"Reattaching to live controller operation {linked_id}; "
                    "no second updater is started",
                    flush=True,
                )
                return linked
            retried, _ = _controller_retry(state_dir, linked_id)
            print(
                f"Controller operation {linked_id} is terminal; continuing "
                f"as bounded attempt {retried['attempt']} "
                f"({retried['operationId']}) preserving the first failure",
                flush=True,
            )
            record["controllerOperationId"] = retried["operationId"]
            if submission_path is not None:
                _controller_atomic_write_json(submission_path, record)
            return retried
    operation, created = _controller_submit(state_dir, intent=intent)
    record["controllerOperationId"] = operation["operationId"]
    if submission_path is not None:
        _controller_atomic_write_json(submission_path, record)
    if not created:
        print(
            f"Reattaching to live controller operation {operation['operationId']}; "
            "no second updater is started",
            flush=True,
        )
    return operation


def _classify_pull_failure(combined_lower):
    if (
        "cannot connect to the docker daemon" in combined_lower
        or "is the docker daemon running" in combined_lower
        or "permission denied while trying to connect" in combined_lower
    ):
        return "daemon"
    if (
        "unauthorized" in combined_lower
        or "authentication required" in combined_lower
        or "no basic auth credentials" in combined_lower
        or "login required" in combined_lower
        or "permission_denied" in combined_lower
        or "denied: denied" in combined_lower
        or "access denied" in combined_lower
    ):
        return "auth"
    if (
        "manifest unknown" in combined_lower
        or "manifest for" in combined_lower
        or "name unknown" in combined_lower
        or "no such image" in combined_lower
        or "not found" in combined_lower
    ):
        return "unpublished"
    return "unknown"


_PULL_HINTS = {
    "daemon": (
        "Hint: the Docker daemon is unreachable; start Docker Desktop "
        "(or check DOCKER_HOST and socket permissions) and retry."
    ),
    "auth": (
        "Hint: registry authentication failed; run `docker login ghcr.io` "
        "with an account that can read the release repository and retry."
    ),
    "unpublished": (
        "Hint: the fetched commit has no published image yet; check the "
        "image publish workflow for that SHA, wait for it to publish, then "
        "retry. Never substitute `latest` for the pinned sha-<commit> image; "
        "for local development use --local-build instead."
    ),
}


def _docker_pull_hint(combined_lower):
    return _PULL_HINTS.get(_classify_pull_failure(combined_lower), "")


def run(args, *, cwd, env=None):
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )
    if result.returncode:
        command = f"{args[0]} {args[1]}" if len(args) > 1 else str(args[0])
        full_command = _redact_diagnostics(" ".join(str(part) for part in args))[
            :_MAX_COMMAND_CHARS
        ]
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        combined = f"{stdout}\n{stderr}".strip()
        detail = _redact_diagnostics(combined).strip()[-_MAX_DIAGNOSTIC_CHARS:]
        hint = ""
        if len(args) > 1 and args[0] == "docker" and args[1] == "pull":
            hint = _docker_pull_hint(combined.lower())
        message = (
            f"{command} failed (exit {result.returncode}); "
            "deployment remains owned by its recorded release job"
            f"\nCommand (redacted): {full_command}"
        )
        if detail:
            message += f"\nDiagnostics (redacted):\n{detail}"
        else:
            message += "\nDiagnostics: no output captured."
        if hint:
            message += f"\n{hint}"
        # Docker/Git diagnostics can contain registry or remote credentials,
        # so only the redacted form above is reported.
        if len(args) > 1 and args[0] == "docker" and args[1] == "pull":
            raise DockerPullError(
                message, _classify_pull_failure(combined.lower())
            ) from None
        raise RuntimeError(message)
    return (getattr(result, "stdout", "") or "").strip()


def _select_release_image(*, repo, branch, tip_revision, image_repository):
    """Select the newest published image on the fetched branch history.

    The fetched tip is preferred: an unpublished pull failure means its publish
    workflow is still running, so the tip is retried for a bounded interval
    before selection falls back to its newest published first-parent ancestor.
    Commits are immutable, so waiting cannot change which artifact a candidate
    names. Auth, daemon and unknown failures propagate immediately instead of
    masking a broken registry as an unpublished release.

    Returns the selected ``(revision, image, skipped_unpublished)``.
    """
    try:
        candidates = run(
            [
                "git",
                "rev-list",
                "--first-parent",
                "-n",
                str(_MAX_PUBLISHED_ANCESTOR_SEARCH),
                tip_revision,
            ],
            cwd=repo,
        ).split()
    except RuntimeError:
        candidates = []
    if tip_revision not in candidates:
        candidates = [tip_revision, *candidates]
    skipped = []
    last_error = None
    for candidate in candidates:
        image = f"{image_repository}:sha-{candidate}"
        # Only the tip can still be publishing; older ancestors have settled.
        attempts = _PULL_RETRY_MAX_ATTEMPTS if candidate == tip_revision else 1
        for attempt in range(1, attempts + 1):
            try:
                run(["docker", "pull", image], cwd=repo)
                return candidate, image, skipped
            except DockerPullError as exc:
                if exc.category != "unpublished":
                    raise
                last_error = exc
            if attempt < attempts:
                print(
                    f"Published image {image} not yet available "
                    f"(attempt {attempt}/{attempts}); waiting "
                    f"{_PULL_RETRY_INTERVAL_SECONDS}s for the image publish "
                    "workflow...",
                    flush=True,
                )
                _sleep(_PULL_RETRY_INTERVAL_SECONDS)
        skipped.append(candidate)
    raise RuntimeError(
        f"Published image {image_repository}:sha-{tip_revision} for "
        f"origin/{branch} revision {tip_revision} is unavailable; checked "
        f"{len(candidates)} commit(s) ({', '.join(skipped)}) with no published "
        f"image; {last_error}"
    ) from last_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--branch", default="main")
    parser.add_argument("--compose-project")
    parser.add_argument(
        "--operator-url", action="append", default=[],
        help="Existing operator origin to verify without changing API authentication configuration; repeat for multiple origins",
    )
    parser.add_argument(
        "--image-repository", default="ghcr.io/moonladderstudios/moonmind"
    )
    parser.add_argument(
        "--resume", help="Resume the printed submission ID using its original inputs"
    )
    parser.add_argument(
        "--retry-operation",
        help="Request a fresh bounded controller attempt for a terminal "
        "operation ID that has a recorded host submission, preserving the "
        "first failure",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--local-build", action="store_true",
        help="Development-only working-tree overlay update (bind-mounts live "
        "source via docker-compose.development.yaml). Never an immutable "
        "release: no image is pulled, published, or digest-pinned and no "
        "release submission is recorded. The default immutable path is "
        "unchanged when this flag is absent.",
    )
    args = parser.parse_args(argv)
    if args.resume and args.operator_url:
        raise ValueError("Resume preserves the original operator URLs; omit --operator-url")
    if args.retry_operation and args.resume:
        raise ValueError("Retry resolves its own recorded submission; omit --resume")
    if args.retry_operation and args.operator_url:
        raise ValueError("Retry preserves the original operator URLs; omit --operator-url")
    if args.retry_operation and args.dry_run:
        raise ValueError("Retry mutates controller state; omit --dry-run")
    if args.local_build and (args.resume or args.retry_operation):
        raise ValueError("Resume replays a recorded release submission; omit --local-build")
    if args.local_build and args.image_repository != parser.get_default("image_repository"):
        raise ValueError("A local working-tree update uses no registry image; omit --image-repository")
    repo = args.repo.resolve(strict=True)
    if args.local_build:
        return _local_build_update(args, repo)
    submissions = repo / "deploy" / "state" / "release-submissions"
    state_dir = _controller_state_dir(repo)
    if args.retry_operation:
        submission_path, record = _find_submission_for_operation(
            submissions, str(args.retry_operation).strip()
        )
        if record is None:
            raise ValueError(
                f"Controller operation {args.retry_operation} has no recorded "
                "host submission; retry it from Settings Operations or start "
                "a new host update"
            )
        if record["repo"] != str(repo):
            raise ValueError("Release submission belongs to another deployment")
        submission_id = submission_path.stem
        operation = _attach_controller_operation(
            state_dir=state_dir,
            submissions=submissions,
            record=record,
            submission_path=submission_path,
        )
    elif args.resume:
        submission_id = str(uuid.UUID(args.resume))
        submission_path = submissions / f"{submission_id}.json"
        record = json.loads(submission_path.read_text())
        if record["repo"] != str(repo):
            raise ValueError("Release submission belongs to another deployment")
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "action": "resume_release",
                        "submissionId": submission_id,
                        "image": record["image"],
                    }
                )
            )
            return 0
        operation = _attach_controller_operation(
            state_dir=state_dir,
            submissions=submissions,
            record=record,
            submission_path=submission_path,
        )
    else:
        run(["git", "check-ref-format", "--branch", args.branch], cwd=repo)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "action": "qualify_and_promote_immutable_release",
                        "repository": str(repo),
                        "branch": args.branch,
                        "checkoutMutation": False,
                        "imageSource": args.image_repository + ":sha-<fetched-commit>",
                    }
                )
            )
            return 0
        run(["git", "fetch", "origin", args.branch], cwd=repo)
        tip_revision = run(
            ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"], cwd=repo
        )
        revision, image, skipped_unpublished = _select_release_image(
            repo=repo,
            branch=args.branch,
            tip_revision=tip_revision,
            image_repository=args.image_repository,
        )
        if revision != tip_revision:
            print(
                f"Tip revision {tip_revision[:12]} has no published image yet; "
                f"using newest published ancestor {revision[:12]} "
                f"(skipped {len(skipped_unpublished)} unpublished commit(s))",
                flush=True,
            )
        observed = json.loads(run(["docker", "image", "inspect", image], cwd=repo))[0]
        if (
            observed.get("Config", {})
            .get("Labels", {})
            .get("org.opencontainers.image.revision")
            != revision
        ):
            raise ValueError(
                "The selected image does not contain the fetched source revision"
            )
        digests = [
            value
            for value in observed.get("RepoDigests", [])
            if value.split("@", 1)[0] == args.image_repository
        ]
        if len(digests) != 1:
            raise ValueError(
                "The selected image has no unique immutable repository digest"
            )
        rendered = json.loads(
            run(["docker", "compose", "config", "--format", "json"], cwd=repo)
        )
        project = args.compose_project or rendered["name"]
        submission_id = str(uuid.uuid4())
        operator_urls = list(args.operator_url)
        inputs = {
            "stack": "moonmind",
            "image": {
                "repository": args.image_repository,
                "reference": digests[0].split("@", 1)[1],
            },
            "sourceRevision": revision,
            "reason": "Update to selected branch snapshot",
        }
        if revision != tip_revision:
            inputs["requestedTipRevision"] = tip_revision
            inputs["skippedUnpublishedRevisions"] = list(skipped_unpublished)
        record = {
            "repo": str(repo),
            "project": project,
            "image": digests[0],
            "inputs": inputs,
            "context": {
                "idempotency_key": f"host-update:{submission_id}",
                "operator": "local-operator",
                "operator_role": "operator",
                **({"deployment_operator_urls": operator_urls} if operator_urls else {}),
            },
        }
        # The host update is a client of the same controller operation the
        # Operations UI observes: identical live intent reattaches instead of
        # recording a second owner.
        operation = _attach_controller_operation(
            state_dir=state_dir,
            submissions=submissions,
            record=record,
        )
        submissions.mkdir(parents=True, exist_ok=True)
        with (submissions / f"{submission_id}.json").open("x") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    print(
        f"Release submission: {submission_id} (resume with --resume {submission_id})",
        flush=True,
    )
    print(
        f"Controller operation: {operation['operationId']} "
        f"(status {operation['status']})",
        flush=True,
    )
    compose = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            record["image"],
            "-c",
            "from pathlib import Path; print(Path('/app/release/docker-compose.yaml').read_text())",
        ],
        cwd=repo,
    )
    # No source checkout/reset is involved. Relative deployment configuration and
    # existing operator-owned .env still resolve against the installed project.
    with tempfile.TemporaryDirectory(prefix="moonmind-release-") as temp:
        path = Path(temp) / "compose.yaml"
        path.write_text(compose)
        command = [
            "docker",
            "compose",
            "--project-name",
            record["project"],
            "--project-directory",
            str(repo),
            "-f",
            str(path),
        ]
        for name in ("docker-compose.override.yaml", "docker-compose.override.yml"):
            override = repo / name
            if override.exists():
                command.extend(["-f", str(override)])
                break
        command.extend(
            [
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "-e",
                f"MOONMIND_DEPLOYMENT_PROJECT_NAME={record['project']}",
                "-e",
                f"MOONMIND_DEPLOYMENT_PROJECT_DIR={repo}",
                # Same substrate protection as the process environment below,
                # stated explicitly: `run -e` wins over service interpolation,
                # so the deployment-control submitter and everything it
                # launches inherit the exclusion even if interpolation drifts.
                "-e",
                "MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES=docker-proxy,sandbox-egress-proxy,postgres",
                "temporal-worker-deployment-control",
                "-m",
                "moonmind.workflows.skills.deployment_release",
                "--submit",
                json.dumps({"inputs": record["inputs"], "context": record["context"]}),
            ]
        )
        # The image-owned deployment-control invocation underneath remains the
        # execution adapter for this operation until #4500's execution engine
        # lands; the shared operation record above is the identity both the
        # host and Settings Operations observe.
        returncode = subprocess.run(
            command,
            cwd=repo,
                env={
                    **os.environ,
                    "MOONMIND_IMAGE": record["image"],
                    "MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES": "docker-proxy,sandbox-egress-proxy,postgres",
                    # The updater reaches Docker through docker-proxy, and
                    # postgres/sandbox-egress-proxy are stateful substrate:
                    # recreating them through a rewritten-bind render on every
                    # release caused repeated proxy suicide (killing all
                    # later docker calls) and a postgres removal. Host-
                    # initiated updates still exclude that substrate from the
                    # main pull/reconcile/verify stage while leaving it
                    # running, so the controller never recreates its own
                    # transport mid-update. The controller then reconciles
                    # release-owned substrate whose definition drifted in a
                    # staged pass after the main stack verifies, and fails
                    # the release when that substrate does not converge.
                },
            check=False,
        ).returncode
        _controller_record_result(
            state_dir,
            operation["operationId"],
            status="SUCCEEDED" if returncode == 0 else "FAILED",
            error=None if returncode == 0 else (
                f"host adapter exited with status {returncode}"
            ),
        )
        return returncode


def _compose_ps_state(*, repo, project):
    """Return {service name: sorted port bindings} for the Compose project."""
    output = run(
        ["docker", "compose", "--project-name", project, "ps", "--format", "json"],
        cwd=repo,
    )
    try:
        records = json.loads(output or "[]")
    except ValueError:
        records = []
        for line in (output or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    if isinstance(records, dict):
        records = [records]
    state = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("Service") or "")
        bindings = sorted(
            "{url}:{published}->{target}/{proto}".format(
                url=pub.get("URL") or "",
                published=pub.get("PublishedPort") or "",
                target=pub.get("TargetPort") or "",
                proto=pub.get("Protocol") or "",
            )
            for pub in (item.get("Publishers") or [])
            if isinstance(pub, dict)
        )
        if name:
            state[name] = bindings
    return state


def _check_operator_url(url, *, timeout_seconds=30):
    import urllib.request

    target = url.rstrip("/") + "/healthz"
    request = urllib.request.Request(target, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.getcode()
    except Exception as exc:
        raise RuntimeError(
            f"Operator URL {url} failed its health check; deployment update did not verify"
        ) from exc
    if status != 200:
        raise RuntimeError(
            f"Operator URL {url} returned HTTP {status} from /healthz; deployment update did not verify"
        )


def _local_build_update(args, repo):
    """Recreate the stack on the live-source development overlay.

    Development-only: exercises the working tree without building,
    publishing, or pinning any image. Writes no release submission and
    claims no digest. Deployment-owned `.env` is never modified.
    """
    head = run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repo)
    dirty = bool(
        run(
            ["git", "status", "--porcelain=v1", "--untracked-files=no"], cwd=repo
        ).strip()
    )
    overlay = repo / "docker-compose.development.yaml"
    if not overlay.exists():
        overlay = repo / "docker-compose.development.yml"
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "local_source_overlay_update",
                    "repository": str(repo),
                    "source": "working-tree",
                    "head": head,
                    "dirty": dirty,
                    "overlay": overlay.name if overlay.exists() else None,
                    "operatorUrls": list(args.operator_url),
                    "checkoutMutation": False,
                    "releaseSubmission": "none (development-only; not an immutable release)",
                }
            )
        )
        return 0
    if not overlay.exists():
        raise RuntimeError(
            "The repository has no live-source development overlay; refusing a local working-tree update"
        )
    rendered = json.loads(
        run(["docker", "compose", "config", "--format", "json"], cwd=repo)
    )
    project = args.compose_project or rendered["name"]
    before = _compose_ps_state(repo=repo, project=project)
    if not before:
        raise RuntimeError(
            "Could not read pre-update Compose state; refusing a local working-tree update without a binding baseline"
        )
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(repo),
        "-f",
        "docker-compose.yaml",
        "-f",
        overlay.name,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "600",
    ]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(
            f"docker compose up failed (exit {result.returncode}); deployment remains on its previous containers"
        )
    after = _compose_ps_state(repo=repo, project=project)
    for name, bindings in before.items():
        if after.get(name) != bindings:
            raise RuntimeError(
                f"Published bindings changed for {name}; expected the overlay update to preserve operator access"
            )
    for url in args.operator_url:
        _check_operator_url(url)
    print(
        "Local working-tree overlay update verified (development-only; not an "
        f"immutable release; head {head[:12]}{' dirty' if dirty else ''})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
