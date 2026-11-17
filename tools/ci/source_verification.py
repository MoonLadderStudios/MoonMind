"""Source evidence for a release: its own source phase receipt, or validated
pre-merge source evidence for the identical tree."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "moonmind.source-verification/v2"
SOURCE_OWNERS = (
    "unit_fast",
    "unit_slow",
    "api_component",
    "temporal_boundary",
    "integration_hermetic",
    "reliability_journey",
    "omnigent_conformance",
    "frontend_static",
    "frontend_browser_chromium",
    "frontend_browser_firefox",
    "generated_contracts",
)
# Pull requests select owners by changed path. Only a run that selected
# everything the release source phase runs can replace it; the full backend
# group also selects shard ownership and full-source Omnigent conformance.
REUSE_SELECTION = SOURCE_OWNERS + ("full_backend", "full_frontend")
REUSE_WORKFLOW = ".github/workflows/pytest-unit-tests.yml"
# These events run with default workflow inputs; manual and scheduled runs can
# carry other inputs or event-specific behavior.
REUSE_EVENTS = ("pull_request", "merge_group")
# Runner images and unlocked test dependencies drift outside the tree. The
# nightly full suite covers one day of drift; older evidence falls back.
REUSE_MAX_AGE = timedelta(hours=24)
REUSE_SCHEMA = "moonmind.source-reuse/v1"


def artifact_attempt(
    name: str, *, prefix: str, run_id: str, current_attempt: str
) -> str:
    """Admit a retained successful job only within this immutable Actions run."""
    match = re.fullmatch(
        re.escape(prefix + "-" + run_id + "-") + r"([1-9][0-9]*)", name
    )
    if (
        not match
        or not current_attempt.isdecimal()
        or int(match[1]) > int(current_attempt)
    ):
        raise ValueError("Artifact does not belong to an admitted attempt of this run")
    return match[1]


def content_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def source_receipt(
    *, revision: str, run_id: str, attempt: str, selection: dict, result: str
) -> dict:
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not run_id.isdecimal()
        or not attempt.isdecimal()
    ):
        raise ValueError("Missing immutable source/run identity")
    if result != "success" or any(
        selection.get(key) != "true" for key in SOURCE_OWNERS
    ):
        raise ValueError("Complete source verification did not succeed")
    return {
        "schema": SCHEMA,
        "revision": revision,
        "runId": run_id,
        "attempt": attempt,
        "status": "passed",
        "owners": list(SOURCE_OWNERS),
        "selection": selection,
    }


def validate_source_receipt(
    payload: dict, *, revision: str, run_id: str, attempt: str, selection: dict
) -> None:
    """Admit only this run's complete receipt for the selection it admitted."""
    expected = source_receipt(
        revision=revision,
        run_id=run_id,
        attempt=attempt,
        selection=selection,
        result="success",
    )
    if payload != expected:
        raise ValueError(
            "Source verification receipt is incomplete or belongs to another "
            "source/run/attempt/selection"
        )


def validate_reused_receipt(
    payload: dict, *, revision: str, run_id: str, attempt: str
) -> None:
    """Admit a pre-merge receipt only when it covers the release source phase."""
    selection = payload.get("selection")
    if not isinstance(selection, dict) or any(
        selection.get(key) != "true" for key in REUSE_SELECTION
    ):
        raise ValueError("Selection does not cover every release source owner")
    validate_source_receipt(
        payload, revision=revision, run_id=run_id, attempt=attempt, selection=selection
    )


def _github_api(path: str) -> bytes:
    """Read one REST resource with the job token; gh follows artifact redirects."""
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, timeout=120, check=False
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()[:300]
        raise OSError(f"GitHub API {path} failed: {detail}")
    return result.stdout


