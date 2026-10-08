"""Portable host entrypoint for the standalone MoonMind release controller.

Only standard-library Python, Git and Compose are required on the host. The
submission is executed by the standalone controller in its own Compose
project (deploy/controller), which owns image staging, changed-service
``up``, local state, and recovery independently of MoonMind health.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

_DEFAULT_CONTROLLER_URL = os.environ.get(
    "MOONMIND_CONTROLLER_URL", "http://127.0.0.1:8472"
)
# Services the controller never recreates through itself: the Docker transport
# substrate. Postgres stays in the service set: `up` is a no-op while its
# definition is unchanged, and required init-db gating stays inside Compose.
_CONTROLLER_EXCLUDED_SERVICES = frozenset({"docker-proxy", "sandbox-egress-proxy"})
_CONTROLLER_POLL_INTERVAL_SECONDS = 10
_CONTROLLER_POLL_TIMEOUT_SECONDS = 1800
# The controller applies inside the submission request; after this bounded
# wait the entrypoint observes its own operation identity instead.
_CONTROLLER_SUBMIT_TIMEOUT_SECONDS = 30
_CONTROLLER_SUBMIT_ATTEMPTS = 2


_MAX_DIAGNOSTIC_CHARS = 4000
_MAX_COMMAND_CHARS = 1000
_MAX_PUBLISHED_ANCESTOR_SEARCH = 20
_LEGACY_TRANSPORT_LEASE_TIMEOUT_SECONDS = 30
_LEGACY_TRANSPORT_LEASE_READY = "MOONMIND_TRANSPORT_LEASE_READY"

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


class DockerPullError(RuntimeError):
    """A `docker pull` failure with a classified cause for retry decisions."""

    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


class ControllerUnreachableError(RuntimeError):
    """The controller transport did not answer."""


class ControllerHTTPError(RuntimeError):
    """The controller answered with an HTTP error status."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _redact_diagnostics(text):
    """Redact likely credential material while keeping registry diagnostics."""
    redacted = _URL_USERINFO_RE.sub(r"\1***@", text or "")
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", redacted)


def _sleep(seconds):
    time.sleep(seconds)


