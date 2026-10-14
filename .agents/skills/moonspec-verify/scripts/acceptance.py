#!/usr/bin/env python3
"""Portable subject capture and evidence reuse for moonspec-verify.

No provider API, MoonMind import, or credential discovery. Callers perform the
authorized fetch first and supply the actual source and completion target.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return (
        subprocess.check_output(
            ["git", "-C", str(repo), *args], env=env, stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )


def nonblank_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def capture(
    repo: Path, repository: str, target: str, *, target_mode: bool = False
) -> dict:
    """Capture actual content, including unstaged/untracked work, without checkout."""
    repo = Path(git(repo, "rev-parse", "--show-toplevel"))
    target_revision = git(repo, "rev-parse", "--verify", f"{target}^{{commit}}")
    target_tree = git(repo, "rev-parse", f"{target_revision}^{{tree}}")
    target_ref = git(repo, "rev-parse", "--symbolic-full-name", target)
    if target_ref.startswith("refs/remotes/"):
        target_ref = (
            "refs/heads/" + target_ref.removeprefix("refs/remotes/").split("/", 1)[1]
        )
    if not target_ref.startswith("refs/heads/"):
        raise ValueError(
            "completion policy must name a branch, not a detached revision"
        )
    revision = target_revision if target_mode else git(repo, "rev-parse", "HEAD")
    if target_mode:
        tree = target_tree
    else:
        # A private index leaves the user's staging intact. Git's ignore rules
        # exclude disposable test artifacts; real untracked source is included.
        with tempfile.TemporaryDirectory(prefix="acceptance-index-") as temp:
            original_objects = Path(git(repo, "rev-parse", "--git-path", "objects"))
            if not original_objects.is_absolute():
                original_objects = repo / original_objects
            temporary_objects = Path(temp) / "objects"
            temporary_objects.mkdir()
            env = dict(
                os.environ,
                GIT_INDEX_FILE=str(Path(temp) / "index"),
                GIT_OBJECT_DIRECTORY=str(temporary_objects),
                GIT_ALTERNATE_OBJECT_DIRECTORIES=str(original_objects),
                GIT_OPTIONAL_LOCKS="0",
            )
            git(repo, "read-tree", "HEAD", env=env)
            git(repo, "add", "--all", "--", ".", env=env)
            # Inspect every staged gitlink, including embedded repositories
            # absent from .gitmodules. A gitlink cannot bind dirty nested
            # source to the captured tree.
            for entry in git(repo, "ls-files", "--stage", "-z", env=env).split("\0"):
                if not entry.startswith("160000 "):
                    continue
                nested = repo / entry.split("\t", 1)[1]
                if (nested / ".git").exists():
                    dirty = git(
                        nested,
                        "--no-optional-locks",
                        "status",
                        "--porcelain",
                        "--untracked-files=all",
                        "--ignore-submodules=none",
                    )
                else:
                    # An empty uninitialized submodule has no local content.
                    # Populated paths without their owning Git metadata cannot
                    # be certified by the recorded revision alone.
                    dirty = nested.is_dir() and any(nested.iterdir())
                if dirty:
                    raise ValueError(
                        "dirty gitlinks require a content-bound workspace checkpoint"
                    )
            tree = git(repo, "write-tree", env=env)
    return {
        "subject": {
            "repository": repository,
            "revision": revision,
            "contentDigest": f"git-tree:{tree}",
        },
        "completionTarget": {
            "ref": target_ref,
            "revision": target_revision,
            "contentDigest": f"git-tree:{target_tree}",
        },
    }


def scope(
    source: bytes, source_ref: str, requirement_ids: list[str], *, complete: bool
) -> dict:
    if not nonblank_strings(requirement_ids) or len(set(requirement_ids)) != len(
        requirement_ids
    ):
        raise ValueError(
            "supply the unique mandatory requirement IDs from the original scope"
        )
    return {
        "sourceRef": source_ref,
        "sourceDigest": "sha256:" + hashlib.sha256(source).hexdigest(),
        "requirementIds": sorted(requirement_ids),
        "complete": complete,
    }


# Only source/proof semantics bind a requirement. Advice and transient readiness
# never create an obligation or invalidate otherwise identical executed proof.
OBLIGATION_FIELDS = (
    "id",
    "sourceRef",
    "sourceDigest",
    "sourceLocator",
    "sourceQuote",
    "classification",
    "requiredBehavior",
    "executableChecks",
    "proofMethods",
    "owner",
    "target",
    "profile",
    "topology",
    "samplingPolicy",
)
PROOF_FIELDS = ("target", "profile", "topology", "samplingPolicy")
ROW_STATUSES = {
    "VERIFIED",
    "PARTIAL",
    "MISSING",
    "CONFLICT",
    "UNVERIFIED",
    "NO_DETERMINATION",
    "FAILED",
}


def digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def obligation_digest(row: dict) -> str:
    return digest({key: row[key] for key in OBLIGATION_FIELDS if key in row})


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity(subject: object) -> bool:
    return isinstance(subject, dict) and all(
        _text(subject.get(key)) for key in ("repository", "revision", "contentDigest")
    )


def bind_verification_map(
    current: dict, assessment: dict, *, source: bytes | None = None
) -> dict:
    """Bind the existing plan to source; no old verdict or new scope is imported."""
    original = current.get("scope")
    if not isinstance(original, dict) or assessment.get("scope") != original:
        raise ValueError(
            "assessment verification map does not match original source scope"
        )
    if (
        source is not None
        and original.get("sourceDigest")
        != "sha256:" + hashlib.sha256(source).hexdigest()
    ):
        raise ValueError(
            "source bytes do not match independently captured source digest"
        )
    required = original.get("requirementIds")
    if not nonblank_strings(required) or len(set(required)) != len(required):
        raise ValueError("verification map requires unique mandatory source IDs")
    rows = assessment.get("verificationMap")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source-bound verification map is missing")
    seen, mapped = set(), set()
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("id")) or row["id"] in seen:
            raise ValueError("verification map has duplicate or invalid IDs")
        seen.add(row["id"])
        if any(
            row.get(key) != original.get(key) or not _text(row.get(key))
            for key in ("sourceRef", "sourceDigest")
        ):
            raise ValueError(
                "verification map requirement is not bound to original source"
            )
        if not all(
            _text(row.get(key)) for key in ("sourceLocator", "requiredBehavior")
        ):
            raise ValueError(
                "verification map requires source locator and required behavior"
            )
        if "sourceQuote" in row and (
            not _text(row["sourceQuote"])
            or (source is not None and row["sourceQuote"] not in source.decode("utf-8"))
        ):
            raise ValueError("source quote is not present in original source bytes")
        classification = row.get("classification")
        if classification not in {"mandatory", "optional", "separate_release"}:
            raise ValueError(
                "verification map requires a source obligation classification"
            )
        mandatory = classification == "mandatory"
        if mandatory != (row["id"] in required) or (
            "mandatory" in row and row["mandatory"] is not mandatory
        ):
            raise ValueError("verification map changes the original mandatory scope")
        if mandatory:
            if not all(
                nonblank_strings(row.get(key))
                for key in ("executableChecks", "proofMethods")
            ) or not _text(row.get("owner")):
                raise ValueError(
                    "mandatory source requirement needs executable checks, proof methods and owner"
                )
            mapped.add(row["id"])
        for key in PROOF_FIELDS:
            if key in row and not _text(row[key]):
                raise ValueError("source proof constraints must be nonblank strings")
    if mapped != set(required):
        raise ValueError("verification map omits original mandatory scope")
    result = {**current, "verificationMap": copy.deepcopy(rows)}
    if "scopeReconciliation" in assessment:
        _validate_reconciliation(assessment["scopeReconciliation"], original)
        result["scopeReconciliation"] = copy.deepcopy(assessment["scopeReconciliation"])
    return result


def _validate_reconciliation(value: object, scope: dict) -> None:
    if not isinstance(value, dict) or value.get("currentScope") != scope:
        raise ValueError("scope reconciliation must cite the current source scope")
    previous = value.get("previousScope")
    removed = value.get("removedRequirements")
    if (
        not isinstance(previous, dict)
        or previous.get("sourceRef") != scope.get("sourceRef")
        or not _text(previous.get("sourceDigest"))
        or not nonblank_strings(previous.get("requirementIds"))
        or not isinstance(removed, list)
    ):
        raise ValueError(
            "scope reconciliation requires original scope and removed source IDs"
        )
    ids = []
    for row in removed:
        if not isinstance(row, dict) or not all(
            _text(row.get(key)) for key in ("id", "reason", "sourceLocator")
        ):
            raise ValueError(
                "removed source requirement needs an ID, reason and source citation"
            )
        ids.append(row["id"])
    if len(set(ids)) != len(ids) or set(ids) != set(previous["requirementIds"]) - set(
        scope["requirementIds"]
    ):
        raise ValueError(
            "scope reconciliation must account for all removed requirements"
        )


def _scope_allows_partial(prior: object, current: dict) -> bool:
    """Retain unchanged rows only through the caller's exact source lineage."""
    scope = current.get("scope")
    if not isinstance(prior, dict) or not isinstance(scope, dict):
        return False
    if prior == scope:
        return True
    reconciliation = current.get("scopeReconciliation")
    try:
        _validate_reconciliation(reconciliation, scope)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return (
        reconciliation["previousScope"] == prior
        and prior.get("complete") is True
        and scope.get("complete") is True
        and all(
            prior.get(key) == scope.get(key) for key in ("sourceRef", "sourceDigest")
        )
    )


