"""Unit tests for retired queue-contract surface (#4108 follow-up)."""

from __future__ import annotations

import pytest

from moonmind.workflows.executions.manifest_contract import (
    ManifestContractError,
    _normalize_options,
    derive_required_capabilities,
)


def _manifest(**blocks) -> dict:
    base: dict = {
        "dataSources": [{"id": "local", "type": "SimpleDirectoryReader"}],
    }
    base.update(blocks)
    return base


class TestRetiredVectorBlocksNormalized:
    @pytest.mark.parametrize(
        "key",
        ["embeddings", "vectorStore", "vectorstore", "VectorStore", " vectorStore "],
    )
    def test_retired_spellings_rejected(self, key: str) -> None:
        with pytest.raises(ManifestContractError, match="4108"):
            derive_required_capabilities(_manifest(**{key: {"type": "qdrant"}}))

    def test_absent_retired_blocks_pass(self) -> None:
        assert derive_required_capabilities(_manifest(**{"vectorStore": {}})) == [
            "local_fs"
        ]


class TestForceFullRetired:
    def test_force_full_rejected_actionably(self) -> None:
        with pytest.raises(ManifestContractError, match="forceFull"):
            _normalize_options({"forceFull": True})

    def test_supported_options_still_normalize(self) -> None:
        assert _normalize_options({"dryRun": True, "maxDocs": 5}) == {
            "dryRun": True,
            "maxDocs": 5,
        }
        assert _normalize_options(None) == {}
