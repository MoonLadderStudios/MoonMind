"""ReaderAdapter wrappers for existing indexers.

Each adapter wraps an existing indexer class to implement the
:class:`~moonmind.manifest.reader_adapter.ReaderAdapter` protocol
(``plan()``, ``fetch()``, ``state()``).

Adapters are auto-registered when this module is imported.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from moonmind.manifest.reader_adapter import PlanResult, register_adapter
from moonmind.schemas.manifest_v0_models import DataSourceConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base helper
# ---------------------------------------------------------------------------


class _BaseAdapter:
    """Common init for all manifest-driven adapters."""

    def __init__(self, ds: DataSourceConfig) -> None:
        self.ds = ds

    def _resolve(self, val: str) -> str:
        """Resolve ``${ENV}`` references to env vars (passthrough if not set)."""
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            env_name = val[2:-1]
            return os.environ.get(env_name, val)
        return val


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GitHubReaderAdapter(_BaseAdapter):
    """Wraps ``moonmind.indexers.github_indexer.GitHubIndexer``."""

    def plan(self) -> PlanResult:
        params = self.ds.params
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        branch = params.get("branch", "main")
        return PlanResult(
            estimated_docs=0,  # cant know without API call
            metadata={
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "filter_extensions": params.get("filterExtensions", []),
            },
        )

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Fetch documents from GitHub via GithubRepositoryReader."""
        try:
            from llama_index.readers.github import GithubRepositoryReader
            from llama_index.readers.github.repository.github_client import (
                GithubClient,
            )
        except ImportError:
            logger.error(
                "llama_index.readers.github is not installed. "
                "Install with: pip install llama-index-readers-github"
            )
            return

        params = self.ds.params
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        branch = params.get("branch", "main")
        filter_exts = params.get("filterExtensions", [])

        token = None
        if self.ds.auth:
            token_raw = getattr(self.ds.auth, "githubToken", None)
            if token_raw is None:
                # Try dict-style access for extra fields
                auth_dict = self.ds.auth.model_dump()
                token_raw = auth_dict.get("githubToken")
            if token_raw:
                token = self._resolve(token_raw)

        try:
            github_client = GithubClient(github_token=token, verbose=False)
            filter_tuple = (filter_exts, "INCLUDE") if filter_exts else None
            reader = GithubRepositoryReader(
                github_client=github_client,
                owner=owner,
                repo=repo,
                filter_file_extensions=filter_tuple,
                verbose=False,
                concurrent_requests=5,
            )
            docs = reader.load_data(branch=branch)
        except Exception as exc:
            logger.exception("Failed to fetch from GitHub %s/%s: %s", owner, repo, exc)
            return

        for doc in docs:
            meta = doc.metadata if hasattr(doc, "metadata") else {}
            meta["source_type"] = "GithubRepositoryReader"
            meta["owner"] = owner
            meta["repo"] = repo
            yield (doc.text if hasattr(doc, "text") else str(doc), meta)

    def state(self) -> Dict[str, Any]:
        params = self.ds.params
        return {
            "owner": params.get("owner", ""),
            "repo": params.get("repo", ""),
            "branch": params.get("branch", "main"),
        }


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


class GoogleDriveReaderAdapter(_BaseAdapter):
    """Wraps ``moonmind.indexers.google_drive_indexer.GoogleDriveIndexer``."""

    def plan(self) -> PlanResult:
        params = self.ds.params
        return PlanResult(
            estimated_docs=0,
            metadata={
                "folder_id": params.get("folderId"),
                "file_ids": params.get("fileIds", []),
            },
        )

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        try:
            from llama_index.readers.google import GoogleDriveReader
        except ImportError:
            logger.error(
                "llama_index.readers.google is not installed. "
                "Install with: pip install llama-index-readers-google"
            )
            return

        params = self.ds.params
        folder_id = params.get("folderId")
        file_ids = params.get("fileIds")

        creds_path = None
        if self.ds.auth:
            auth_dict = self.ds.auth.model_dump()
            raw = auth_dict.get("serviceAccountKeyPath")
            if raw:
                creds_path = self._resolve(raw)

        try:
            reader = GoogleDriveReader(credentials_path=creds_path)
            if file_ids:
                docs = reader.load_data(file_ids=file_ids)
            elif folder_id:
                docs = reader.load_data(folder_id=folder_id)
            else:
                logger.error("GoogleDrive adapter requires folderId or fileIds param")
                return
        except Exception as exc:
            logger.exception("Failed to fetch from Google Drive: %s", exc)
            return

        for doc in docs:
            meta = doc.metadata if hasattr(doc, "metadata") else {}
            meta["source_type"] = "GoogleDriveReader"
            yield (doc.text if hasattr(doc, "text") else str(doc), meta)

    def state(self) -> Dict[str, Any]:
        return {"folder_id": self.ds.params.get("folderId")}


