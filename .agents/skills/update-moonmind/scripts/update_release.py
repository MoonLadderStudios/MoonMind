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
        "--image-repository", default="ghcr.io/moonladderstudios/moonmind"
    )
    parser.add_argument(
        "--resume", help="Resume the printed submission ID using its original inputs"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo.resolve(strict=True)
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


if __name__ == "__main__":
    raise SystemExit(main())
