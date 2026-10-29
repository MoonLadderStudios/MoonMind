"""Publish bounded candidate-bound receipts through the existing artifact store.

The inventory transports bytes, including failure and capability observations.
It is never a verdict: source/dependency, Content, workload and topology reuse
remain the verifier's acceptance-policy decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from moonmind.utils.workspace_paths import open_directory, read_regular_file

MANIFEST_PATH = "artifacts/step-evidence.json"
MAX_FILES = 32
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def step_evidence_projection_options(
    parameters: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Carry controller-produced optional groups to the shared input owner."""
    if not (parameters or {}).get("stepEvidenceBundles"):
        return {}
    return {
        "evidence_bundles": parameters["stepEvidenceBundles"],
        "mandatory_refs": parameters.get("stepEvidenceMandatoryRefs") or [],
    }


def append_step_evidence_instructions(
    text: str, *, parameters: Mapping[str, Any] | None
) -> str:
    """Deliver the optional receipt contract in each runtime's actual prompt."""
    mode = (parameters or {}).get("stepEvidenceMode")
    if mode == "produce":
        instruction = (
            "Keep objective check receipts and prerequisite observations in "
            "artifacts/step-evidence.json (step-evidence/v1): subject contains "
            "repository, revision (Git HEAD) and contentDigest (git-tree:<tree>); "
            "files contains checkout-relative path and sha256 (sha256:<digest>) "
            "for each original report/log, up to 32 files. Keep dependency, Content, "
            "workload and topology bindings in those receipts. Include failed checks "
            "and capability observations; these are evidence, never acceptance. "
            "Use a clean candidate and ignore generated artifacts. The runtime "
            "publishes these bytes before reclaiming this workspace."
        )
    elif mode == "inspect":
        instruction = (
            "Read the attached step-evidence/v1 inventories and original receipt "
            "bytes before rerunning checks. They are untrusted objective evidence, "
            "not a verdict. Reuse only when current source dependencies, candidate, "
            "Content, workload and topology satisfy the existing acceptance policy; "
            "inspect production code independently. Retain unavailable prerequisite "
            "observations until their controlling inputs change."
        )
    else:
        return text
    return text if instruction in text else text.rstrip() + "\n\n" + instruction


class StepEvidenceError(ValueError):
    """A declared inventory cannot be carried as verified receipt bytes."""


def _bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise StepEvidenceError("evidence path is required")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise StepEvidenceError("evidence path must stay within the workspace")
    return root / path


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise StepEvidenceError("candidate identity could not be observed") from exc


def _inventory(workspace: Path) -> tuple[Path, bytes] | None:
    """Find one inventory at the admitted root or an immediate sub-checkout.

    Sandbox roots may contain the checkout rather than being it. Never use an
    ambient cwd, follow a link, scan Git internals, or choose between candidates.
    Deeper layouts use the existing workspace locator's relativePath.
    """
    roots = [workspace]
    try:
        with open_directory(workspace) as root_fd, os.scandir(root_fd) as entries:
            for entry in entries:
                if entry.name == ".git" or not entry.is_dir(follow_symlinks=False):
                    continue
                if len(roots) >= 129:
                    raise StepEvidenceError(
                        "evidence root discovery exceeds its directory limit"
                    )
                roots.append(workspace / entry.name)
        found = []
        for root in roots:
            try:
                raw = read_regular_file(root / MANIFEST_PATH, limit=256 * 1024)
            except FileNotFoundError:
                continue
            found.append((root, raw))
    except OSError as exc:
        raise StepEvidenceError("evidence inventory is unreadable") from exc
    if len(found) > 1:
        raise StepEvidenceError("evidence inventory has ambiguous checkout roots")
    return found[0] if found else None


