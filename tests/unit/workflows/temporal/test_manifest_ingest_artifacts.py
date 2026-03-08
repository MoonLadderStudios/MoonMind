import pytest
from typing import Any
from temporalio import workflow
from datetime import timedelta
from unittest.mock import patch, MagicMock

# Create a full manifest ingest workflow that uses the refs
from moonmind.workflows.temporal.workflows.manifest_ingest import MoonMindManifestIngestWorkflow

@pytest.mark.asyncio
async def test_manifest_ingest_artifacts_returns_refs() -> None:
    pass