def _normalized_remaining_work(
    report: dict, scope: dict, subject: dict
) -> tuple[set[str], list[str]]:
    """Validate one complete source-linked handoff for both judgment and reuse.

    Callers choose the independently established scope: current judgments use
    current scope; partial reuse uses the historical binding only after exact
    source/reconciliation lineage has been checked. A malformed handoff never
    means an empty blocker set.
    """
    verdict = report.get("verdict")
    if not isinstance(verdict, str) or verdict not in {
        "FULLY_IMPLEMENTED",
        "ADDITIONAL_WORK_NEEDED",
        "NO_DETERMINATION",
        "BLOCKED",
    }:
        return set(), ["remaining work has no canonical verification verdict"]
    required = scope.get("requirementIds")
    if not nonblank_strings(required) or len(set(required)) != len(required):
        return set(), ["remaining work requires unique mandatory source IDs"]
    work = report.get("remainingWork")
    reference = report.get("remainingWorkRef")
    if reference is not None and not _text(reference):
        return set(), ["remaining work reference must be a nonblank artifact reference"]
    if work is None or work == []:
        if verdict == "FULLY_IMPLEMENTED" and not reference:
            return set(), []
        return set(), ["non-passing report needs inline source-linked remaining work"]
    if not _valid_remaining_work(work):
        return set(), ["remaining work must be a complete actionable inline handoff"]
    errors = []
    gaps = set()
    for item in work:
        ids = item.get("requirementIds", [item["requirement"]])
        if (
            not nonblank_strings(ids)
            or len(set(ids)) != len(ids)
            or item["requirement"] not in ids
            or not set(ids) <= set(required)
        ):
            errors.append(
                "remaining work must consistently name mandatory requirements in the report source scope"
            )
        else:
            gaps.update(ids)
        if "candidate" in item and item["candidate"] != subject:
            errors.append("remaining work refers to a different candidate")
    return gaps, errors