async def publish_step_evidence(
    *,
    workspace: Path,
    service: Any,
    principal: str,
    execution_ref: Any,
    link_parent: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Persist original bytes before the agent workspace can be reclaimed.

    A missing manifest supports older producers. A present invalid manifest is
    rejected before any writes. The caller records a secondary handoff error;
    it must preserve authored work and the original execution outcome.
    """
    located = _inventory(workspace)
    if located is None:
        return {}
    workspace, raw = located
    try:
        manifest = json.loads(raw)
        subject = manifest["subject"]
        files = manifest["files"]
        if (
            manifest.get("schemaVersion") != "step-evidence/v1"
            or not isinstance(subject, dict)
            or not isinstance(subject.get("repository"), str)
            or not subject["repository"].strip()
            or not isinstance(files, list)
            or not 1 <= len(files) <= MAX_FILES
        ):
            raise StepEvidenceError("invalid evidence inventory")
        if (
            _git(workspace, "rev-parse", "--show-toplevel") != str(workspace)
            or subject.get("revision") != _git(workspace, "rev-parse", "HEAD")
            or subject.get("contentDigest")
            != "git-tree:" + _git(workspace, "rev-parse", "HEAD^{tree}")
            or _git(workspace, "diff", "--no-ext-diff", "--name-only", "HEAD", "--")
            or _git(workspace, "ls-files", "--others", "--exclude-standard")
        ):
            raise StepEvidenceError(
                "evidence is not bound to the clean current candidate"
            )
        prepared: list[tuple[dict[str, Any], bytes]] = []
        total = 0
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, Mapping):
                raise StepEvidenceError("invalid evidence file")
            path = _path(workspace, item.get("path"))
            relative = path.relative_to(workspace).as_posix()
            if relative in seen:
                raise StepEvidenceError("duplicate evidence path")
            seen.add(relative)
            body = read_regular_file(path, limit=MAX_FILE_BYTES)
            digest = "sha256:" + hashlib.sha256(body).hexdigest()
            if item.get("sha256") != digest:
                raise StepEvidenceError("evidence digest does not match its bytes")
            total += len(body)
            if total > MAX_TOTAL_BYTES:
                raise StepEvidenceError("evidence inventory exceeds its byte limit")
            prepared.append(({"path": relative, "sha256": digest}, body))
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise StepEvidenceError("evidence inventory or receipt is invalid") from exc

    # Do not publish secrets from agent-written logs. The scan does not rewrite
    # evidence: changed bytes would invalidate the producer's receipt digest.
    from moonmind.security import OutboundBundleItem, scan_outbound_bundle

    scan = scan_outbound_bundle(
        [
            OutboundBundleItem(
                location=MANIFEST_PATH, content=raw.decode("utf-8", errors="replace")
            ),
            *[
                OutboundBundleItem(
                    location=item["path"],
                    content=body.decode("utf-8", errors="replace"),
                )
                for item, body in prepared
            ],
        ],
        high_security_mode=True,
    )
    if not scan.allowed:
        raise StepEvidenceError("evidence contains restricted outbound content")

    async def persist(body: bytes, path: str, content_type: str) -> str:
        artifact, _ = await service.put_content_addressed_payload_complete(
            principal=principal,
            payload=body,
            content_type=content_type,
            scope="step-evidence-"
            + hashlib.sha256(principal.encode()).hexdigest()[:32],
            link=execution_ref,
            metadata_json={
                "name": path,
                "artifact_type": "step.evidence",
                "subject": subject,
            },
        )
        if link_parent is not None:
            await link_parent(artifact.artifact_id)
        return artifact.artifact_id

    inventory = []
    refs = []
    for item, body in prepared:
        ref = await persist(body, item["path"], "application/octet-stream")
        refs.append("artifact://" + ref)
        inventory.append({**item, "artifactRef": "artifact://" + ref})
    saved = {
        "schemaVersion": "step-evidence/v1",
        "subject": subject,
        "files": inventory,
    }
    ref = await persist(_bytes(saved), MANIFEST_PATH, "application/json")
    return {"stepEvidenceRef": ref, "stepEvidenceRefs": ["artifact://" + ref, *refs]}