# ---------------------------------------------------------------------------
# Simple Directory (Local)
# ---------------------------------------------------------------------------


class SimpleDirectoryReaderAdapter(_BaseAdapter):
    """Wraps ``moonmind.indexers.local_data_indexer.LocalDataIndexer``."""

    def plan(self) -> PlanResult:
        input_dir = self.ds.params.get("inputDir", ".")
        p = Path(input_dir)
        if not p.exists():
            return PlanResult(estimated_docs=0, metadata={"error": f"{p} not found"})

        recursive = self.ds.params.get("recursive", False)
        exts = self.ds.params.get("requiredExts")

        count = 0
        total_bytes = 0
        pattern = "**/*" if recursive else "*"
        for f in p.glob(pattern):
            if f.is_file():
                if exts and f.suffix not in exts:
                    continue
                count += 1
                total_bytes += f.stat().st_size

        return PlanResult(
            estimated_docs=count,
            estimated_size_bytes=total_bytes,
            metadata={"inputDir": input_dir, "recursive": recursive},
        )

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        input_dir = self.ds.params.get("inputDir", ".")
        recursive = self.ds.params.get("recursive", False)
        exts = self.ds.params.get("requiredExts")

        p = Path(input_dir)
        if not p.exists():
            logger.error("Input directory does not exist: %s", p)
            return

        pattern = "**/*" if recursive else "*"
        for f in p.glob(pattern):
            if not f.is_file():
                continue
            if exts and f.suffix not in exts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning("Could not read %s: %s", f, exc)
                continue

            meta = {
                "source_type": "SimpleDirectoryReader",
                "file_path": str(f),
                "file_name": f.name,
            }
            yield (text, meta)

    def state(self) -> Dict[str, Any]:
        return {"inputDir": self.ds.params.get("inputDir", ".")}


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


class ConfluenceReaderAdapter(_BaseAdapter):
    """Wraps ``moonmind.indexers.confluence_indexer.ConfluenceIndexer``."""

    def plan(self) -> PlanResult:
        params = self.ds.params
        return PlanResult(
            estimated_docs=params.get("maxPages", 100),
            metadata={
                "spaceKey": params.get("spaceKey"),
            },
        )

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        try:
            from llama_index.readers.confluence import ConfluenceReader
        except ImportError:
            logger.error(
                "llama_index.readers.confluence is not installed. "
                "Install with: pip install llama-index-readers-confluence"
            )
            return

        params = self.ds.params
        space_key = params.get("spaceKey")
        max_pages = params.get("maxPages", 100)

        base_url = None
        token = None
        if self.ds.auth:
            auth_dict = self.ds.auth.model_dump()
            base_url = self._resolve(auth_dict.get("baseUrl", ""))
            token = self._resolve(auth_dict.get("token", ""))

        if not base_url or not token:
            logger.error("Confluence adapter requires auth.baseUrl and auth.token")
            return

        try:
            reader = ConfluenceReader(
                base_url=base_url,
                oauth2={"token": token},
                cloud=True,
            )
            if space_key:
                docs = reader.load_data(
                    space_key=space_key, max_num_results=max_pages
                )
            else:
                logger.error("Confluence adapter requires params.spaceKey")
                return
        except Exception as exc:
            logger.exception("Failed to fetch from Confluence: %s", exc)
            return

        for doc in docs:
            meta = doc.metadata if hasattr(doc, "metadata") else {}
            meta["source_type"] = "ConfluenceReader"
            yield (doc.text if hasattr(doc, "text") else str(doc), meta)

    def state(self) -> Dict[str, Any]:
        return {"spaceKey": self.ds.params.get("spaceKey")}


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------


def register_builtin_adapters() -> None:
    """Register all built-in reader adapters."""
    register_adapter("GithubRepositoryReader", GitHubReaderAdapter)
    register_adapter("GoogleDriveReader", GoogleDriveReaderAdapter)
    register_adapter("SimpleDirectoryReader", SimpleDirectoryReaderAdapter)
    register_adapter("ConfluenceReader", ConfluenceReaderAdapter)


# Auto-register on import
register_builtin_adapters()
