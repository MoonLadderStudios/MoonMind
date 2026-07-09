#!/usr/bin/env python3
"""MM-939 doc-index-to-slice artifact generator.

Consumes the advisory MoonSpec documentation index and emits temporary,
artifact-backed doc slices plus compact implementation packets. Canonical docs
remain authoritative; generated slices are derived workflow inputs.

Traceability: MM-939 (source issue MM-927, "Moon Spec Doc Architecture
Alignment"); covers DESIGN-REQ-006, DESIGN-REQ-011, and DESIGN-REQ-012.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4


ISSUE = "MM-939"
SOURCE_ISSUE = "MM-927"
DEFAULT_INDEX = Path("artifacts/moonspec-doc-index/index.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/moonspec-doc-slices")
COVERAGE_GATE_PASS = (
    "PASS - every canonical claim and run-local coverage point is owned by at "
    "least one doc slice."
)
TEMPORARY_SPEC_ADAPTER_ROLE = (
    "spec.md, when produced downstream, is a temporary derived adapter from "
    "the doc slice and Source Packet; canonical docs and stable doc-index "
    "claim identities remain authoritative."
)


@dataclass(frozen=True)
class DocIndexRef:
    path: str
    digest: str
    claimCount: int
    documentCount: int


@dataclass(frozen=True)
class SourceReference:
    path: str
    title: str
    sections: list[str]
    claimIds: list[str]
    coverageIds: list[str]


@dataclass(frozen=True)
class StoryCandidate:
    id: str
    summary: str
    description: str
    sourceReference: SourceReference
    independentTest: str
    acceptanceCriteria: list[str]
    requirements: list[str]
    dependencies: list[str]


@dataclass(frozen=True)
class DocSlice:
    id: str
    sourceDocument: str
    documentClass: str
    viewpoint: str
    owningSurface: str
    authority: str
    stableClaimIds: list[str]
    coverageIds: list[str]
    dependencies: list[str]
    coverage: dict[str, list[str]]
    storyCandidate: StoryCandidate


@dataclass(frozen=True)
class ImplementationPacket:
    id: str
    docSliceId: str
    artifactRole: str
    downstreamStages: list[str]
    storyCandidateRef: dict[str, str]
    sourcePacket: dict[str, Any]
    coverageRefs: list[str]


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _validate_doc_index_payload(doc_index: Mapping[str, Any]) -> None:
    if _string(doc_index.get("tool")) != "index_moonspec_docs":
        raise ValueError("Doc index JSON was not produced by index_moonspec_docs.")
    if not isinstance(doc_index.get("documents"), list):
        raise ValueError("Doc index JSON must include a documents list.")
    if not isinstance(doc_index.get("claims"), list):
        raise ValueError("Doc index JSON must include a claims list.")


def _document_title(
    document: Mapping[str, Any],
    claims: Iterable[Mapping[str, Any]],
) -> str:
    for claim in claims:
        if _string(claim.get("type")) == "document":
            return _string(claim.get("heading"))
    return Path(_string(document.get("path"))).stem


def _section_claim_groups(
    claims: list[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    document_claims = [
        claim for claim in claims if _string(claim.get("type")) == "document"
    ]
    groups: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] | None = None

    for claim in claims:
        claim_type = _string(claim.get("type"))
        if claim_type == "document":
            continue
        if claim_type == "section" or current is None:
            current = [*document_claims, claim]
            groups.append(current)
        else:
            current.append(claim)

    if groups:
        return groups
    return [document_claims] if document_claims else []


def _coverage_id(index: int) -> str:
    return f"DESIGN-REQ-{index:03d}"


def build_doc_slice_payload(
    doc_index: Mapping[str, Any],
    *,
    index_path: Path,
    index_digest: str,
    slices_path: str,
    packets_path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build derived doc slices and implementation packets from doc-index output."""

    _validate_doc_index_payload(doc_index)

    documents = [
        dict(document)
        for document in doc_index.get("documents", [])
        if isinstance(document, Mapping)
    ]
    claims = [
        dict(claim)
        for claim in doc_index.get("claims", [])
        if isinstance(claim, Mapping) and _string(claim.get("id"))
    ]
    claims_by_document: dict[str, list[Mapping[str, Any]]] = {}
    for claim in claims:
        document_path = _string(claim.get("documentPath"))
        claims_by_document.setdefault(document_path, []).append(claim)

    doc_index_ref = DocIndexRef(
        path=index_path.as_posix(),
        digest=index_digest,
        claimCount=len(claims),
        documentCount=len(documents),
    )

    doc_slices: list[DocSlice] = []
    implementation_packets: list[ImplementationPacket] = []
    coverage_matrix: dict[str, list[str]] = {}
    coverage_counter = 1

    for document in sorted(documents, key=lambda entry: _string(entry.get("path"))):
        document_path = _string(document.get("path"))
        document_claims = claims_by_document.get(document_path, [])
        title = _document_title(document, document_claims)
        for group in _section_claim_groups(document_claims):
            slice_number = len(doc_slices) + 1
            slice_id = f"DOC-SLICE-{slice_number:03d}"
            story_id = f"STORY-{slice_number:03d}"
            coverage_id = _coverage_id(coverage_counter)
            coverage_counter += 1
            section_headings = [
                _string(claim.get("heading"))
                for claim in group
                if _string(claim.get("type")) != "document"
            ]
            if not section_headings:
                section_headings = [title]
            claim_ids = [
                _string(claim.get("id"))
                for claim in group
                if _string(claim.get("id"))
            ]
            dependencies: list[str] = []
            related_docs = _strings(document.get("relatedDocs"))
            for related_doc in related_docs:
                if related_doc.startswith("docs/") and related_doc != document_path:
                    dependencies.append(f"doc:{related_doc}")

            summary_subject = section_headings[0]
            source_reference = SourceReference(
                path=document_path,
                title=title,
                sections=section_headings,
                claimIds=claim_ids,
                coverageIds=[coverage_id],
            )
            story = StoryCandidate(
                id=story_id,
                summary=f"Implement doc slice for {summary_subject}",
                description=(
                    "As a MoonSpec operator, I can implement and verify the "
                    f"canonical documentation claims for {summary_subject} "
                    "without treating generated artifacts as source of truth."
                ),
                sourceReference=source_reference,
                independentTest=(
                    "Verify the implementation against the listed stable claim "
                    "IDs and coverage ID, then run MoonSpec verification against "
                    "the preserved Source Packet."
                ),
                acceptanceCriteria=[
                    "Each listed stable canonical claim is implemented or "
                    + "explicitly ruled out.",
                    "The downstream Source Packet preserves source document "
                    + "and claim references.",
                    TEMPORARY_SPEC_ADAPTER_ROLE,
                ],
                requirements=[
                    f"Maintain coverage for {coverage_id}.",
                    "Do not embed canonical document body text in workflow payloads.",
                    "Do not replace canonical docs with generated slice artifacts.",
                ],
                dependencies=dependencies,
            )
            coverage = {claim_id: [slice_id] for claim_id in claim_ids}
            coverage[coverage_id] = [slice_id]
            for key, value in coverage.items():
                coverage_matrix.setdefault(key, []).extend(value)

            doc_slice = DocSlice(
                id=slice_id,
                sourceDocument=document_path,
                documentClass=_string(document.get("documentClass")),
                viewpoint=_string(document.get("viewpoint")),
                owningSurface=_string(document.get("owningSurface")),
                authority=_string(document.get("authority")),
                stableClaimIds=claim_ids,
                coverageIds=[coverage_id],
                dependencies=dependencies,
                coverage=coverage,
                storyCandidate=story,
            )
            doc_slices.append(doc_slice)
            implementation_packets.append(
                ImplementationPacket(
                    id=f"IMPL-PACKET-{slice_number:03d}",
                    docSliceId=slice_id,
                    artifactRole="temporary implementation packet",
                    downstreamStages=[
                        "moonspec-specify",
                        "moonspec-assess",
                        "moonspec-plan",
                        "moonspec-tasks",
                        "moonspec-implement",
                        "moonspec-verify",
                    ],
                    storyCandidateRef={
                        "docSlicesPath": slices_path,
                        "docSliceId": slice_id,
                        "storyId": story_id,
                    },
                    sourcePacket={
                        "artifactRole": TEMPORARY_SPEC_ADAPTER_ROLE,
                        "sourceDocument": document_path,
                        "documentClass": _string(document.get("documentClass")),
                        "viewpoint": _string(document.get("viewpoint")),
                        "owningSurface": _string(document.get("owningSurface")),
                        "stableClaimIds": claim_ids,
                        "coverageIds": [coverage_id],
                        "sourceIssueTraceability": [ISSUE, SOURCE_ISSUE],
                    },
                    coverageRefs=[*claim_ids, coverage_id],
                )
            )

    uncovered = [
        _string(claim.get("id"))
        for claim in claims
        if _string(claim.get("id")) not in coverage_matrix
    ]
    coverage_gate = (
        COVERAGE_GATE_PASS if not uncovered else "FAIL - uncovered claims remain."
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    common = {
        "tool": "slice_moonspec_docs",
        "issue": ISSUE,
        "sourceIssue": SOURCE_ISSUE,
        "generatedAt": generated_at,
        "artifactRole": "temporary derived workflow artifact",
        "authoritativeSource": "canonical docs via doc-index stable claim identities",
        "docIndexRef": asdict(doc_index_ref),
        "temporarySpecAdapterRole": TEMPORARY_SPEC_ADAPTER_ROLE,
        "coverageGate": coverage_gate,
        "uncoveredClaims": uncovered,
    }
    slices_payload = {
        **common,
        "docSliceCount": len(doc_slices),
        "implementationPacketsPath": packets_path,
        "coverageMatrix": coverage_matrix,
        "docSlices": [asdict(item) for item in doc_slices],
    }
    packets_payload = {
        **common,
        "docSlicesPath": slices_path,
        "implementationPacketCount": len(implementation_packets),
        "implementationPackets": [asdict(item) for item in implementation_packets],
    }
    return slices_payload, packets_payload


def write_doc_slice_artifacts(
    doc_index: Mapping[str, Any],
    *,
    index_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    index_digest = _digest_file(index_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    slices_path = output_dir / "doc-slices.json"
    packets_path = output_dir / "implementation-packets.json"
    slices_payload, packets_payload = build_doc_slice_payload(
        doc_index,
        index_path=index_path,
        index_digest=index_digest,
        slices_path=slices_path.as_posix(),
        packets_path=packets_path.as_posix(),
    )
    slices_path.write_text(
        json.dumps(slices_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    packets_path.write_text(
        json.dumps(packets_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "tool": "slice_moonspec_docs",
        "issue": ISSUE,
        "sourceIssue": SOURCE_ISSUE,
        "docSlicesPath": slices_path.as_posix(),
        "implementationPacketsPath": packets_path.as_posix(),
        "docSliceCount": slices_payload["docSliceCount"],
        "implementationPacketCount": packets_payload["implementationPacketCount"],
        "coverageGate": slices_payload["coverageGate"],
        "docIndexRef": slices_payload["docIndexRef"],
    }


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_ROOT / f"doc-slices-{stamp}-{uuid4().hex}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX,
        help=f"Doc-index JSON input path (default: {DEFAULT_INDEX.as_posix()}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Artifact output directory. Defaults under "
            "artifacts/moonspec-doc-slices/."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Console output format.",
    )
    args = parser.parse_args(argv)

    index_path = args.index
    if not index_path.is_file():
        print(f"Doc index not found: {index_path}", file=sys.stderr)
        return 2
    try:
        doc_index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Doc index is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(doc_index, Mapping):
        print("Doc index JSON must be an object.", file=sys.stderr)
        return 2

    output_dir = args.output_dir or _default_output_dir()
    try:
        summary = write_doc_slice_artifacts(
            doc_index,
            index_path=index_path,
            output_dir=output_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "MM-939 MoonSpec doc slices wrote "
            f"{summary['docSliceCount']} slice(s) and "
            f"{summary['implementationPacketCount']} implementation packet(s) "
            f"to {summary['docSlicesPath']} and "
            f"{summary['implementationPacketsPath']}."
        )
    return 0 if summary["coverageGate"] == COVERAGE_GATE_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