def _classify_pull_failure(combined_lower):
    if (
        "read-only file system" in combined_lower
        or "no space left on device" in combined_lower
    ):
        return "storage"
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
    "storage": (
        "Hint: Docker storage is not writable. Check free space on both the "
        "host and Docker data disk; reclaim unused images/build cache. On "
        "Docker Desktop, free host disk space before restarting Desktop to "
        "recover a read-only filesystem, then retry. Preserve deployment "
        "volumes and active workflow data; do not reset Docker's data disk."
    ),
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
            f"{command} failed (exit {result.returncode})"
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
        "--controller-url", default=_DEFAULT_CONTROLLER_URL,
        help="Standalone controller endpoint (default %(default)s)",
    )
    parser.add_argument(
        "--controller-secret-file", default=None,
        help="Deployment-owned controller bearer secret file "
        "(default <repo>/deploy/state/controller/secrets/controller-bearer "
        "or $MOONMIND_CONTROLLER_SECRET_FILE)",
    )
    parser.add_argument(
        "--legacy-direct", action="store_true",
        help="Transitional: confirm the legacy application-owned updater for "
        "a deployment without an installed controller (for example to resume "
        "a legacy submission). Refused once a controller owns the deployment.",
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
    requested_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(requested_args)
    if args.resume and args.operator_url:
        raise ValueError("Resume preserves the original operator URLs; omit --operator-url")
    if args.local_build and args.resume:
        raise ValueError("Resume replays a recorded release submission; omit --local-build")
    if args.local_build and args.image_repository != parser.get_default("image_repository"):
        raise ValueError("A local working-tree update uses no registry image; omit --image-repository")
    repo = args.repo.resolve(strict=True)
    if args.local_build:
        return _local_build_update(args, repo)
    submissions = repo / "deploy" / "state" / "release-submissions"
    if args.resume:
        submission_id = str(uuid.UUID(args.resume))
        record = json.loads((submissions / f"{submission_id}.json").read_text())
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
        submissions.mkdir(parents=True, exist_ok=True)
        with (submissions / f"{submission_id}.json").open("x") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    print(
        f"Release submission: {submission_id} (resume with --resume {submission_id})",
        flush=True,
    )
    return _submit_release(
        record,
        repo,
        controller_url=args.controller_url,
        secret_file=args.controller_secret_file,
        legacy_direct=args.legacy_direct,
        controller_url_explicit=(
            bool(os.environ.get("MOONMIND_CONTROLLER_URL"))
            or any(
                arg == "--controller-url" or arg.startswith("--controller-url=")
                for arg in requested_args
            )
        ),
        is_resume=args.resume is not None,
    )


def _submit_release(
    record,
    repo,
    *,
    controller_url,
    secret_file,
    legacy_direct,
    controller_url_explicit=False,
    is_resume=False,
):
    """Route the recorded submission to its installed execution owner.

    An installed (or explicitly selected) standalone controller owns the
    update. Until a deployment installs it, the application-owned updater
    remains the supported default so a bare invocation still updates.

    A resume never takes that automatic fallback on its own: the original
    submission may still be owned by the controller, and forking the legacy
    updater would create two deployment writers. Resume requires the owner
    to be reconciled first via an explicit selection.
    """
    explicit_controller = bool(
        secret_file
        or os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE")
        or controller_url_explicit
    )
    if not controller_url_explicit:
        identity = _controller_identity(repo)
        port = identity.get("port") if identity else None
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
            controller_url = f"http://127.0.0.1:{port}"
    default_secret = _default_controller_secret_file(repo)
    legacy_notice = None
    if not explicit_controller and not default_secret.exists():
        # The notice stays free of secret material (CodeQL clear-text
        # logging): it names no secret path or value, only the installer.
        legacy_notice = (
            "Standalone controller is not installed; updating through the "
            "application-owned updater. Install the controller with "
            "`python3 deploy/controller/bootstrap.py install` to use it."
        )
    elif not explicit_controller:
        secret = default_secret.read_text(encoding="utf-8").strip()
        try:
            _controller_call(controller_url, secret, "GET", "/v1/healthz", timeout=5)
        except ControllerUnreachableError:
            # Bootstrap may have written a secret and Compose file before its
            # unpublished image could start. Fall back only if that controller
            # never recorded an operation and owns no container; a stopped
            # controller with durable work must retain its recovery authority.
            if not _controller_never_started(repo):
                if legacy_direct:
                    _refuse_legacy_direct()
                # Keep the same owner. Its host-owned prerequisite can
                # reconcile a failed controller recreation under its lock.
                return _submit_via_controller(
                    record, repo, controller_url=controller_url, secret_file=secret_file,
                )
            else:
                legacy_notice = (
                    "Standalone controller bootstrap did not start a service; "
                    "updating through the application-owned updater."
                )
    if legacy_direct:
        if legacy_notice is None:
            _refuse_legacy_direct()
        return _submit_legacy_direct(record, repo)
    if legacy_notice is not None:
        _reject_automatic_resume(is_resume)
        print(legacy_notice, flush=True)
        return _submit_legacy_direct(record, repo)
    return _submit_via_controller(
        record,
        repo,
        controller_url=controller_url,
        secret_file=secret_file,
    )


def _refuse_legacy_direct():
    raise RuntimeError(
        "Refusing --legacy-direct: a standalone controller owns this "
        "deployment, and a second updater could compete with its "
        "operation. Omit --legacy-direct to update through the controller."
    )


def _reject_automatic_resume(is_resume):
    if is_resume:
        raise RuntimeError(
            "Refusing to resume with the legacy application-owned "
            "updater: the original submission may still be owned by the "
            "standalone controller. Reconcile controller ownership "
            "first, then resume with --legacy-direct to confirm the "
            "legacy path or --controller-secret-file to resume through "
            "the controller."
        )


def _default_controller_secret_file(repo):
    override = os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE")
    if override:
        return Path(override)
    return repo / "deploy" / "state" / "controller" / "secrets" / "controller-bearer"


def _controller_identity(repo):
    """Read the installed endpoint and project without creating new state."""
    path = repo / "deploy" / "state" / "controller" / "controller-identity.json"
    if not path.is_file():
        return None
    try:
        identity = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return identity if isinstance(identity, dict) else {}


def _controller_never_started(repo):
    """Prove an incomplete bootstrap owns no operation or Compose container."""
    state_dir = repo / "deploy" / "state" / "controller"
    operations_dir = state_dir / "operations"
    if operations_dir.exists() and any(operations_dir.iterdir()):
        return False
    identity = _controller_identity(repo)
    if identity is None:
        return not (state_dir / "controller-compose.yaml").exists()
    project = identity.get("project")
    if not isinstance(project, str) or not project:
        return False
    containers = run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ],
        cwd=repo,
    )
    return not containers.strip()