def _source_bound(report: dict, current: dict) -> bool:
    refs = report.get("validatedRefs")
    binding = refs.get("acceptance") if isinstance(refs, dict) else None
    keys = (
        "verificationMap",
        "evidenceSet",
        "evidenceSetDigest",
        "scopeReconciliation",
        "sourceBinding",
    )
    return any(
        key in current or (isinstance(binding, dict) and key in binding) for key in keys
    )


def bind_evidence(current: dict, evidence: dict[str, bytes]) -> dict:
    """Bind bytes read by the authorized owner, never an artifact existence claim."""
    if not isinstance(evidence, dict) or any(
        not _text(ref) or not isinstance(body, bytes) for ref, body in evidence.items()
    ):
        raise ValueError("evidence inventory requires reference-to-bytes entries")
    inventory = {
        ref: "sha256:" + hashlib.sha256(body).hexdigest()
        for ref, body in evidence.items()
    }
    return {**current, "evidenceSet": inventory, "evidenceSetDigest": digest(inventory)}


def _inventory_valid(binding: dict) -> bool:
    inventory = binding.get("evidenceSet")
    return (
        isinstance(inventory, dict)
        and all(
            _text(ref)
            and isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
            and all(c in "0123456789abcdef" for c in value[7:])
            for ref, value in inventory.items()
        )
        and binding.get("evidenceSetDigest") == digest(inventory)
    )


def _freshness_errors(
    binding: dict, current: dict, now: datetime | None = None
) -> list[str]:
    freshness = binding.get("freshness", {})
    if (
        not isinstance(freshness, dict)
        or not current.get("freshnessPolicy")
        or freshness.get("policy") != current["freshnessPolicy"]
    ):
        return ["required evidence freshness changed or missing"]
    expiry = freshness.get("validUntil")
    if expiry is not None:
        try:
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expiry.tzinfo is None or (now or datetime.now(timezone.utc)) >= expiry:
                return ["evidence expired"]
        except (AttributeError, TypeError, ValueError):
            return ["invalid evidence expiry"]
    return []


