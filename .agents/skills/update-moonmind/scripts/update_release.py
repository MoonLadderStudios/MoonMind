"""Portable host entrypoint for the image-owned MoonMind release controller.

Only standard-library Python, Git and Compose are required on the host. The
selected image owns deployment semantics, including canary, promotion and drain.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
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
    # Replacement owner first: the standalone controller in its own Compose
    # project (MoonLadderStudios/MoonMind#4500). Its Docker transport and
    # command endpoint survive target-project shutdown, unlike the legacy
    # application-owned control service below. The legacy path is retained
    # only until the old writer is positively stopped/reconciled.
    controller_rc = _try_controller_handoff(record=record)
    if controller_rc is not None:
        return controller_rc
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
        return subprocess.run(
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


def _try_controller_handoff(*, record):
    """Submit trusted release data to the standalone controller if configured.

    Returns the controller exit code on a handled submission, else None to
    fall through to the legacy application-owned path. A controller failure
    falls back with an explicit diagnostic; the installation is unchanged.
    """
    try:
        from moonmind_controller import client
    except ImportError:
        return None
    if not client.is_controller_configured():
        return None
    payload = client.build_operation_payload(
        operation_id=str(
            record["context"].get("idempotency_key") or f"host-update:{record['image']}"
        ),
        target_image=record["image"],
        services=("api", "worker"),
    )
    try:
        receipt = client.submit_operation(payload)
    except Exception as exc:
        print(
            f"Standalone controller unavailable ({exc}); falling back to the "
            "legacy application-owned updater until cutover completes.",
            flush=True,
        )
        return None
    print(f"Controller operation: {receipt} (standalone replacement owner)", flush=True)
    return 0


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
