"""Disposable Compose upgrade routing convergence using the selected controller.

This adapter does not run the updater API. It preserves the controller's local
operation/lock state and invokes its normal routing convergence after Compose
has installed the candidate workers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def validate_target(*, repo, project, image, compose_files):
    if not re.fullmatch(r"moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?", project):
        raise ValueError("cutover requires an isolated moonmind-test project")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("cutover requires the inspected candidate image ID")
    repo = Path(repo).resolve()
    marker = repo / ".first-run-journey-project"
    if (
        not repo.name.startswith("moonmind-upgrade-4356.")
        or not (repo / ".git").is_file()
        or not marker.is_file()
        or marker.read_text().strip() != project
    ):
        raise ValueError("cutover requires its owned disposable upgrade checkout")
    files = [Path(path).resolve() for path in compose_files]
    if (
        not files
        or files[0] != repo / "docker-compose.yaml"
        or not all(path.is_file() for path in files)
    ):
        raise ValueError("cutover requires the existing selected Compose files")
    return repo, [str(path) for path in files]


def run_cutover(*, repo, project, image, revision, compose_files, controller):
    repo, compose_files = validate_target(
        repo=repo, project=project, image=image, compose_files=compose_files
    )
    state = repo / "deploy/state/controller"
    target = {
        "project": project,
        "projectDir": str(repo),
        "composeFiles": list(compose_files),
    }
    identity = hashlib.sha256(
        json.dumps([project, image, revision, target], sort_keys=True).encode()
    ).hexdigest()[:24]
    store = controller.record_mod.OperationStore(state)
    with controller.lock_mod.StackLock(state, project).acquire():
        operation = store.begin(
            stack=project,
            desired_image=image,
            source_revision=revision,
            reason="Disposable Compose upgrade routing convergence",
            target=target,
            operation_id=f"journey-cutover-{identity}",
        )
        if operation["target"] != target or operation["desired"]["image"] != image:
            raise ValueError("existing cutover operation targets another deployment")
        overlay = controller.write_image_overlay(
            str(state), operation["operationId"], image
        )
        check = controller.converge_routing_cutover(
            store,
            operation,
            runner=controller.engine.subprocess_runner(),
            overlay=overlay,
        )
        receipt = store.routing_cutover(stack=project)
        return {
            "passed": bool(
                check
                and check.get("status") == "passed"
                and receipt
                and receipt.get("routingCutover", {}).get("status") == "completed"
            ),
            "check": check,
            "receipt": receipt,
            "operation": store.load(operation["operationId"]),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    parser.add_argument("-f", "--compose-file", action="append", required=True)
    args = parser.parse_args()
    validate_target(
        repo=args.repo,
        project=args.project,
        image=args.image,
        compose_files=args.compose_file,
    )
    # Resolve the actual selected checkout's controller, never the caller's.
    sys.path.insert(0, str(args.repo.resolve() / "deploy/controller"))
    import server as controller

    try:
        result = run_cutover(
            repo=args.repo,
            project=args.project,
            image=args.image,
            revision=args.revision,
            compose_files=args.compose_file,
            controller=controller,
        )
    except Exception as exc:
        result = {"passed": False, "error": controller.redact_text(str(exc))}
    args.receipt_file.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_file.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
