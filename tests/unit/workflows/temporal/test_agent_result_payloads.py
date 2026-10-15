"""Unit tests for compact MoonSpec verify metadata issue projection.

MoonLadderStudios/MoonMind#4491 (P1): the production native-verifier path
compacts the gate before the parent workflow constructs the contract-repair
input. The projection must preserve a bounded issue list so repair keeps the
evidence it must address.
"""

from __future__ import annotations

import copy
import json

import pytest

from moonmind.schemas.temporal_payload_policy import MAX_TEMPORAL_METADATA_BYTES
from moonmind.workflows.temporal.agent_result_payloads import (
    compact_moonspec_verify_metadata,
    compact_published_agent_run_result_payload,
)


def test_compact_moonspec_verify_metadata_preserves_bounded_issues() -> None:
    compacted = compact_moonspec_verify_metadata(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "issues": [
                {
                    "severity": "error",
                    "description": "original gap",
                    "evidence": "gap evidence",
                }
            ],
        }
    )
    assert compacted["issues"] == [
        {
            "severity": "error",
            "description": "original gap",
            "evidence": "gap evidence",
        }
    ]


def test_compact_moonspec_verify_metadata_bounds_issue_count() -> None:
    compacted = compact_moonspec_verify_metadata(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "issues": [
                {"severity": "error", "description": f"gap {index}"}
                for index in range(25)
            ],
        }
    )
    assert len(compacted["issues"]) == 20


def test_compact_moonspec_verify_metadata_truncates_long_issue_text() -> None:
    compacted = compact_moonspec_verify_metadata(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "issues": [
                {
                    "severity": "error",
                    "description": "d" * 2000,
                    "evidence": "e" * 2000,
                }
            ],
        }
    )
    assert len(compacted["issues"]) == 1
    assert len(compacted["issues"][0]["description"]) <= 700
    assert len(compacted["issues"][0]["evidence"]) <= 700


def test_compact_moonspec_verify_metadata_omits_absent_issues() -> None:
    compacted = compact_moonspec_verify_metadata({"verdict": "FULLY_IMPLEMENTED"})
    assert "issues" not in compacted


@pytest.fixture
def unverified_gate() -> dict:
    unverified = {"reason": "Needs a GPU render host.", "verifiableBy": "GPU CI lane"}
    return {
        "verdict": "FULLY_IMPLEMENTED",
        "gateResultRef": "art_gate",
        "validatedRefs": {
            "acceptance": {
                "schemaVersion": "acceptance/v1",
                "subject": {
                    "repository": "example/repo",
                    "revision": "abc",
                    "contentDigest": "git-tree:abc",
                },
                "scope": {
                    "sourceRef": "example/repo#1",
                    "sourceDigest": "sha256:source",
                    "complete": True,
                    "requirementIds": ["AC-1", "AC-2"],
                },
                "completionTarget": {
                    "ref": "refs/heads/main",
                    "revision": "def",
                    "contentDigest": "git-tree:def",
                },
                "evidence": [
                    {"requirementId": "AC-1", "evidenceRefs": ["test:ac-1"]},
                    {"requirementId": "AC-2", "unverified": unverified},
                ],
                "freshness": {"policy": "content"},
            }
        },
    }


def test_compact_moonspec_verify_metadata_keeps_unverified_criteria(
    unverified_gate,
) -> None:
    compacted = compact_moonspec_verify_metadata(unverified_gate)
    rows = compacted["validatedRefs"]["acceptance"]["evidence"]
    assert rows == [
        {"requirementId": "AC-1", "evidenceRefs": ["art_gate"]},
        {
            "requirementId": "AC-2",
            "evidenceRefs": [],
            "unverified": {
                "reason": "Needs a GPU render host.",
                "verifiableBy": "GPU CI lane",
            },
        },
    ]


@pytest.mark.parametrize(
    "text", ["long explanation " * 1000, "\U0001f30d" * 10000], ids=["ascii", "unicode"]
)
def test_published_unverified_disclosures_fit_workflow_metadata(
    unverified_gate, text
) -> None:
    gate = copy.deepcopy(unverified_gate)
    binding = gate["validatedRefs"]["acceptance"]
    binding["scope"]["requirementIds"] = [f"AC-{index}" for index in range(8)]
    binding["evidence"] = [
        {
            "requirementId": identifier,
            "unverified": {"reason": text, "verifiableBy": text},
        }
        for identifier in binding["scope"]["requirementIds"]
    ]
    compacted = compact_published_agent_run_result_payload(
        {"metadata": {"moonSpecVerify": gate, "outputAgentResultRef": "art_result"}}
    )
    assert (
        len(json.dumps(compacted["metadata"]).encode()) <= MAX_TEMPORAL_METADATA_BYTES
    )
    projected = compacted["metadata"]["moonSpecVerify"]
    assert projected["gateResultRef"] == "art_gate"
    assert projected["validatedRefs"]["acceptance"]["scope"] == binding["scope"]
    rows = projected["validatedRefs"]["acceptance"]["evidence"]
    assert [row["requirementId"] for row in rows] == binding["scope"]["requirementIds"]
    assert all(
        row["unverified"]["reason"] and row["unverified"]["verifiableBy"]
        for row in rows
    )
    assert all(not row["evidenceRefs"] for row in rows)
    assert binding["evidence"][0]["unverified"]["reason"] == text