def _strict_errors(
    report: dict,
    current: dict,
    *,
    exact: bool,
    now: datetime | None = None,
    partial: bool = False,
) -> list[str]:
    """Validate declarations only; owners still interpret the referenced bytes."""
    refs = report.get("validatedRefs")
    binding = refs.get("acceptance") if isinstance(refs, dict) else None
    if not isinstance(binding, dict) or binding.get("schemaVersion") != "acceptance/v1":
        return ["current judgment requires an acceptance/v1 binding"]
    errors = []
    prior, subject = binding.get("subject"), current.get("subject")
    keys = (
        ("repository", "revision", "contentDigest")
        if exact
        else ("repository", "contentDigest")
    )
    if (
        not _identity(prior)
        or not _identity(subject)
        or any(prior[key] != subject[key] for key in keys)
    ):
        errors.append("judgment is not bound to the current candidate")
    scope = current.get("scope")
    prior_scope = binding.get("scope")
    if not isinstance(scope, dict) or (
        prior_scope != scope
        and not (partial and _scope_allows_partial(prior_scope, current))
    ):
        errors.append("judgment is not bound to the current source scope")
    try:
        bind_verification_map(
            current, {"scope": scope, "verificationMap": current.get("verificationMap")}
        )
        bind_verification_map(
            {**current, "scope": prior_scope} if partial else current,
            {"scope": prior_scope, "verificationMap": binding.get("verificationMap")},
        )
    except (ValueError, TypeError):
        errors.append("source-bound verification map is invalid")
    for value, value_scope in (
        (binding, prior_scope if partial else scope),
        (current, scope),
    ):
        if "scopeReconciliation" in value:
            try:
                _validate_reconciliation(value["scopeReconciliation"], value_scope)
            except (TypeError, ValueError):
                errors.append("invalid source scope reconciliation")
    if exact and binding.get("scopeReconciliation") != current.get(
        "scopeReconciliation"
    ):
        errors.append("judgment has omitted or changed current source reconciliation")
    for value in (binding, current):
        if "sourceBinding" in value:
            source_binding = value["sourceBinding"]
            if (
                not isinstance(source_binding, dict)
                or not _text(source_binding.get("artifactRef"))
                or not isinstance(source_binding.get("pointer"), str)
            ):
                errors.append("invalid original source artifact selector")
    if exact and binding.get("sourceBinding") != current.get("sourceBinding"):
        errors.append("judgment has a different original source artifact selector")
    old_map = binding.get("verificationMap")
    new_map = current.get("verificationMap")
    if (
        not partial
        and isinstance(old_map, list)
        and isinstance(new_map, list)
        and (
            {r.get("id"): obligation_digest(r) for r in old_map if isinstance(r, dict)}
            != {
                r.get("id"): obligation_digest(r)
                for r in new_map
                if isinstance(r, dict)
            }
        )
    ):
        errors.append("source proof obligations changed")
    for value in (binding, current):
        if not _inventory_valid(value):
            errors.append("evidence inventory is missing, malformed or not byte-bound")
    if not partial and binding.get("evidenceSetDigest") != current.get(
        "evidenceSetDigest"
    ):
        errors.append("judgment has not considered the current evidence set")
    target, prior_target = current.get("completionTarget"), binding.get(
        "completionTarget"
    )
    if (
        not isinstance(target, dict)
        or not isinstance(prior_target, dict)
        or not all(
            _text(value.get(key))
            for value in (target, prior_target)
            for key in ("ref", "revision", "contentDigest")
        )
        or target.get("ref") != prior_target.get("ref")
    ):
        errors.append("completion policy changed or missing")
    if exact and target != prior_target:
        errors.append("judgment is not bound to the current completion target")
    errors.extend(_freshness_errors(binding, current, now))
    return errors


def _row_errors(row: object, binding: dict, current: dict) -> list[str]:
    if not isinstance(row, dict):
        return ["invalid requirement evidence row"]
    plan = next(
        (
            item
            for item in current["verificationMap"]
            if item["id"] == row.get("requirementId")
        ),
        None,
    )
    if plan is None or plan["classification"] != "mandatory":
        return ["evidence row is outside mandatory source scope"]
    old_plan = next(
        (
            item
            for item in binding["verificationMap"]
            if item["id"] == row.get("requirementId")
        ),
        None,
    )
    if (
        old_plan is None
        or row.get("obligationDigest") != obligation_digest(old_plan)
        or row.get("obligationDigest") != obligation_digest(plan)
    ):
        return ["evidence row does not bind the current source obligation"]
    if row.get("status") not in ROW_STATUSES:
        return ["evidence row has no explicit verified or nonpassing status"]
    refs = row.get("evidenceRefs")
    if row["status"] == "VERIFIED" and not nonblank_strings(refs):
        return ["verified requirement has no objective evidence references"]
    if not isinstance(refs, list) or any(not _text(ref) for ref in refs):
        return ["invalid requirement evidence references"]
    if any(
        ref not in current["evidenceSet"]
        or binding["evidenceSet"].get(ref) != current["evidenceSet"].get(ref)
        for ref in refs
    ):
        return ["requirement evidence is unresolved or its bytes changed"]
    tested = row.get("testedSubject", binding["subject"])
    if not _identity(tested) or any(
        tested[key] != current["subject"][key]
        for key in ("repository", "contentDigest")
    ):
        return ["requirement evidence tested a different subject"]
    if row["status"] == "VERIFIED":
        if row.get("proofMethod") not in plan["proofMethods"]:
            return ["verified evidence does not use a source-allowed proof method"]
        if any(row.get(key) != plan[key] for key in PROOF_FIELDS if key in plan):
            return ["verified evidence does not match the source proof constraints"]
    return []