def _controller_call(controller_url, secret, method, path, payload=None, timeout=30):
    from urllib.parse import urljoin

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        urljoin(controller_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = _redact_diagnostics(exc.read().decode("utf-8", errors="replace")[-2000:])
        raise ControllerHTTPError(
            f"Controller {method} {path} failed with HTTP {exc.code}: {detail}",
            exc.code,
        ) from None
    except urllib.error.URLError as exc:
        raise ControllerUnreachableError(
            f"Controller at {controller_url} is unreachable ({exc.reason}); "
            "start or restore it with "
            "`python3 deploy/controller/bootstrap.py restore`."
        ) from None
    except (OSError, TimeoutError) as exc:
        # The request may have reached the controller; only its answer was
        # lost. Callers resolve that through the operation identity.
        raise ControllerUnreachableError(
            f"Controller at {controller_url} did not answer ({type(exc).__name__})"
        ) from None


def _controller_operation_id(context):
    """Derive the controller operation identity from the recorded submission.

    The same submission (including ``--resume``) always names the same
    controller operation, so a lost acknowledgment or a rerun reattaches
    instead of starting a second writer.
    """
    submission = str(context.get("idempotency_key", "")).removeprefix("host-update:")
    candidate = f"host-{submission}" if submission else f"host-{uuid.uuid4()}"
    return re.sub(r"[^A-Za-z0-9._-]", "-", candidate)[:128]


def _submit_controller_operation(controller_url, secret, payload):
    """Submit once; after a lost acknowledgment observe before resubmitting."""
    operation_id = payload["operationId"]
    last_error = None
    for _attempt in range(_CONTROLLER_SUBMIT_ATTEMPTS):
        try:
            _, created = _controller_call(
                controller_url,
                secret,
                "POST",
                "/v1/operations",
                payload,
                timeout=_CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
            )
            return created
        except ControllerUnreachableError as exc:
            last_error = exc
            print(
                f"Controller did not acknowledge operation {operation_id}; "
                f"observing it before any resubmission ({exc})",
                flush=True,
            )
        try:
            _, observed = _controller_call(
                controller_url, secret, "GET", f"/v1/operations/{operation_id}"
            )
            return observed
        except ControllerHTTPError as exc:
            if exc.status != 404:
                raise
            # Never recorded: resubmitting the same identity is safe.
        except ControllerUnreachableError as exc:
            last_error = exc
            break
    raise last_error


def _resolve_compose_files(repo):
    """Resolve the deployment-owned Compose file set for the controller.

    The initial `docker compose config` honors deployment-owned selection
    such as COMPOSE_FILE; the controller request must carry that same
    resolved file set instead of a hard-coded base file, or an installation
    using extra files (for example `COMPOSE_FILE=docker-compose.yaml:site.yaml`)
    would apply with a materially different stack. Each entry resolves
    relative to the deployment checkout and must exist.
    """
    selection = os.environ.get("COMPOSE_FILE", "")
    if selection.strip():
        files = []
        for part in re.split(r"[;:]", selection):
            name = part.strip()
            if not name:
                continue
            candidate = Path(name)
            if not candidate.is_absolute():
                candidate = repo / name
            if not candidate.is_file():
                raise RuntimeError(
                    f"COMPOSE_FILE entry does not exist: {name!r}; refusing "
                    "to apply with a different file set than the deployment uses."
                )
            try:
                files.append(str(candidate.resolve().relative_to(repo.resolve())))
            except ValueError:
                raise RuntimeError(
                    f"COMPOSE_FILE entry is outside the deployment checkout: "
                    f"{name!r}; refusing to pass an out-of-project file set "
                    "to the controller."
                ) from None
        if not files:
            raise RuntimeError(
                "COMPOSE_FILE is set but selects no files; refusing to apply "
                "with a different file set than the deployment uses."
            )
        return files
    compose_files = ["docker-compose.yaml"]
    for name in ("docker-compose.override.yaml", "docker-compose.override.yml"):
        if (repo / name).exists():
            compose_files.append(name)
            break
    return compose_files


def _ensure_controller_journal_transition(record, repo, controller_url, secret):
    """Use the existing host lifecycle before an old controller owns work.

    The portable script extracts only trusted, image-owned Python sources;
    it does not depend on a checked-out release or a healthy API/worker.
    Installed source comes first so a rollback can use current repair code.
    """
    try:
        _, health = _controller_call(
            controller_url, secret, "GET", "/v1/healthz", timeout=5
        )
    except ControllerUnreachableError:
        health = {}
    if "active-journal-transition" in (health.get("capabilities") or ()):
        return
    ids = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={record['project']}",
        ],
        cwd=repo,
    ).split()
    candidates = []
    if ids:
        for container in json.loads(run(["docker", "inspect", *ids], cwd=repo)):
            service = ((container.get("Config") or {}).get("Labels") or {}).get(
                "com.docker.compose.service"
            )
            if service in {
                "api",
                "temporal-worker-agent-runtime",
                "temporal-worker-deployment-control",
            }:
                image = container.get("Image")
                if image and image not in candidates:
                    candidates.append(image)
    candidates.append(record["image"])
    export = (
        "import json,pathlib,sys; p=pathlib.Path('/app/deploy/controller'); "
        "sys.path.insert(0,str(p)); "
        "present=(p/'bootstrap.py').is_file(); "
        "print('{}') if not present else None; "
        "sys.exit(0) if not present else None; import bootstrap,server; "
        "supported=callable(getattr(bootstrap,'cmd_ensure',None)) and "
        "'active-journal-transition' in getattr(server,'CONTROLLER_CAPABILITIES',()); "
        "print(json.dumps({f.name:f.read_text() for f in p.glob('*.py')} if supported else {}))"
    )
    for image in candidates:
        bundle = json.loads(
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network=none",
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    export,
                ],
                cwd=repo,
            )
        )
        if not bundle:
            continue
        with tempfile.TemporaryDirectory(
            prefix="moonmind-controller-bootstrap-"
        ) as directory:
            for name, source in bundle.items():
                if (
                    Path(name).name != name
                    or not name.endswith(".py")
                    or not isinstance(source, str)
                ):
                    raise RuntimeError(
                        "Controller image returned an invalid source bundle."
                    )
                (Path(directory) / name).write_text(source, encoding="utf-8")
            run(
                [
                    sys.executable,
                    str(Path(directory) / "bootstrap.py"),
                    "ensure",
                    "--state-dir",
                    str(repo / "deploy" / "state" / "controller"),
                    "--repo",
                    str(repo),
                    "--stack",
                    "moonmind",
                    "--target-project",
                    record["project"],
                    "--image",
                    record["image"],
                    "--controller-url",
                    controller_url,
                ],
                cwd=repo,
            )
        _, health = _controller_call(
            controller_url, secret, "GET", "/v1/healthz", timeout=5
        )
        if "active-journal-transition" not in (health.get("capabilities") or ()):
            raise RuntimeError(
                "Controller prerequisite did not expose journal transition support."
            )
        return
    raise RuntimeError(
        "Neither installed source nor requested image supplies the controller prerequisite."
    )


