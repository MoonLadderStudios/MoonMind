#!/usr/bin/env python3
"""Materialize published Omnigent release-support evidence for a deployed commit.

Source issue: MoonLadderStudios/MoonMind#3710.

The Omnigent catalog reports release-support status from three published
documents: the #3508 browser acceptance manifest, the Tier-1 exact
deployable-artifact projection, and the protected-live readiness projection.
CI publishes each one as a GitHub Actions run artifact; this tool copies the
newest unexpired document *for the deployed commit* into the durable evidence
directory Compose bind-mounts read-only at ``/workspace/omnigent-evidence``.

Because the destination filenames are the Compose defaults, an operator runs

    python tools/materialize_omnigent_evidence.py --commit "$DEPLOYED_COMMIT"

and the deployed API consumes the evidence with no further configuration. A
document that is missing, expired, or published for a different commit is left
unwritten, so the catalog keeps reporting its fail-closed support reason rather
than reading stale authority.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_EVIDENCE_DIR = REPO_ROOT / "var/omnigent-evidence"
DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"


class EvidenceSource:
    """One published evidence document and where it lands on disk."""

    def __init__(
        self,
        *,
        key: str,
        workflow: str,
        artifact_prefix: str,
        member: str,
        destination: str,
        commit_field: str,
    ) -> None:
        self.key = key
        self.workflow = workflow
        self.artifact_prefix = artifact_prefix
        self.member = member
        self.destination = destination
        self.commit_field = commit_field


# The three documents the catalog reads, in the order they are reported.
EVIDENCE_SOURCES: tuple[EvidenceSource, ...] = (
    EvidenceSource(
        key="acceptance",
        workflow="omnigent-live-conformance.yml",
        artifact_prefix="omnigent-live-published-matrix-",
        member="published-matrix.json",
        destination="acceptance-manifest.json",
        commit_field="sourceCommit",
    ),
    EvidenceSource(
        key="exactArtifact",
        workflow="pytest-unit-tests.yml",
        artifact_prefix="omnigent-exact-artifact-",
        member="projection.json",
        destination="exact-artifact-projection.json",
        commit_field="sourceCommit",
    ),
    EvidenceSource(
        key="liveHealth",
        workflow="omnigent-live-verification-health.yml",
        artifact_prefix="omnigent-live-health-",
        member="projection.json",
        destination="live-health-projection.json",
        commit_field="deployedCommit",
    ),
)


def select_artifact(
    artifacts: Sequence[Mapping[str, Any]], *, prefix: str
) -> Mapping[str, Any] | None:
    """Return the newest unexpired artifact whose name starts with ``prefix``."""

    candidates = [
        artifact
        for artifact in artifacts
        if str(artifact.get("name", "")).startswith(prefix)
        and not artifact.get("expired")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda artifact: (
            str(artifact.get("created_at") or ""),
            int(artifact.get("id") or 0),
        ),
    )


def select_runs_for_commit(
    runs: Sequence[Mapping[str, Any]], *, commit: str
) -> list[Mapping[str, Any]]:
    """Return successful runs for ``commit``, newest attempt first.

    Evidence for a different commit is not evidence for the deployed artifact,
    so a run for another ``head_sha`` is never a candidate.
    """

    matching = [
        run
        for run in runs
        if str(run.get("head_sha", "")) == commit
        and str(run.get("status", "")).strip().lower() == "completed"
        and str(run.get("conclusion", "")).strip().lower() == "success"
    ]
    return sorted(
        matching,
        key=lambda run: (str(run.get("updated_at") or ""), int(run.get("id") or 0)),
        reverse=True,
    )


def read_member(download_dir: Path, member: str) -> Mapping[str, Any] | None:
    """Read ``member`` from an extracted artifact directory."""

    for candidate in sorted(download_dir.rglob(member)):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            return payload
    return None


def _gh_api(repository: str, path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _download(repository: str, run_id: Any, artifact_name: str, member: str):
    with tempfile.TemporaryDirectory() as raw_dir:
        download_dir = Path(raw_dir)
        subprocess.run(
            [
                "gh", "run", "download", str(run_id),
                "--repo", repository,
                "--name", artifact_name,
                "--dir", str(download_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return read_member(download_dir, member)


def materialize_source(
    source: EvidenceSource,
    *,
    repository: str,
    commit: str,
    destination_dir: Path,
) -> dict[str, Any]:
    """Materialize one evidence document, reporting the observed outcome."""

    try:
        runs = _gh_api(
            repository,
            f"actions/workflows/{source.workflow}/runs?per_page=50",
        ).get("workflow_runs", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return {"key": source.key, "status": "unavailable", "detail": str(exc)[:200]}

    for run in select_runs_for_commit(runs, commit=commit):
        try:
            artifacts = _gh_api(
                repository, f"actions/runs/{run['id']}/artifacts?per_page=100"
            ).get("artifacts", [])
            artifact = select_artifact(artifacts, prefix=source.artifact_prefix)
            if artifact is None:
                continue
            document = _download(
                repository, run["id"], str(artifact["name"]), source.member
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
            return {"key": source.key, "status": "unavailable", "detail": str(exc)[:200]}
        if document is None:
            continue
        observed_commit = str(document.get(source.commit_field, ""))
        if observed_commit != commit:
            # A published document that names another commit proves nothing
            # about the deployed artifact.
            continue
        destination = destination_dir / source.destination
        destination.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {
            "key": source.key,
            "status": "materialized",
            "path": str(destination),
            "runId": run["id"],
            "artifact": artifact["name"],
        }
    return {
        "key": source.key,
        "status": "missing",
        "detail": f"no unexpired {source.workflow} evidence for commit {commit[:12]}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        required=True,
        help="Deployed source commit the evidence must be published for.",
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DIR,
        help="Directory Compose bind-mounts at /workspace/omnigent-evidence.",
    )
    args = parser.parse_args(argv)

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    results = [
        materialize_source(
            source,
            repository=args.repository,
            commit=args.commit,
            destination_dir=args.evidence_dir,
        )
        for source in EVIDENCE_SOURCES
    ]
    print(json.dumps({"commit": args.commit, "results": results}, indent=2))
    materialized = sum(1 for r in results if r["status"] == "materialized")
    print(
        f"materialized {materialized}/{len(results)} evidence documents into "
        f"{args.evidence_dir}"
    )
    # Missing evidence is a reported, fail-closed state, not a tool failure: the
    # catalog keeps its support reason until the evidence exists.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