def _reusable_rows(
    report: dict, current: dict, now: datetime | None = None
) -> list[dict]:
    if (
        report.get("invalid")
        or report.get("degraded")
        or _strict_errors(report, current, exact=False, now=now, partial=True)
    ):
        return []
    binding = report["validatedRefs"]["acceptance"]
    rows = binding.get("evidence")
    if not isinstance(rows, list):
        return []
    ids = [row.get("requirementId") for row in rows if isinstance(row, dict)]
    blocked, handoff_errors = _normalized_remaining_work(
        report, binding["scope"], binding["subject"]
    )
    if handoff_errors:
        return []
    result = []
    for row in rows:
        if (
            isinstance(row, dict)
            and ids.count(row.get("requirementId")) == 1
            and row.get("status") == "VERIFIED"
            and _text(row.get("requirementId"))
            and row.get("requirementId") not in blocked
            and not _row_errors(row, binding, current)
        ):
            retained = copy.deepcopy(row)
            retained.setdefault("testedSubject", copy.deepcopy(binding["subject"]))
            result.append(retained)
    return result


def admission(current: dict, previous: dict | None = None) -> dict:
    """Cheap prerequisite routing; never dispatch work or manufacture readiness."""
    bind_verification_map(
        current,
        {
            "scope": current.get("scope"),
            "verificationMap": current.get("verificationMap"),
        },
    )
    result = {
        "readyRequirementIds": [],
        "waitingRequirementIds": [],
        "blockedRequirementIds": [],
        "errors": [],
    }
    states = []
    for row in current["verificationMap"]:
        if row["classification"] != "mandatory":
            continue
        probes = [("capability", row["id"], row["owner"], row.get("capability"))]
        prerequisites = row.get("prerequisites", [])
        if not isinstance(prerequisites, list):
            prerequisites = [None]
        prerequisite_ids = [
            p.get("id")
            for p in prerequisites
            if isinstance(p, dict) and _text(p.get("id"))
        ]
        for probe in prerequisites:
            valid_id = (
                isinstance(probe, dict)
                and _text(probe.get("id"))
                and prerequisite_ids.count(probe["id"]) == 1
            )
            probes.append(
                (
                    "prerequisite",
                    probe["id"] if valid_id else "invalid",
                    probe.get("owner") if isinstance(probe, dict) else None,
                    probe if valid_id else None,
                )
            )
        statuses = []
        normalized = []
        for kind, probe_id, owner, probe in probes:
            if (
                not _text(owner)
                or not isinstance(probe, dict)
                or not _text(probe.get("resumeCheck"))
                or probe.get("status")
                not in {"ready", "busy", "unavailable", "unauthorized"}
                or not nonblank_strings(probe.get("evidenceRefs"))
                or not _inventory_valid(current)
                or any(
                    ref not in current["evidenceSet"] for ref in probe["evidenceRefs"]
                )
            ):
                status = "unavailable"
                result["errors"].append(
                    f"{row['id']}: current capability probe identity, owner, resume check or evidence is missing"
                )
            else:
                status = probe["status"]
            statuses.append(status)
            normalized.append(
                {"kind": kind, "id": probe_id, "owner": owner, "status": status}
            )
        state = (
            "blocked"
            if any(s in {"unavailable", "unauthorized"} for s in statuses)
            else "waiting" if "busy" in statuses else "ready"
        )
        result[state + "RequirementIds"].append(row["id"])
        states.append(
            {
                "id": row["id"],
                "obligationDigest": obligation_digest(row),
                "probes": sorted(normalized, key=lambda p: (p["kind"], p["id"])),
            }
        )
    subject = current.get("subject", {})
    result["fingerprint"] = digest(
        {
            "subject": {
                key: subject.get(key) for key in ("repository", "contentDigest")
            },
            "scope": current.get("scope"),
            "states": sorted(states, key=lambda s: s["id"]),
        }
    )
    result["madeProgress"] = (
        not isinstance(previous, dict)
        or previous.get("fingerprint") != result["fingerprint"]
    )
    return result


