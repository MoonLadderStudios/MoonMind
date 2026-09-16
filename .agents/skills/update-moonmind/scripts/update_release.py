"""Portable host entrypoint for the image-owned MoonMind release controller.

Only standard-library Python, Git and Compose are required on the host. The
selected image owns deployment semantics, including canary, promotion and drain.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


def run(args, *, cwd, env=None):
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )
    if result.returncode:
        # Docker/Git diagnostics can contain registry or remote credentials.
        raise RuntimeError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}); deployment remains owned by its recorded release job"
        )
    return result.stdout.strip()


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
        revision = run(
            ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"], cwd=repo
        )
        image = f"{args.image_repository}:sha-{revision}"
        run(["docker", "pull", image], cwd=repo)
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
        record = {
            "repo": str(repo),
            "project": project,
            "image": digests[0],
            "inputs": {
                "stack": "moonmind",
                "image": {
                    "repository": args.image_repository,
                    "reference": digests[0].split("@", 1)[1],
                },
                "sourceRevision": revision,
                "reason": "Update to selected branch snapshot",
            },
            "context": {
                "idempotency_key": f"host-update:{submission_id}",
                "operator": "local-operator",
                "operator_role": "operator",
                **({"deployment_operator_urls": args.operator_url} if args.operator_url else {}),
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
            env={**os.environ, "MOONMIND_IMAGE": record["image"]},
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


if __name__ == "__main__":
    raise SystemExit(main())
