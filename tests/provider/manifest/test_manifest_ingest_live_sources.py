"""MM-755 provider verification for live manifest ingest data sources.

These tests intentionally exercise the real manifest pipeline against live
third-party readers. They are skipped unless the operator supplies explicit
live-source configuration.

Run manually, for example:

    python -m pytest tests/provider/manifest -m provider_verification -q -s
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from moonmind.manifest.pipeline import ManifestPipeline
from moonmind.schemas.manifest_v0_models import ManifestV0

pytestmark = [
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]


def _manifest(source: dict[str, Any]) -> ManifestV0:
    return ManifestV0.model_validate(
        {
            "version": "v0",
            "metadata": {
                "name": "mm-755-live-manifest-ingest",
                "description": "MM-755 live data source verification",
            },
            "embeddings": {
                "provider": "openai",
                "model": "text-embedding-3-large",
            },
            "vectorStore": {
                "type": "qdrant",
                "indexName": "mm-755-provider-verification",
            },
            "dataSources": [source],
            "indices": [
                {
                    "id": "idx1",
                    "type": "VectorStoreIndex",
                    "sources": ["live-source"],
                }
            ],
            "retrievers": [
                {
                    "id": "ret1",
                    "type": "Vector",
                    "indices": ["idx1"],
                }
            ],
            "run": {
                "dryRun": False,
                "errorPolicy": "stopOnFirstError",
            },
        }
    )


def _run_live_source(manifest: ManifestV0) -> dict[str, object]:
    result = ManifestPipeline(manifest).run()

    assert result.sources, "manifest pipeline should report the live data source"
    source = result.sources[0]
    assert source.error is None, source.error
    assert source.doc_count > 0
    assert result.total_docs == source.doc_count
    return result.to_dict()


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN", "").strip(),
    reason="GITHUB_TOKEN is required for MM-755 live GitHub manifest verification",
)
def test_manifest_pipeline_ingests_live_github_source() -> None:
    """Run manifest ingest against a real GitHub repository reader."""
    repo = os.environ.get("MM_MANIFEST_TEST_GITHUB_REPO", "octocat/Spoon-Knife")
    branch = os.environ.get("MM_MANIFEST_TEST_GITHUB_BRANCH", "main")
    if "/" not in repo:
        pytest.skip("MM_MANIFEST_TEST_GITHUB_REPO must be in owner/repo form")
    owner, repo_name = repo.split("/", 1)

    output = _run_live_source(
        _manifest(
            {
                "id": "live-source",
                "type": "GithubRepositoryReader",
                "params": {
                    "owner": owner,
                    "repo": repo_name,
                    "branch": branch,
                    "filterExtensions": [".md"],
                },
                "auth": {"githubToken": "${GITHUB_TOKEN}"},
            }
        )
    )

    assert output["sources"][0]["type"] == "GithubRepositoryReader"


@pytest.mark.skipif(
    not all(
        [
            os.environ.get("CONFLUENCE_URL", "").strip(),
            os.environ.get("CONFLUENCE_API_KEY", "").strip(),
            os.environ.get("TEST_CONFLUENCE_SPACE_KEY", "").strip(),
        ]
    ),
    reason=(
        "CONFLUENCE_URL, CONFLUENCE_API_KEY, and TEST_CONFLUENCE_SPACE_KEY are "
        "required for MM-755 live Confluence manifest verification"
    ),
)
def test_manifest_pipeline_ingests_live_confluence_source() -> None:
    """Run manifest ingest against a real Confluence reader."""
    space_key = os.environ["TEST_CONFLUENCE_SPACE_KEY"].strip()
    output = _run_live_source(
        _manifest(
            {
                "id": "live-source",
                "type": "ConfluenceReader",
                "params": {
                    "spaceKey": space_key,
                    "maxPages": 5,
                },
                "auth": {
                    "baseUrl": "${CONFLUENCE_URL}",
                    "token": "${CONFLUENCE_API_KEY}",
                },
            }
        )
    )

    assert output["sources"][0]["type"] == "ConfluenceReader"


@pytest.mark.skipif(
    not os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    or not os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_KEY_PATH", "").strip(),
    reason=(
        "GOOGLE_DRIVE_FOLDER_ID and GOOGLE_DRIVE_SERVICE_ACCOUNT_KEY_PATH are "
        "required for MM-755 live Google Drive manifest verification"
    ),
)
def test_manifest_pipeline_ingests_live_google_drive_source() -> None:
    """Run manifest ingest against a real Google Drive reader."""
    folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
    output = _run_live_source(
        _manifest(
            {
                "id": "live-source",
                "type": "GoogleDriveReader",
                "params": {"folderId": folder_id},
                "auth": {
                    "serviceAccountKeyPath": (
                        "${GOOGLE_DRIVE_SERVICE_ACCOUNT_KEY_PATH}"
                    )
                },
            }
        )
    )

    assert output["sources"][0]["type"] == "GoogleDriveReader"