def reuse(report: dict, current: dict, *, now: datetime | None = None) -> dict:
    """Check an objective report against freshly captured subject/scope/policy."""
    retained = []
    strict = False
    try:
        strict = _source_bound(report, current)
        if strict:
            retained = _reusable_rows(report, current, now)
        result = _reuse(report, current, now=now)
        if strict:
            errors = _strict_errors(report, current, exact=False, now=now)
            binding = report.get("validatedRefs", {}).get("acceptance", {})
            if not errors:
                _, handoff_errors = _normalized_remaining_work(
                    report, current["scope"], current["subject"]
                )
                errors.extend(handoff_errors)
            rows = binding.get("evidence", [])
            if not isinstance(rows, list):
                errors.append("requirement evidence rows are malformed")
            elif not errors:
                for row in rows:
                    errors.extend(_row_errors(row, binding, current))
                    if isinstance(row, dict) and row.get("status") != "VERIFIED":
                        errors.append("mandatory requirement is not verified")
            result["reasons"].extend(errors)
            result["reusable"] = not result["reasons"]
            result["completionEligible"] = (
                result["completionEligible"] and result["reusable"]
            )
            result["reusableRows"] = retained
            if not result["reusable"]:
                result["candidateRows"] = _candidate_rows(report, current)
        return result
    except (AttributeError, KeyError, TypeError, ValueError):
        result = {
            "reusable": False,
            "completionEligible": False,
            "reasons": [
                "malformed acceptance binding; obtain complete objective evidence"
            ],
        }
        if strict:
            result["reusableRows"] = retained
        return result


def _reuse(report: dict, current: dict, *, now: datetime | None = None) -> dict:
    evidence = report.get("validatedRefs", {}).get("acceptance", {})
    reasons = []
    if (
        report.get("verdict") != "FULLY_IMPLEMENTED"
        or report.get("invalid")
        or report.get("degraded")
        or report.get("remainingWork")
        or report.get("remainingWorkRef")
    ):
        reasons.append("no accepted objective verdict")
    if evidence.get("schemaVersion") != "acceptance/v1":
        reasons.append("objective acceptance binding missing")
    prior_subject = evidence.get("subject", {})
    subject = current.get("subject", {})
    # A commit containing identical content (including a squash merge) can
    # reuse content evidence. The report retains the original tested revision.
    for key in ("repository", "contentDigest"):
        if not subject.get(key) or prior_subject.get(key) != subject[key]:
            reasons.append(f"subject {key} changed or missing")
    if not prior_subject.get("revision") or not subject.get("revision"):
        reasons.append("revision identity missing")
    current_scope = current.get("scope", {})
    if (
        current_scope.get("complete") is not True
        or evidence.get("scope") != current_scope
    ):
        reasons.append("source scope changed or incomplete")
    required = current_scope.get("requirementIds", [])
    rows = evidence.get("evidence", [])
    if (
        not nonblank_strings(required)
        or len(set(required)) != len(required)
        or sorted(row.get("requirementId", "") for row in rows) != sorted(required)
        or any(not nonblank_strings(row.get("evidenceRefs")) for row in rows)
    ):
        reasons.append("mandatory requirement evidence missing")
    target = current.get("completionTarget", {})
    evidence_target = evidence.get("completionTarget", {})
    if (
        not target.get("ref")
        or evidence_target.get("ref") != target["ref"]
        or not evidence_target.get("revision")
        or not evidence_target.get("contentDigest")
    ):
        reasons.append("completion policy changed or missing")
    freshness = evidence.get("freshness", {})
    if (
        not current.get("freshnessPolicy")
        or freshness.get("policy") != current["freshnessPolicy"]
    ):
        reasons.append("required evidence freshness changed or missing")
    if freshness.get("validUntil"):
        try:
            expiry = datetime.fromisoformat(
                freshness["validUntil"].replace("Z", "+00:00")
            )
            if expiry.tzinfo is None or (now or datetime.now(timezone.utc)) >= expiry:
                reasons.append("evidence expired")
        except (TypeError, ValueError):
            reasons.append("invalid evidence expiry")
    reusable = not reasons
    return {
        "reusable": reusable,
        "completionEligible": bool(
            reusable
            and target.get("revision")
            and target.get("contentDigest") == subject.get("contentDigest")
        ),
        "reasons": reasons,
    }


def _candidate_rows(report: dict, current: dict) -> list[dict]:
    """Legacy coverage only nominates artifacts to inspect, never accepted proof."""
    subject = report.get("subject")
    if (
        not _identity(subject)
        or not _identity(current.get("subject"))
        or any(
            subject[key] != current["subject"][key]
            for key in ("repository", "contentDigest")
        )
        or not _scope_allows_partial(report.get("scope"), current)
        or current["scope"].get("complete") is not True
    ):
        return []
    prior, latest = report.get("verificationMap"), current.get("verificationMap")
    rows = report.get("requirementCoverage")
    if not all(isinstance(value, list) for value in (prior, latest, rows)):
        return []
    old_checks = {
        r.get("id"): r.get("executableChecks")
        for r in prior
        if isinstance(r, dict) and _text(r.get("id"))
    }
    new_checks = {
        r.get("id"): r.get("executableChecks")
        for r in latest
        if isinstance(r, dict) and _text(r.get("id"))
    }
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("id")):
            continue
        rid = row["id"]
        if (
            rid not in seen
            and rid in current["scope"]["requirementIds"]
            and row.get("verificationStatus") == "VERIFIED"
            and nonblank_strings(row.get("testEvidenceRefs"))
            and nonblank_strings(old_checks.get(rid))
            and old_checks[rid] == new_checks.get(rid)
        ):
            result.append(
                {
                    "requirementId": rid,
                    "evidenceRefs": copy.deepcopy(row["testEvidenceRefs"]),
                }
            )
        seen.add(rid)
    return result


