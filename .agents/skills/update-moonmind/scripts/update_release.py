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
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


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
                        **_controller_plan(repo, _controller_image(record)),
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
                        **_controller_plan(
                            repo,
                            _controller_image_for(
                                args.image_repository, "<fetched-commit>"
                            ),
                        ),
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
        controller_url_explicit=(
            bool(os.environ.get("MOONMIND_CONTROLLER_URL"))
            or any(
                arg == "--controller-url" or arg.startswith("--controller-url=")
                for arg in requested_args
            )
        ),
    )


def _submit_release(
    record,
    repo,
    *,
    controller_url,
    secret_file,
    controller_url_explicit=False,
):
    """Route the recorded submission to the deployment's standalone controller.

    The controller is the only updater. A deployment without one installs
    and starts it through its own bootstrap first; a controller that cannot
    be installed fails the update instead of launching another writer. An
    installed controller that owns recorded work or a container keeps its
    recovery authority: it is restored, never replaced around.
    """
    explicit_controller = bool(
        secret_file
        or os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE")
        or controller_url_explicit
    )
    if not explicit_controller:
        default_secret = _default_controller_secret_file(repo)
        if not default_secret.exists():
            _install_controller(record, repo)
        else:
            secret = default_secret.read_text(encoding="utf-8").strip()
            try:
                _controller_call(
                    _installed_controller_url(repo, controller_url),
                    secret,
                    "GET",
                    "/v1/healthz",
                    timeout=5,
                )
            except ControllerUnreachableError:
                # Bootstrap may have written a secret and Compose file before
                # its image could start. Only a controller that never recorded
                # an operation and owns no container is installed again.
                if not _controller_never_started(repo):
                    raise
                _install_controller(record, repo)
    if not controller_url_explicit:
        controller_url = _installed_controller_url(repo, controller_url)
    return _submit_via_controller(
        record,
        repo,
        controller_url=controller_url,
        secret_file=secret_file,
    )


def _installed_controller_url(repo, default_url):
    """Return the loopback endpoint recorded by this deployment's bootstrap."""
    identity = _controller_identity(repo)
    port = identity.get("port") if identity else None
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return f"http://127.0.0.1:{port}"
    return default_url


def _controller_image_for(image_repository, revision):
    """Name the controller image published with a release revision.

    The release workflow publishes ``<app repository>-controller`` with the
    same ``sha-<commit>`` tag as the application image, so the controller
    follows the selected release. ``MOONMIND_CONTROLLER_IMAGE`` overrides it.
    """
    explicit = os.environ.get("MOONMIND_CONTROLLER_IMAGE", "").strip()
    if explicit:
        return explicit
    return f"{image_repository}-controller:sha-{revision}"


def _controller_image(record):
    inputs = record.get("inputs") or {}
    image = inputs.get("image") or {}
    repository = image.get("repository") or str(record.get("image", "")).split("@", 1)[0]
    return _controller_image_for(repository, inputs.get("sourceRevision") or "latest")


def _controller_plan(repo, controller_image):
    """Describe the controller route without creating controller state."""
    return {
        "executionOwner": "standalone-controller",
        "controllerInstalled": bool(
            os.environ.get("MOONMIND_CONTROLLER_URL")
            or _default_controller_secret_file(repo).exists()
        ),
        "controllerImageSource": controller_image,
    }


def _install_controller(record, repo):
    """Install and start this deployment's controller through its bootstrap.

    The deployment checkout's ``deploy/controller/bootstrap.py`` owns the
    controller lifecycle: it creates the deployment-owned secret, derives the
    project, endpoint, and API link network, pins the image to a digest, and
    starts the separate Compose project. Its output streams to the operator.
    """
    bootstrap = repo / "deploy" / "controller" / "bootstrap.py"
    if not bootstrap.is_file():
        raise RuntimeError(
            f"This checkout has no controller bootstrap at {bootstrap}; the "
            "standalone controller cannot be installed and no other updater "
            "was started."
        )
    image = _controller_image(record)
    print(
        f"Standalone controller is not installed; installing {image}.",
        flush=True,
    )
    for command in ("install", "start"):
        result = subprocess.run(
            [
                sys.executable,
                str(bootstrap),
                command,
                "--repo",
                str(repo),
                "--image",
                image,
                "--target-project",
                record["project"],
            ],
            cwd=repo,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Controller {command} failed (exit {result.returncode}) for "
                f"{image}; no other updater was started. Resolve the "
                "bootstrap error above (for example an unpublished or "
                "unreadable controller image), then rerun this command; "
                "MOONMIND_CONTROLLER_IMAGE selects another controller image."
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
    # The controller is a private loopback endpoint guarded by a bearer
    # secret: an ambient HTTP(S) proxy must neither see nor reroute it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
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
