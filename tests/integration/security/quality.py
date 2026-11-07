"""Small ground-truth oracle and evidence export for security CI scenarios.

This is test support, not an assessment service. Native/report bytes come from
the production artifact owner; expected outcomes come from the licensed case
manifest, independently of scanner or agent conclusions.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import time
from pathlib import Path

from tools.ci.redact_diagnostics import redact_text

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "tests/fixtures/security_review/quality-cases.json"
OUTPUT = ROOT / "artifacts/integration-ci/security-quality"


def observation_digest(observed):
    """Compare actual semantic observations without run IDs or timings."""
    facts = {
        key: sorted(observed[key]) if isinstance(observed[key], list) else observed[key]
        for key in ("outcome", "findings", "covered", "satisfied")
    }
    return (
        "sha256:"
        + hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    )


def evaluate(expected, observed, evidence):
    wanted, found = set(expected["findings"]), set(observed["findings"])
    missing_evidence = [
        ref for ref in observed["executionEvidence"] if not evidence.get(ref)
    ]
    metrics = {
        "falsePositives": len(found - wanted),
        "missedDefects": len(wanted - found),
        "incorrectSatisfiedConclusions": int(
            observed["satisfied"] and not expected["satisfied"]
        ),
        "coverageGaps": len(
            set(expected.get("scope", expected["covered"])) - set(observed["covered"])
        ),
        "traceableFindings": sum(
            bool(refs := observed["evidenceByFinding"].get(finding))
            and all(evidence.get(ref) for ref in refs)
            for finding in found
        ),
        "observedFindings": len(found),
    }
    failures = [
        key
        for key in ("falsePositives", "missedDefects", "incorrectSatisfiedConclusions")
        if metrics[key]
    ]
    if metrics["coverageGaps"] != expected.get("coverageGaps", 0) or set(
        expected["covered"]
    ) - set(observed["covered"]):
        failures.append("coverage_mismatch")
    if observed["outcome"] != expected["outcome"]:
        failures.append("outcome_mismatch")
    if observed["satisfied"] != expected["satisfied"]:
        failures.append("satisfaction_mismatch")
    if metrics["traceableFindings"] != len(found):
        failures.append("untraceable_findings")
    if not observed["executionEvidence"] or missing_evidence:
        failures.append("missing_execution_evidence")
    return {"passed": not failures, "failures": failures, "metrics": metrics}


def observation(outcome, findings, covered, *, native_ref, satisfied=False):
    return {
        "outcome": outcome,
        "findings": sorted(set(findings)),
        "covered": sorted(set(covered)),
        "satisfied": satisfied,
        "evidenceByFinding": {finding: [native_ref] for finding in set(findings)},
        "executionEvidence": [native_ref],
    }


UNVERIFIED = [
    {
        "check": "live scanners and provider accounts",
        "status": "Unverified",
        "reason": "Cloud/ZAP/scanner transports are controlled fixtures, not live services.",
        "verifiableBy": "Existing credentialed CI or authorized assessment owner",
        "nextCheck": "Run upstream-assessment-images.yml and docker-publish-repository-security.yml; then run the admitted scope against an authorized test account/origin.",
    },
    {
        "check": "agent reasoning quality",
        "status": "Unverified",
        "reason": "Selected Skill/harness routing uses a controlled response; no model was evaluated.",
        "verifiableBy": "Authorized provider evaluation owner",
        "nextCheck": "Run security-investigate on the retained ownership fixture with one selected model, a 60-second budget and the same protected-access oracle; retain its actual response and usage.",
    },
    {
        "check": "independent professional assurance",
        "status": "Unverified",
        "reason": "Controlled readiness scenarios do not constitute independent qualification.",
        "verifiableBy": "Independent qualified assessor",
        "nextCheck": "Assess the explicitly authorized deployment and compare findings and coverage with this retained sample.",
    },
]


class QualityRun:
    def __init__(self):
        self.started = time.monotonic()
        self.cpu = self._cpu()
        self.useful_seconds = None

    @staticmethod
    def _cpu():
        return sum(
            usage.ru_utime + usage.ru_stime
            for usage in (
                resource.getrusage(resource.RUSAGE_SELF),
                resource.getrusage(resource.RUSAGE_CHILDREN),
            )
        )

    def useful(self):
        if self.useful_seconds is None:
            self.useful_seconds = time.monotonic() - self.started

    async def finish(
        self,
        service,
        case_id,
        observed,
        refs,
        *,
        provenance,
        repeat=None,
        mutations=None,
    ):
        from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

        manifest_bytes = MANIFEST.read_bytes()
        manifest = json.loads(manifest_bytes)
        case = next(row for row in manifest["cases"] if row["id"] == case_id)
        directory = OUTPUT / case_id
        directory.mkdir(parents=True, exist_ok=True)
        evidence, retained = {}, {}
        for index, ref in enumerate(dict.fromkeys(refs)):
            _, body = await service.read(
                artifact_id=ref,
                access=ArtifactAccessContext.for_execution("assessment"),
            )
            source_digest = "sha256:" + hashlib.sha256(body).hexdigest()
            body = redact_text(body.decode("utf-8"))[0].encode("utf-8")
            name = f"evidence-{index}.txt"
            (directory / name).write_bytes(body)
            evidence[ref] = body
            retained[ref] = {
                "path": name,
                "sourceSha256": source_digest,
                "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }
        result = evaluate(case["expected"], observed, evidence)
        probes = {
            name: {"observed": actual, **evaluate(case["expected"], actual, evidence)}
            for name, actual in (mutations or {}).items()
        }
        if set(case.get("requiredMutations", [])) - set(probes):
            result["failures"].append("missing_mutation_check")
        if any(probe["passed"] for probe in probes.values()):
            result["failures"].append("mutation_not_rejected")
        if repeat is not None and observation_digest(observed) != observation_digest(
            repeat
        ):
            result["failures"].append("nonreproducible_observation")
        result["passed"] = not result["failures"]
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            revision = None
        result.update(
            {
                "schemaVersion": "security-quality/v1",
                "runIdentity": os.environ.get(
                    "PYTEST_XDIST_TESTRUNUID", str(os.getpid())
                ),
                "case": case,
                "manifestDigest": "sha256:"
                + hashlib.sha256(manifest_bytes).hexdigest(),
                "observed": observed,
                "repeatObserved": repeat,
                "retainedArtifacts": retained,
                "mutationChecks": probes,
                "provenance": {
                    **provenance,
                    "sourceRevision": revision,
                    "python": platform.python_version(),
                    "dependencies": {
                        name: importlib.metadata.version(name)
                        for name in ("pytest", "temporalio", "pydantic", "boto3")
                    },
                },
                "measurements": {
                    "wallSeconds": time.monotonic() - self.started,
                    "cpuSeconds": self._cpu() - self.cpu,
                    "timeToUsefulResultSeconds": self.useful_seconds,
                    "retainedBytes": sum(len(body) for body in evidence.values()),
                    "processPeakRssKiB": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss
                    / (1024 if platform.system() == "Darwin" else 1),
                    "limits": "Wall/CPU span the fixture journey and artifact reads; CPU includes waited-for children. RSS is the pytest worker lifetime high-water mark, not incremental container memory. No provider billing was measured.",
                },
                "reproducibility": {
                    "status": "measured" if repeat is not None else "Unverified",
                    "firstDigest": observation_digest(observed),
                    "repeatDigest": (
                        observation_digest(repeat) if repeat is not None else None
                    ),
                    "matches": (
                        observation_digest(observed) == observation_digest(repeat)
                        if repeat is not None
                        else None
                    ),
                    "nextCheck": "Rerun this same case with the recorded tool/configuration and compare semantic observation digests.",
                },
                "unverifiedChecks": UNVERIFIED,
            }
        )
        (directory / "result.json").write_text(
            redact_text(json.dumps(result, indent=2))[0] + "\n"
        )
        # Persist failures before asserting, so a red CI run retains useful evidence.
        assert result["passed"], result["failures"]
        return result


def summarize(directory=OUTPUT, manifest_path=MANIFEST):
    """Recheck corpus completeness and retained bytes in the existing CI lane."""
    raw = manifest_path.read_bytes()
    cases = json.loads(raw)["cases"]
    missing, failed, results = [], [], []
    for case in cases:
        path = directory / case["id"] / "result.json"
        if not path.exists():
            missing.append(case["id"])
            continue
        try:
            result = json.loads(path.read_text())
            evidence = {}
            for ref, item in result["retainedArtifacts"].items():
                body = (path.parent / item["path"]).read_bytes()
                if item["sha256"] != "sha256:" + hashlib.sha256(body).hexdigest():
                    raise ValueError("retained evidence changed")
                evidence[ref] = body
            valid = (
                result["case"] == case
                and result["manifestDigest"]
                == "sha256:" + hashlib.sha256(raw).hexdigest()
                and evaluate(case["expected"], result["observed"], evidence)["passed"]
                and not set(case.get("requiredMutations", []))
                - set(result["mutationChecks"])
                and all(
                    not evaluate(case["expected"], probe["observed"], evidence)[
                        "passed"
                    ]
                    for probe in result["mutationChecks"].values()
                )
                and (
                    result.get("repeatObserved") is None
                    or observation_digest(result["observed"])
                    == observation_digest(result["repeatObserved"])
                )
            )
            if not valid:
                failed.append(case["id"])
            results.append(result)
        except (OSError, ValueError, KeyError, TypeError):
            failed.append(case["id"])
    run_ids = {result.get("runIdentity") for result in results}
    if len(run_ids) != 1 or None in run_ids:
        failed.append("mixed_or_missing_execution_identity")
    return {
        "schemaVersion": "security-quality-summary/v1",
        "passed": not missing and not failed,
        "missingCases": missing,
        "failedCases": failed,
        "cases": [
            {"id": result["case"]["id"], "metrics": result["metrics"]}
            for result in results
        ],
        "limits": "Only these controlled cases and recorded tool/configuration identities were observed; see each result's Unverified checks and cost/reproducibility limits.",
    }


if __name__ == "__main__":
    summary = summarize()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (OUTPUT / "case-manifest.json").write_bytes(MANIFEST.read_bytes())
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["passed"] else 1)
