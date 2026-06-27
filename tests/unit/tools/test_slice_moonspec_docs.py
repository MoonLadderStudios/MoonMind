"""MM-939 MoonSpec doc slice artifact tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "slice_moonspec_docs.py"
SPEC = importlib.util.spec_from_file_location("slice_moonspec_docs", MODULE_PATH)
assert SPEC is not None
slice_moonspec_docs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = slice_moonspec_docs
SPEC.loader.exec_module(slice_moonspec_docs)

mod = slice_moonspec_docs


def _doc_index() -> dict:
    return {
        "tool": "index_moonspec_docs",
        "issue": "MM-930",
        "sourceIssue": "MM-927",
        "documentCount": 1,
        "claimCount": 3,
        "documents": [
            {
                "path": "docs/Workflows/ExampleDesign.md",
                "documentClass": "System / Feature Design View",
                "viewpoint": "System / Feature Design View",
                "authority": "MoonSpec doc-native workflow behavior",
                "owningSurface": "MoonSpec",
                "relatedDocs": [],
                "relatedImplementation": ["tools/slice_moonspec_docs.py"],
            }
        ],
        "claims": [
            {
                "id": "claim:doc",
                "documentPath": "docs/Workflows/ExampleDesign.md",
                "heading": "Example Design",
                "type": "document",
                "anchor": "docs/Workflows/ExampleDesign.md#example-design",
                "digest": "sha256:doc",
            },
            {
                "id": "claim:capability",
                "documentPath": "docs/Workflows/ExampleDesign.md",
                "heading": "Doc Slice Capability",
                "type": "section",
                "anchor": "docs/Workflows/ExampleDesign.md#doc-slice-capability",
                "digest": "sha256:capability",
            },
            {
                "id": "claim:traceability",
                "documentPath": "docs/Workflows/ExampleDesign.md",
                "heading": "Implementation Packet Traceability",
                "type": "subsection",
                "anchor": (
                    "docs/Workflows/ExampleDesign.md"
                    "#implementation-packet-traceability"
                ),
                "digest": "sha256:traceability",
            },
        ],
        "warnings": [],
    }


def test_build_doc_slice_payload_maps_doc_index_claims_to_story_candidate(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "artifacts" / "moonspec-doc-index" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps(_doc_index()), encoding="utf-8")

    slices, packets = mod.build_doc_slice_payload(
        _doc_index(),
        index_path=index_path,
        index_digest="sha256:index",
        slices_path="artifacts/moonspec-doc-slices/run/doc-slices.json",
        packets_path="artifacts/moonspec-doc-slices/run/implementation-packets.json",
    )

    assert slices["issue"] == "MM-939"
    assert slices["sourceIssue"] == "MM-927"
    assert slices["authoritativeSource"] == (
        "canonical docs via doc-index stable claim identities"
    )
    assert slices["docIndexRef"] == {
        "path": index_path.as_posix(),
        "digest": "sha256:index",
        "claimCount": 3,
        "documentCount": 1,
    }
    assert slices["coverageGate"] == mod.COVERAGE_GATE_PASS

    doc_slice = slices["docSlices"][0]
    assert doc_slice["id"] == "DOC-SLICE-001"
    assert doc_slice["sourceDocument"] == "docs/Workflows/ExampleDesign.md"
    assert doc_slice["stableClaimIds"] == [
        "claim:doc",
        "claim:capability",
        "claim:traceability",
    ]
    assert doc_slice["storyCandidate"]["id"] == "STORY-001"
    assert doc_slice["storyCandidate"]["sourceReference"] == {
        "path": "docs/Workflows/ExampleDesign.md",
        "title": "Example Design",
        "sections": [
            "Doc Slice Capability",
            "Implementation Packet Traceability",
        ],
        "claimIds": ["claim:doc", "claim:capability", "claim:traceability"],
        "coverageIds": ["DESIGN-REQ-001"],
    }
    assert (
        packets["docSlicesPath"]
        == "artifacts/moonspec-doc-slices/run/doc-slices.json"
    )


def test_string_helper_preserves_falsy_non_none_values() -> None:
    assert mod._string(False) == "False"
    assert mod._string(0) == "0"
    assert mod._string(0.0) == "0.0"
    assert mod._string(None) == ""


def test_implementation_packets_preserve_source_packet_traceability() -> None:
    slices, packets = mod.build_doc_slice_payload(
        _doc_index(),
        index_path=Path("artifacts/moonspec-doc-index/index.json"),
        index_digest="sha256:index",
        slices_path="artifacts/moonspec-doc-slices/run/doc-slices.json",
        packets_path="artifacts/moonspec-doc-slices/run/implementation-packets.json",
    )

    packet = packets["implementationPackets"][0]
    assert packet["docSliceId"] == slices["docSlices"][0]["id"]
    assert packet["downstreamStages"] == [
        "moonspec-specify",
        "moonspec-plan",
        "moonspec-tasks",
        "moonspec-implement",
        "moonspec-verify",
    ]
    assert packet["storyCandidateRef"] == {
        "docSlicesPath": "artifacts/moonspec-doc-slices/run/doc-slices.json",
        "docSliceId": "DOC-SLICE-001",
        "storyId": "STORY-001",
    }
    assert packet["sourcePacket"]["sourceDocument"] == "docs/Workflows/ExampleDesign.md"
    assert packet["sourcePacket"]["stableClaimIds"] == [
        "claim:doc",
        "claim:capability",
        "claim:traceability",
    ]
    assert packet["sourcePacket"]["sourceIssueTraceability"] == ["MM-939", "MM-927"]
    assert packet["coverageRefs"] == [
        "claim:doc",
        "claim:capability",
        "claim:traceability",
        "DESIGN-REQ-001",
    ]


def test_coverage_matrix_preserves_canonical_claims_and_run_local_points() -> None:
    slices, _ = mod.build_doc_slice_payload(
        _doc_index(),
        index_path=Path("artifacts/moonspec-doc-index/index.json"),
        index_digest="sha256:index",
        slices_path="artifacts/moonspec-doc-slices/run/doc-slices.json",
        packets_path="artifacts/moonspec-doc-slices/run/implementation-packets.json",
    )

    assert slices["coverageMatrix"]["claim:doc"] == ["DOC-SLICE-001"]
    assert slices["coverageMatrix"]["claim:capability"] == ["DOC-SLICE-001"]
    assert slices["coverageMatrix"]["claim:traceability"] == ["DOC-SLICE-001"]
    assert slices["coverageMatrix"]["DESIGN-REQ-001"] == ["DOC-SLICE-001"]
    assert slices["uncoveredClaims"] == []


def test_write_doc_slice_artifacts_emits_compact_artifact_paths(tmp_path: Path) -> None:
    index_path = tmp_path / "artifacts" / "moonspec-doc-index" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps(_doc_index(), sort_keys=True), encoding="utf-8")
    output_dir = tmp_path / "artifacts" / "moonspec-doc-slices" / "run"

    summary = mod.write_doc_slice_artifacts(
        _doc_index(),
        index_path=index_path,
        output_dir=output_dir,
    )

    assert summary["docSlicesPath"] == (output_dir / "doc-slices.json").as_posix()
    assert summary["implementationPacketsPath"] == (
        output_dir / "implementation-packets.json"
    ).as_posix()
    assert summary["docSliceCount"] == 1
    assert summary["implementationPacketCount"] == 1
    assert summary["docIndexRef"]["digest"].startswith("sha256:")
    slices_text = (output_dir / "doc-slices.json").read_text(encoding="utf-8")
    packets_text = (output_dir / "implementation-packets.json").read_text(
        encoding="utf-8"
    )
    assert "The payload body of docs/Workflows/ExampleDesign.md" not in slices_text
    assert "The payload body of docs/Workflows/ExampleDesign.md" not in packets_text


def test_build_doc_slice_payload_rejects_non_doc_index_json() -> None:
    packets_path = "artifacts/moonspec-doc-slices/run/implementation-packets.json"
    try:
        mod.build_doc_slice_payload(
            {},
            index_path=Path("artifacts/moonspec-doc-index/index.json"),
            index_digest="sha256:index",
            slices_path="artifacts/moonspec-doc-slices/run/doc-slices.json",
            packets_path=packets_path,
        )
    except ValueError as exc:
        assert "index_moonspec_docs" in str(exc)
    else:
        raise AssertionError("Expected non-doc-index JSON to be rejected")


def test_default_output_dir_is_unique_within_same_second() -> None:
    first = mod._default_output_dir()
    second = mod._default_output_dir()

    assert first != second
    assert first.parent == mod.DEFAULT_OUTPUT_ROOT
    assert second.parent == mod.DEFAULT_OUTPUT_ROOT


def test_temporary_spec_adapter_role_is_explicit_in_slices_and_packets() -> None:
    slices, packets = mod.build_doc_slice_payload(
        _doc_index(),
        index_path=Path("artifacts/moonspec-doc-index/index.json"),
        index_digest="sha256:index",
        slices_path="artifacts/moonspec-doc-slices/run/doc-slices.json",
        packets_path="artifacts/moonspec-doc-slices/run/implementation-packets.json",
    )

    assert "temporary derived adapter" in slices["temporarySpecAdapterRole"]
    assert "canonical docs" in slices["temporarySpecAdapterRole"]
    assert "temporary derived adapter" in packets["temporarySpecAdapterRole"]
    assert (
        packets["implementationPackets"][0]["sourcePacket"]["artifactRole"]
        == mod.TEMPORARY_SPEC_ADAPTER_ROLE
    )