def _valid_remaining_work(work: object) -> bool:
    """Require actionable handoff fields, not just a nonempty mapping."""
    if not isinstance(work, list) or not work:
        return False
    for item in work:
        if not isinstance(item, dict):
            return False
        for field in ("requirement", "gapType", "remainingWork"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                return False
    return True


def _current_nonpassing_errors(report: dict, current: dict) -> list[str]:
    binding = report.get("validatedRefs", {}).get("acceptance")
    binding = binding if isinstance(binding, dict) else report
    errors = []
    subject = current.get("subject")
    if not _identity(subject) or binding.get("subject") != subject:
        errors.append("non-passing report is not bound to the current candidate")
    scope = current.get("scope")
    if not isinstance(scope, dict) or binding.get("scope") != scope:
        errors.append("non-passing report is not bound to the current source scope")
        return errors
    _, handoff_errors = _normalized_remaining_work(report, scope, subject)
    errors.extend(handoff_errors)
    return errors


def validate_report(report: object, current: dict | None = None) -> dict:
    """Preflight producer output, not proof that referenced checks actually ran.

    Preserve the report and return diagnostics only. The host still validates
    its envelope and the verifier still inspects evidence through its owners.
    Success binding reuses the existing acceptance implementation below.
    """
    if not isinstance(report, dict):
        return {"valid": False, "errors": ["structured report must be a JSON object"]}
    if current is not None and not isinstance(current, dict):
        return {"valid": False, "errors": ["current binding must be a JSON object"]}
    actions = {
        "FULLY_IMPLEMENTED": {"advance"},
        "ADDITIONAL_WORK_NEEDED": {"reattempt_current_step", "needs_human", "blocked"},
        "NO_DETERMINATION": {"reattempt_current_step", "needs_human", "blocked"},
        "BLOCKED": {"blocked"},
    }
    errors = []
    verdict = report.get("verdict")
    action = report.get("recommendedNextAction")
    if not isinstance(verdict, str) or verdict not in actions:
        errors.append("verdict is missing or is not a canonical verification verdict")
    elif not isinstance(action, str) or action not in actions[verdict]:
        errors.append("recommendedNextAction is missing or incompatible with verdict")
    if not isinstance(report.get("recoverableInCurrentRuntime"), bool):
        errors.append("recoverableInCurrentRuntime must be an explicit boolean")
    for flag in ("invalid", "degraded"):
        if not isinstance(report.get(flag, False), bool) or report.get(flag, False):
            errors.append(
                f"producer report is flagged {flag} or the flag is not boolean"
            )
    if verdict == "FULLY_IMPLEMENTED":
        if report.get("remainingWork") or report.get("remainingWorkRef"):
            errors.append("success cannot retain unresolved remaining work")
        if current is None:
            errors.append(
                "success preflight requires freshly captured current subject and scope"
            )
        else:
            errors.extend(reuse(report, current)["reasons"])
    elif isinstance(verdict, str) and verdict in actions:
        work = report.get("remainingWork")
        reference = report.get("remainingWorkRef")
        if not _valid_remaining_work(work) and not (
            isinstance(reference, str) and reference.strip()
        ):
            errors.append(
                "non-passing report requires remainingWork or a remainingWorkRef"
            )
        if current is not None:
            try:
                errors.extend(_current_nonpassing_errors(report, current))
            except (AttributeError, KeyError, TypeError, ValueError):
                errors.append("malformed current non-passing binding")
    if current is not None and _source_bound(report, current):
        try:
            binding_errors = _strict_errors(report, current, exact=True)
            errors.extend(binding_errors)
            if not binding_errors:
                binding = report["validatedRefs"]["acceptance"]
                rows = binding.get("evidence")
                if not isinstance(rows, list):
                    errors.append("requirement evidence rows are malformed")
                else:
                    ids = [
                        row.get("requirementId")
                        for row in rows
                        if isinstance(row, dict)
                    ]
                    if len(ids) != len(set(ids)):
                        errors.append("duplicate requirement evidence rows")
                    for row in rows:
                        errors.extend(_row_errors(row, binding, current))
                    if verdict != "FULLY_IMPLEMENTED":
                        gaps, handoff_errors = _normalized_remaining_work(
                            report, current["scope"], current["subject"]
                        )
                        errors.extend(handoff_errors)
                        passed = {
                            row["requirementId"]
                            for row in rows
                            if isinstance(row, dict) and row.get("status") == "VERIFIED"
                        }
                        if passed & gaps:
                            errors.append(
                                "verified requirement cannot also remain a blocking gap"
                            )
                        if passed | gaps != set(current["scope"]["requirementIds"]):
                            errors.append(
                                "current judgment must account for every mandatory requirement"
                            )
        except (AttributeError, KeyError, TypeError, ValueError):
            errors.append("malformed source-bound acceptance binding")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors))}