def _submit_via_controller(record, repo, *, controller_url, secret_file):
    """Execute the recorded submission through the standalone controller.

    The controller owns image staging, changed-service `up`, local state, and
    recovery. Its Docker transport and endpoint survive target-project
    shutdown, so this path works while MoonMind itself is unhealthy.
    """
    secret_path = Path(secret_file) if secret_file else _default_controller_secret_file(repo)
    if not secret_path.exists():
        raise RuntimeError(
            f"Controller secret is missing at {secret_path}; install the "
            "controller first with "
            "`python3 deploy/controller/bootstrap.py install`."
        )
    secret = secret_path.read_text(encoding="utf-8").strip()
    _ensure_controller_journal_transition(record, repo, controller_url, secret)
    rendered = json.loads(
        run(["docker", "compose", "config", "--format", "json"], cwd=repo)
    )
    configured = rendered.get("services", {}) or {}
    services = sorted(
        name for name in configured if name not in _CONTROLLER_EXCLUDED_SERVICES
    )
    if not services:
        raise RuntimeError("Controller apply has no services to reconcile.")
    compose_files = _resolve_compose_files(repo)
    context = record.get("context", {}) or {}
    operator_urls = list(context.get("deployment_operator_urls", []) or [])
    deployment_env = repo / ".env"
    target = {
        "project": record["project"],
        "projectDir": str(repo),
        "composeFiles": compose_files,
        "services": services,
        "idempotencyKey": context.get("idempotency_key", ""),
    }
    if deployment_env.is_file():
        # The controller layers this under its image overlay so Compose
        # keeps operator authentication, bindings, and infrastructure
        # versions instead of rendering with defaults.
        target["envFile"] = str(deployment_env)
    if operator_urls:
        # Recorded for post-apply operator-access verification; the
        # controller stores the submission target unchanged.
        target["operatorUrls"] = operator_urls
    created = _submit_controller_operation(
        controller_url,
        secret,
        {
            "operationId": _controller_operation_id(context),
            "stack": "moonmind",
            "desiredImage": record["image"],
            "sourceRevision": record["inputs"].get("sourceRevision", ""),
            "reason": record["inputs"].get("reason", ""),
            "target": target,
        },
    )
    # The controller may answer with the operation that already owns this
    # target (a duplicate from Settings Operations); observe that one.
    operation_id = created.get("operationId")
    if not operation_id:
        raise RuntimeError(f"Controller refused the submission: {created}")
    print(f"Controller operation: {operation_id}", flush=True)
    deadline = time.time() + _CONTROLLER_POLL_TIMEOUT_SECONDS
    last_status = None
    submission_id = str(context.get("idempotency_key", "")).removeprefix("host-update:")
    while True:
        try:
            _, operation = _controller_call(
                controller_url, secret, "GET", f"/v1/operations/{operation_id}"
            )
        except ControllerUnreachableError as exc:
            # A status-read outage is not a failed update: the controller
            # keeps its durable record, so keep observing until the deadline.
            operation = {"status": last_status}
            print(f"Controller status unavailable; retrying: {exc}", flush=True)
        status = operation.get("status")
        if status != last_status:
            print(f"Controller operation {operation_id}: {status}", flush=True)
            last_status = status
        if status == "succeeded":
            installed = (operation.get("installed") or {}).get("image", "")
            print(f"Update installed: {installed}", flush=True)
            return 0
        if status == "partially_verified":
            print(
                "Update installed with explicit verification gaps: "
                f"{_redact_diagnostics(json.dumps(operation.get('verification', [])))}",
                flush=True,
            )
            return 2
        if status == "failed":
            raise RuntimeError(
                f"Controller operation {operation_id} failed: "
                f"{_redact_diagnostics(operation.get('errorSummary', 'unknown error'))}. "
                "Retry it from Settings Operations or rerun this command for a "
                "new attempt; the original error stays in its record."
            )
        if status == "superseded":
            raise RuntimeError(
                f"Controller operation {operation_id} was superseded: "
                f"{_redact_diagnostics(operation.get('supersededReason', ''))}"
            )
        if time.time() > deadline:
            raise RuntimeError(
                f"Controller operation {operation_id} did not finish within "
                f"{_CONTROLLER_POLL_TIMEOUT_SECONDS}s; reattach with "
                f"--resume {submission_id} or inspect the "
                "controller operation directly."
            )
        _sleep(_CONTROLLER_POLL_INTERVAL_SECONDS)


