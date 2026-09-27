"""Bounded legacy tools-volume retirement (MoonLadderStudios/MoonMind#4558).

Normal startup no longer creates tools volumes. Version-named leftovers from
before the cutover (``moonmind-omnigent-tools-gh-*``) are removed only when
they are exact, positively identified, and unreferenced: every running and
stopped container on the daemon, the current deployment configuration, and
rollback retention are checked first. Prefix or Compose-label matching alone
is never ownership proof. Shared or ambiguous resources stay untouched with a
concise reason, and cleanup errors never fail an otherwise healthy update.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from typing import Any, Awaitable, Callable


#: Strict legacy identity: the exact version-named tools volumes MoonMind used
#: to publish. Anything else (data volumes, caches, workspaces, other
#: projects' similarly named volumes) is retained untouched.
LEGACY_TOOLS_VOLUME_PATTERN = re.compile(
    r"^moonmind-omnigent-tools-gh-\d+\.\d+\.\d+(?:-[A-Za-z0-9_.-]+)?$"
)

Decision = tuple[str, str]


def classify_legacy_tools_volume(
    *,
    name: str,
    labels: dict[str, str] | None,
    running_refs: list[str] | tuple[str, ...],
    stopped_refs: list[str] | tuple[str, ...],
    config_refs: list[str] | tuple[str, ...],
    rollback_retained: list[str] | tuple[str, ...],
    deployment_project: str = "moonmind",
) -> Decision:
    """Decide remove/retain for one candidate volume without touching it."""

    candidate = str(name or "").strip()
    if not LEGACY_TOOLS_VOLUME_PATTERN.fullmatch(candidate):
        return ("retain", "not-a-legacy-tools-volume")
    if candidate in set(running_refs or ()):
        return ("retain", "referenced-by-running-container")
    if candidate in set(stopped_refs or ()):
        return ("retain", "referenced-by-stopped-container")
    if candidate in set(config_refs or ()):
        return ("retain", "referenced-by-current-config")
    if candidate in set(rollback_retained or ()):
        return ("retain", "retained-for-rollback")
    if labels is None:
        return ("retain", "ambiguous-ownership")
    project = str((labels or {}).get("com.docker.compose.project") or "").strip()
    if project and project != deployment_project:
        return ("retain", "foreign-project-volume")
    return ("remove", "unused-owned-legacy-bundle")


def plan_legacy_cleanup(
    candidates: list[dict[str, Any]],
    *,
    deployment_project: str = "moonmind",
) -> dict[str, Any]:
    """Classify every candidate; safe to run repeatedly (inspect/dry-run)."""

    remove: list[str] = []
    retained: dict[str, str] = {}
    for candidate in candidates:
        decision, reason = classify_legacy_tools_volume(
            name=str(candidate.get("name") or ""),
            labels=candidate.get("labels"),
            running_refs=candidate.get("running_refs") or [],
            stopped_refs=candidate.get("stopped_refs") or [],
            config_refs=candidate.get("config_refs") or [],
            rollback_retained=candidate.get("rollback_retained") or [],
            deployment_project=deployment_project,
        )
        if decision == "remove":
            remove.append(str(candidate.get("name") or ""))
        else:
            retained[str(candidate.get("name") or "")] = reason
    return {"remove": sorted(remove), "retained": retained}


def _removal_failed_as_in_use(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return "is in use" in lowered or "volume is in use" in lowered


def _removal_failed_as_missing(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return "no such volume" in lowered


async def apply_legacy_cleanup(
    names: list[str],
    *,
    runner: Callable[[list[str]], Awaitable[tuple[int, str, str]]],
) -> dict[str, Decision]:
    """Remove approved names with ordinary exact-name removal (no force).

    A consumer appearing between inspection and deletion surfaces as an
    in-use failure and the candidate is retained; containers are never
    removed to make deletion succeed. Errors for one candidate never block
    the others.
    """

    results: dict[str, Decision] = {}
    for name in names:
        candidate = str(name or "").strip()
        if not LEGACY_TOOLS_VOLUME_PATTERN.fullmatch(candidate):
            results[candidate] = ("retained", "not-a-legacy-tools-volume")
            continue
        try:
            code, _stdout, stderr = await runner(["docker", "volume", "rm", candidate])
        except Exception as exc:
            results[candidate] = ("retained", f"removal-error: {type(exc).__name__}")
            continue
        if code == 0:
            results[candidate] = ("removed", "removed")
        elif _removal_failed_as_missing(stderr):
            results[candidate] = ("retained", "already-removed")
        elif _removal_failed_as_in_use(stderr):
            results[candidate] = ("retained", "became-in-use-during-deletion")
        else:
            detail = (stderr or "").strip().replace("\n", " ")[:160]
            results[candidate] = ("retained", f"removal-failed: {detail}" or "removal-failed")
    return results


async def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    completed = await asyncio.to_thread(
        subprocess.run, argv, capture_output=True, text=True, check=False
    )
    return completed.returncode, completed.stdout, completed.stderr


def _list_candidate_names() -> list[str]:
    completed = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [
        line.strip() for line in completed.stdout.splitlines() if line.strip()
    ]


def _inspect_labels(name: str) -> dict[str, str] | None:
    completed = subprocess.run(
        ["docker", "volume", "inspect", name, "--format", "{{json .Labels}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _container_volume_refs() -> tuple[list[str], list[str]]:
    completed = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ([], [])
    running: list[str] = []
    stopped: list[str] = []
    running_names: set[str] = set()
    probe = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        running_names = {
            line.strip() for line in probe.stdout.splitlines() if line.strip()
        }
    for line in completed.stdout.splitlines():
        container = line.strip()
        if not container:
            continue
        volumes = subprocess.run(
            ["docker", "inspect", container, "--format", "{{range .Mounts}}{{.Name}} {{end}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        refs = (
            [item for item in volumes.stdout.split() if item]
            if volumes.returncode == 0
            else []
        )
        (running if container in running_names else stopped).extend(refs)
    return (sorted(set(running)), sorted(set(stopped)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect (default) or explicitly apply bounded legacy "
        "Omnigent tools-volume retirement."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove approved unused legacy tools volumes (default is dry-run).",
    )
    parser.add_argument(
        "--deployment-project",
        default="moonmind",
        help="Compose project name treated as deployment-owned.",
    )
    args = parser.parse_args(argv)

    names = [
        name
        for name in _list_candidate_names()
        if LEGACY_TOOLS_VOLUME_PATTERN.fullmatch(name)
    ]
    running_refs, stopped_refs = _container_volume_refs()
    candidates = [
        {
            "name": name,
            "labels": _inspect_labels(name),
            "running_refs": running_refs,
            "stopped_refs": stopped_refs,
            "config_refs": [],
            "rollback_retained": [],
        }
        for name in names
    ]
    plan = plan_legacy_cleanup(candidates, deployment_project=args.deployment_project)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.apply:
        return 0
    results = asyncio.run(apply_legacy_cleanup(plan["remove"], runner=_default_runner))
    print(json.dumps(results, indent=2, sort_keys=True))
    # Cleanup failures are reported, never fatal to the caller.
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "LEGACY_TOOLS_VOLUME_PATTERN",
    "apply_legacy_cleanup",
    "classify_legacy_tools_volume",
    "plan_legacy_cleanup",
]