def _admit_producer(
    api: Callable[[str], bytes],
    run_id: str,
    *,
    repository: str,
    tree: Callable[[str], str],
    release_tree: str,
    now: datetime,
) -> tuple[dict, bytes]:
    """Return the identity and receipt bytes of one trusted complete run.

    Identity comes from the run's own Actions resource, which, unlike the run
    listing, documents referenced workflows; the receipt must agree with it.
    """
    run = json.loads(api(f"repos/{repository}/actions/runs/{run_id}"))
    attempt = str(run["run_attempt"])
    if run.get("path") != REUSE_WORKFLOW or run.get("event") not in REUSE_EVENTS:
        raise ValueError(
            f"{run.get('event')} run of {run.get('path')} is not trusted "
            "pre-merge source verification"
        )
    if (run.get("head_repository") or {}).get("full_name") != repository:
        raise ValueError("Tested head is not in this repository")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError(
            f"Latest attempt {attempt} is {run.get('status')}/{run.get('conclusion')}"
        )
    started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
    if not timedelta(0) <= now - started <= REUSE_MAX_AGE:
        raise ValueError(
            f"Evidence from {run['run_started_at']} is outside the reuse window"
        )
    if tree(run["head_sha"]) != release_tree:
        raise ValueError(f"Tested tree of {run['head_sha']} differs from the release")
    # A pull request run takes its workflow definition from the test merge
    # commit, which the API exposes through local reusable workflow calls.
    workflows = {
        ref.get("sha")
        for ref in run.get("referenced_workflows") or []
        if str(ref.get("path", "")).startswith(repository + "/")
    }
    workflow = workflows.pop() if len(workflows) == 1 else ""
    if not workflow or tree(workflow) != release_tree:
        raise ValueError("Workflow definitions are unknown or differ from the release")
    name = f"source-verification-{run_id}-{attempt}"
    listed = json.loads(
        api(f"repos/{repository}/actions/runs/{run_id}/artifacts?name={name}")
    )
    artifacts = [item for item in listed.get("artifacts", []) if item["name"] == name]
    if len(artifacts) != 1 or artifacts[0].get("expired"):
        raise ValueError(f"Receipt {name} of the latest attempt is missing or expired")
    artifact = artifacts[0]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest"))):
        raise ValueError(f"Receipt {name} has no recorded digest")
    archive = api(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip")
    if content_digest(archive) != artifact["digest"]:
        raise ValueError(f"Receipt {name} does not match its recorded digest")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        if bundle.namelist() != ["receipt.json"]:
            raise ValueError(f"Receipt {name} does not contain exactly one receipt")
        data = bundle.read("receipt.json")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError(f"Receipt {name} is not an object")
    validate_reused_receipt(
        payload, revision=run["head_sha"], run_id=run_id, attempt=attempt
    )
    return {
        "repository": repository,
        "event": run["event"],
        "runId": run_id,
        "attempt": attempt,
        "url": run.get("html_url", ""),
        "revision": run["head_sha"],
        "tree": release_tree,
        "workflowCommit": workflow,
        "startedAt": run["run_started_at"],
        "receiptArtifact": name,
        "artifactId": str(artifact["id"]),
        "artifactDigest": artifact["digest"],
        "receiptDigest": content_digest(data),
    }, data


def _reason(error: Exception) -> str:
    if isinstance(error, (OSError, ValueError)):
        return str(error)
    return f"{type(error).__name__}: {error}"


def reuse_decision(
    api: Callable[[str], bytes],
    *,
    repository: str,
    revision: str,
    run_id: str,
    attempt: str,
    now: datetime,
) -> tuple[dict, bytes | None]:
    """Decide whether trusted pre-merge evidence replaces the source phase.

    The producer receipt is returned unchanged; the decision records who
    produced it and which release consumed it.
    """
    trees: dict[str, str] = {}

    def tree(sha: str) -> str:
        if sha not in trees:
            commit = json.loads(api(f"repos/{repository}/git/commits/{sha}"))
            trees[sha] = commit["tree"]["sha"]
        return trees[sha]

    decision = {
        "schema": REUSE_SCHEMA,
        "reused": False,
        "consumer": dict(
            repository=repository, revision=revision, runId=run_id, attempt=attempt
        ),
        "producer": None,
        "rejections": [],
    }
    # Every unproven candidate falls back; its reason stays in the record.
    try:
        decision["consumer"]["tree"] = tree(revision)
        pulls = json.loads(api(f"repos/{repository}/commits/{revision}/pulls"))
        heads = [revision] + [
            pull["head"]["sha"]
            for pull in pulls
            if pull.get("merge_commit_sha") == revision and pull.get("merged_at")
        ]
        runs: dict[str, dict] = {}
        for head in dict.fromkeys(heads):
            listed = json.loads(
                api(
                    f"repos/{repository}/actions/workflows/"
                    f"{Path(REUSE_WORKFLOW).name}/runs?head_sha={head}&per_page=10"
                )
            )
            for run in listed.get("workflow_runs", []):
                runs.setdefault(str(run["id"]), run)
    except Exception as error:
        decision["rejections"].append(
            {"reason": f"Candidate lookup failed: {_reason(error)}"}
        )
        return decision, None
    if not runs:
        decision["rejections"].append(
            {"reason": "No run tested this commit or its merged pull request head"}
        )
    for run in sorted(
        runs.values(), key=lambda run: str(run.get("run_started_at")), reverse=True
    ):
        try:
            producer, receipt = _admit_producer(
                api,
                str(run["id"]),
                repository=repository,
                tree=tree,
                release_tree=decision["consumer"]["tree"],
                now=now,
            )
        except Exception as error:
            decision["rejections"].append(
                {"runId": str(run.get("id")), "reason": _reason(error)}
            )
            continue
        decision.update(reused=True, producer=producer)
        return decision, receipt
    return decision, None


def _reuse_summary(decision: dict) -> str:
    consumer, producer = decision["consumer"], decision["producer"]
    if producer:
        return (
            "### Source evidence: reused pre-merge verification\n\n"
            f"- Producer: {producer['event']} run [{producer['runId']} attempt "
            f"{producer['attempt']}]({producer['url']}) tested "
            f"`{producer['revision']}` with workflow commit "
            f"`{producer['workflowCommit']}`\n"
            f"- Receipt: `{producer['receiptArtifact']}` "
            f"(`{producer['receiptDigest']}`)\n"
            f"- Consumer: release run {consumer['runId']} attempt "
            f"{consumer['attempt']} for `{consumer['revision']}`, same tree "
            f"`{producer['tree']}`\n"
        )
    rows = "".join(
        "| {} | {} |\n".format(
            item.get("runId", "-"), " ".join(item["reason"].split()).replace("|", "/")
        )
        for item in decision["rejections"]
    )
    return (
        "### Source evidence: source phase runs\n\n"
        "| Candidate run | Not reused because |\n|---|---|\n" + rows
    )


def _write_reuse(args: argparse.Namespace) -> int:
    decision, receipt = reuse_decision(
        _github_api,
        repository=args.repository,
        revision=args.revision,
        run_id=args.run_id,
        attempt=args.attempt,
        now=datetime.now(timezone.utc),
    )
    args.path.mkdir(parents=True, exist_ok=True)
    (args.path / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")
    producer = decision["producer"] or {}
    if receipt is not None:
        (args.path / "receipt.json").write_bytes(receipt)
    outputs = {
        "reused": "true" if decision["reused"] else "false",
        "receipt_artifact": producer.get("receiptArtifact", ""),
        "receipt_digest": producer.get("receiptDigest", ""),
    }
    for variable, text in (
        ("GITHUB_OUTPUT", "".join(f"{k}={v}\n" for k, v in outputs.items())),
        ("GITHUB_STEP_SUMMARY", _reuse_summary(decision)),
    ):
        if os.environ.get(variable):
            with open(os.environ[variable], "a", encoding="utf-8") as handle:
                handle.write(text)
    if decision["reused"]:
        print(f"::notice::Reusing source verification {producer['url']}")
    else:
        print("::notice::No reusable source evidence; the source phase runs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "verify", "reuse"))
    parser.add_argument("path", type=Path, help="Receipt, or reuse output folder")
    parser.add_argument("--repository", default="")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--selection", default="{}")
    parser.add_argument("--result", default="")
    parser.add_argument("--artifact")
    parser.add_argument("--digest", default="", help="Expected receipt sha256")
    args = parser.parse_args()
    if args.action == "reuse":
        if not args.repository:
            parser.error("reuse requires --repository")
        return _write_reuse(args)
    try:
        identity = dict(
            revision=args.revision, run_id=args.run_id, attempt=args.attempt
        )
        if args.artifact:
            identity["attempt"] = artifact_attempt(
                args.artifact,
                prefix="source-verification",
                run_id=args.run_id,
                current_attempt=args.attempt,
            )
        selection = json.loads(args.selection)
        if not isinstance(selection, dict):
            raise ValueError("Admitted selection is not an object")
        if args.action == "write":
            receipt = source_receipt(
                **identity, selection=selection, result=args.result
            )
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(json.dumps(receipt, indent=2) + "\n")
            return 0
        data = args.path.read_bytes()
        if not args.digest or content_digest(data) != args.digest:
            raise ValueError("Source receipt bytes do not match the handed-off digest")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Source receipt is not an object")
        validate_source_receipt(payload, **identity, selection=selection)
    except (OSError, ValueError) as error:
        print(f"::error::Source verification {args.action} refused: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