@contextmanager
def _legacy_transport_lease(command, repo, env):
    """Hold the updater's existing kernel lock independently of its proxy.

    The trusted rendered deployment-control service supplies its state mount and
    lock directory. Its stdin lifetime holds the same lease used by release
    jobs, including legacy-owner protection; the host needs no lock algorithm
    or Docker socket mounted into another container.
    """
    name = "moonmind-transport-lease-" + uuid.uuid4().hex
    script = (
        "import asyncio, os, sys\n"
        "from moonmind.workflows.skills.deployment_execution import "
        "FileDeploymentUpdateLockManager\n"
        "async def hold():\n"
        "    manager = FileDeploymentUpdateLockManager("
        "os.environ.get('MOONMIND_DEPLOYMENT_LOCK_DIR') or "
        "'/workspace/deployment_state/locks')\n"
        "    async with await manager.acquire('moonmind'):\n"
        f"        print({_LEGACY_TRANSPORT_LEASE_READY!r}, flush=True)\n"
        "        sys.stdin.buffer.read()\n"
        "asyncio.run(hold())\n"
    )
    holder = [
        *command,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        name,
        "--entrypoint",
        "python",
        "temporal-worker-deployment-control",
        "-u",
        "-c",
        script,
    ]
    # File polling works for attached child output on Windows and Linux;
    # select() on a subprocess pipe does not work on Windows.
    with tempfile.TemporaryDirectory(prefix="moonmind-transport-lease-") as temp:
        log = Path(temp) / "holder.log"
        with log.open("wb") as output:
            try:
                process = subprocess.Popen(
                    holder,
                    cwd=repo,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                raise RuntimeError(
                    "Shared deployment lock holder could not start: "
                    + _redact_diagnostics(str(exc))
                ) from None
            try:
                deadline = time.monotonic() + _LEGACY_TRANSPORT_LEASE_TIMEOUT_SECONDS
                while True:
                    diagnostic = log.read_text(errors="replace")
                    if (
                        _LEGACY_TRANSPORT_LEASE_READY in diagnostic.splitlines()
                        and process.poll() is None
                    ):
                        break
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Shared deployment lock was not acquired before proxy repair.\n"
                            + _redact_diagnostics(diagnostic)[-_MAX_DIAGNOSTIC_CHARS:]
                        )
                    _sleep(0.1)

                def held():
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Shared deployment lock holder exited before proxy repair.\n"
                            + _redact_diagnostics(log.read_text(errors="replace"))[
                                -_MAX_DIAGNOSTIC_CHARS:
                            ]
                        )

                yield held
            finally:
                # EOF releases the kernel lease before the release handoff.
                # If that acknowledgment is lost, stop only our named holder.
                primary_error = sys.exc_info()[0] is not None
                status = None
                try:
                    try:
                        process.stdin.close()
                    except OSError:
                        # A broken pipe can mean the holder already exited;
                        # its exit or container removal still needs observing.
                        pass
                    try:
                        status = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        status = None
                    if status != 0:
                        try:
                            result = subprocess.run(
                                ["docker", "rm", "--force", name],
                                cwd=repo,
                                env=env,
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                            if result.returncode and "no such container" not in (
                                f"{result.stdout or ''}\n{result.stderr or ''}".lower()
                            ):
                                raise RuntimeError(
                                    _redact_diagnostics(
                                        f"{result.stdout or ''}\n{result.stderr or ''}"
                                    ).strip()[-_MAX_DIAGNOSTIC_CHARS:]
                                    or "Docker did not confirm lock holder removal"
                                )
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                    message = (
                        "Shared deployment lock holder cleanup unavailable: "
                        + _redact_diagnostics(str(exc))
                    )
                    if not primary_error:
                        raise RuntimeError(message) from None
                    print(message, flush=True)
                if status not in (None, 0):
                    message = (
                        f"Shared deployment lock holder exited with status {status}.\n"
                        + _redact_diagnostics(log.read_text(errors="replace"))[
                            -_MAX_DIAGNOSTIC_CHARS:
                        ]
                    )
                    if not primary_error:
                        raise RuntimeError(message)
                    print(message, flush=True)


def _ensure_legacy_docker_transport(command, repo, env):
    service = "temporal-worker-deployment-control"
    rendered = json.loads(
        run([*command, "config", "--format", "json"], cwd=repo, env=env)
    )
    worker_image = rendered.get("services", {}).get(service, {}).get("image")
    # The target image was already acquired and verified by the host. A
    # distinct worker image retains its deployment-owned acquisition policy,
    # platform and build configuration through Compose, before probes disable
    # registry access. The client version command needs no Docker API access.
    if worker_image and worker_image != env.get("MOONMIND_IMAGE"):
        run(
            [
                *command,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "docker",
                service,
                "--version",
            ],
            cwd=repo,
            env=env,
        )
    # Compose run lacks --pull on supported older V2 releases, so an overlay
    # disables pulls without altering the deployment-owned service boundary.
    with tempfile.TemporaryDirectory(prefix="moonmind-transport-policy-") as temp:
        path = Path(temp) / "compose.json"
        path.write_text(
            json.dumps(
                {
                    "services": {
                        "temporal-worker-deployment-control": {"pull_policy": "never"},
                    }
                }
            )
        )
        _check_legacy_docker_transport([*command, "-f", str(path)], repo, env)


def _check_legacy_docker_transport(command, repo, env):
    """Prove the child transport, repairing only its unavailable proxy.

    Host Docker access does not prove that a Compose one-off can reach Docker.
    In particular, a stopped Desktop proxy can retain a stale WSL socket bind.
    Keep a working proxy intact; try starting it before one bounded recreation
    refreshes its mounts and network from the deployment-owned configuration.
    """
    service = "temporal-worker-deployment-control"
    rendered = json.loads(
        run([*command, "config", "--format", "json"], cwd=repo, env=env)
    )
    configured = rendered.get("services", {})
    endpoint = configured.get(service, {}).get("environment", {}).get("DOCKER_HOST", "")
    probe = [
        *command,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "docker",
        service,
        "info",
        "--format",
        "{{.ServerVersion}}",
    ]
    errors = []
    transport_failed = False
    probe_timed_out = False

    def attempt(args, phase, *, timeout=30):
        nonlocal transport_failed, probe_timed_out
        if phase == "Docker access probe":
            transport_failed = probe_timed_out = False
        try:
            result = subprocess.run(
                args, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            detail = f"{phase} timed out after {timeout} seconds"
            probe_timed_out = phase == "Docker access probe"
        else:
            if result.returncode == 0 and (
                phase != "Docker access probe" or result.stdout.strip()
            ):
                return True
            if phase == "Docker access probe":
                diagnostic = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
                # Compose image-pull/start errors must not authorize proxy
                # replacement. Identify the child's Docker endpoint or its
                # direct API error, rather than generic registry DNS/503 text.
                transport_failed = (
                    (
                        "lookup docker-proxy" in diagnostic
                        and "no such host" in diagnostic
                    )
                    or (
                        "docker-proxy:" in diagnostic
                        and any(
                            marker in diagnostic
                            for marker in (
                                "failed to connect to the docker api",
                                "cannot connect to the docker daemon",
                                "connection refused",
                                "connection reset by peer",
                            )
                        )
                    )
                    or bool(
                        re.search(r"error response from daemon:\s*503\b", diagnostic)
                    )
                    or (
                        result.returncode == 0
                        and "503 service unavailable" in diagnostic
                    )
                )
            detail = _redact_diagnostics(
                f"{phase} failed (exit {result.returncode}): "
                f"{result.stdout or ''}\n{result.stderr or ''}"
            ).strip()[-_MAX_DIAGNOSTIC_CHARS:]
        errors.append(detail)
        print(detail, flush=True)
        return False

    def ready():
        for index in range(3):
            if index:
                _sleep(1)
            if attempt(probe, "Docker access probe"):
                return True
        return False

    def fail():
        raise RuntimeError(
            "Updater Docker transport is unavailable; release handoff did not start.\n"
            + "\n".join(errors)
        )

    if attempt(probe, "Docker access probe"):
        return
    # A timeout alone does not prove proxy failure. Reconcile it, and never
    # repair the proxy for a one-off's own image, mount, or startup error.
    if probe_timed_out and ready():
        return
    if not transport_failed:
        fail()
    # An explicitly configured external/socket endpoint retains its authority.
    # Starting the local proxy cannot repair it and must not replace it.
    if (
        urlsplit(endpoint or "").hostname == "docker-proxy"
        and "docker-proxy" in configured
    ):
        try:
            with _legacy_transport_lease(command, repo, env) as held:
                # Another updater may have restored it while we acquired the
                # shared lock. Reconcile before any host mutation.
                if attempt(probe, "Docker access probe"):
                    held()
                    return
                if transport_failed:
                    print(
                        "Restoring the updater's Docker proxy before release handoff.",
                        flush=True,
                    )
                    repair = [
                        *command,
                        "up",
                        "-d",
                        "--no-deps",
                        "--no-build",
                        "--pull",
                        "missing",
                    ]
                    held()
                    attempt(
                        [*repair, "--no-recreate", "docker-proxy"],
                        "Start Docker proxy",
                        timeout=60,
                    )
                    held()
                    # A failed acknowledgment can still have started it.
                    if ready():
                        held()
                        return
                    if transport_failed:
                        print(
                            "Docker proxy is still unavailable; recreating only that service once.",
                            flush=True,
                        )
                        held()
                        attempt(
                            [*repair, "--force-recreate", "docker-proxy"],
                            "Recreate Docker proxy",
                            timeout=60,
                        )
                        held()
                        if ready():
                            held()
                            return
        except RuntimeError as exc:
            errors.append(_redact_diagnostics(str(exc)))
    fail()


def _submit_legacy_direct(record, repo):
    """Transitional escape hatch: the old application-owned updater container.

    Establishes the target image's container Docker transport from the host
    before handing off. Prefer the independently owned standalone controller.
    """
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
        # Propagate the deployment's selected Compose file set (the same
        # resolution the controller path carries). The release image already
        # supplies the base file, so only the additional selected files are
        # layered here; omitting them would reconcile without custom services
        # while `--remove-orphans` may remove them.
        compose_selection = os.environ.get("COMPOSE_FILE", "")
        if compose_selection.strip():
            for name in _resolve_compose_files(repo):
                if name in ("docker-compose.yaml", "docker-compose.yml"):
                    continue
                command.extend(["-f", str(repo / name)])
        else:
            for name in ("docker-compose.override.yaml", "docker-compose.override.yml"):
                override = repo / name
                if override.exists():
                    command.extend(["-f", str(override)])
                    break
        env = {
            **os.environ,
            "MOONMIND_IMAGE": record["image"],
            "MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES": "docker-proxy,sandbox-egress-proxy,postgres",
        }
        _ensure_legacy_docker_transport(command, repo, env)
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
        return subprocess.run(
            command,
            cwd=repo,
            env=env,
            check=False,
        ).returncode


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


def cli(argv=None):
    """Report expected operational failures without a Python traceback."""
    try:
        return main(argv)
    except (RuntimeError, ValueError) as exc:
        print(f"Update failed: {_redact_diagnostics(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
