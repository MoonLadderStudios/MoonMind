"""Unit tests for compact MoonSpec verify metadata issue projection.

MoonLadderStudios/MoonMind#4491 (P1): the production native-verifier path
compacts the gate before the parent workflow constructs the contract-repair
input. The projection must preserve a bounded issue list so repair keeps the
evidence it must address.
"""

from __future__ import annotations

from moonmind.workflows.temporal.agent_result_payloads import (
    compact_moonspec_verify_metadata,
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


def test_compact_moonspec_verify_metadata_keeps_unverified_criteria() -> None:
    unverified = {"reason": "Needs a GPU render host.", "verifiableBy": "GPU CI lane"}
    compacted = compact_moonspec_verify_metadata(
        {
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
    )
    rows = compacted["validatedRefs"]["acceptance"]["evidence"]
    assert rows == [
        {"requirementId": "AC-1", "evidenceRefs": ["art_gate"]},
        {"requirementId": "AC-2", "evidenceRefs": [], "unverified": unverified},
    ]