def skill_identity(script: Path) -> dict:
    """Describe the files beside the executed helper, not an upstream checkout."""
    root = script.resolve().parents[1]
    paths = ("SKILL.md", "references/acceptance-policy.md", "scripts/acceptance.py")
    return {
        "skill": "moonspec-verify",
        "skillPath": str(root),
        "files": {
            name: "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in paths
        },
    }


def read_report_json(path: Path) -> object:
    """Reject ambiguous duplicate fields and non-JSON numeric constants."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-JSON numeric constant")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def source_bytes(path: Path, pointer: str = "") -> bytes:
    if not pointer:
        return path.read_bytes()
    if not pointer.startswith("/"):
        raise ValueError("source pointer must be empty or an RFC6901 pointer")
    value = read_report_json(path)
    for token in pointer[1:].split("/"):
        if "~" in token.replace("~0", "").replace("~1", ""):
            raise ValueError("invalid source pointer escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdigit() or str(int(key)) != key:
                raise ValueError("source array pointer requires a canonical index")
            value = value[int(key)]
        else:
            value = value[key]
    if not isinstance(value, str):
        raise ValueError("source pointer must select explicit original source text")
    return value.encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--repo", type=Path, default=Path.cwd())
    capture_parser.add_argument("--repository", required=True)
    capture_parser.add_argument("--target", required=True)
    capture_parser.add_argument("--target-mode", action="store_true")
    capture_parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Original source and constraints, preserved verbatim",
    )
    capture_parser.add_argument("--source-ref", required=True)
    capture_parser.add_argument("--source-artifact-ref")
    capture_parser.add_argument("--source-pointer", default="")
    capture_parser.add_argument("--requirement", action="append", required=True)
    capture_parser.add_argument("--incomplete", action="store_true")
    capture_parser.add_argument("--freshness-policy", default="content")
    capture_parser.add_argument("--assessment", type=Path)
    capture_parser.add_argument(
        "--evidence", nargs=2, action="append", metavar=("REF", "PATH")
    )
    admission_parser = commands.add_parser("admission")
    admission_parser.add_argument("--current", type=Path, required=True)
    admission_parser.add_argument("--previous", type=Path)
    reuse_parser = commands.add_parser("reuse")
    reuse_parser.add_argument("--report", type=Path, required=True)
    reuse_parser.add_argument("--current", type=Path, required=True)
    report_parser = commands.add_parser("validate-report")
    report_parser.add_argument("--report", type=Path, required=True)
    report_parser.add_argument("--current", type=Path)
    commands.add_parser("identity")
    args = parser.parse_args()
    if args.command == "capture":
        if (args.evidence or args.source_artifact_ref) and not args.assessment:
            parser.error("--evidence and --source-artifact-ref require --assessment")
        result = capture(
            args.repo, args.repository, args.target, target_mode=args.target_mode
        )
        original_source = source_bytes(args.source, args.source_pointer)
        if args.source_pointer and not args.source_artifact_ref:
            raise ValueError("source pointer requires original artifact reference")
        if args.source_artifact_ref:
            result["sourceBinding"] = {
                "artifactRef": args.source_artifact_ref,
                "pointer": args.source_pointer,
            }
        result["scope"] = scope(
            original_source,
            args.source_ref,
            args.requirement,
            complete=not args.incomplete,
        )
        result["freshnessPolicy"] = args.freshness_policy
        if args.assessment:
            result = bind_verification_map(
                result, read_report_json(args.assessment), source=original_source
            )
        if args.assessment or args.evidence:
            refs = [ref for ref, path in args.evidence or []]
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate evidence reference")
            result = bind_evidence(
                result,
                {ref: Path(path).read_bytes() for ref, path in args.evidence or []},
            )
    elif args.command == "admission":
        result = admission(
            read_report_json(args.current),
            read_report_json(args.previous) if args.previous else None,
        )
    elif args.command == "reuse":
        result = reuse(read_report_json(args.report), read_report_json(args.current))
    elif args.command == "identity":
        result = skill_identity(Path(__file__))
    else:
        try:
            result = validate_report(
                read_report_json(args.report),
                read_report_json(args.current) if args.current else None,
            )
        except (OSError, UnicodeError, ValueError):
            result = {
                "valid": False,
                "errors": ["report or current JSON is unreadable or malformed"],
            }
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "validate-report" and not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
