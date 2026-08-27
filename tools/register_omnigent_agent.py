#!/usr/bin/env python3
"""Register one portable agent bundle in an Omnigent deployment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def register_agent(agent_source: Path, *, database_url: str, artifact_dir: Path) -> str:
    """Execute Omnigent's canonical startup registration entrypoint."""

    from omnigent.cli import _preregister_agent
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore

    artifact_store = LocalArtifactStore(str(artifact_dir))
    agent_store = SqlAlchemyAgentStore(database_url)
    agent_cache = AgentCache(
        artifact_store=artifact_store,
        cache_dir=artifact_dir / ".cache",
    )
    agent_id = _preregister_agent(
        agent_source,
        agent_store,
        artifact_store,
        agent_cache,
    )
    if not agent_id:
        raise RuntimeError(f"Omnigent did not register agent bundle {agent_source}")
    return str(agent_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_source", type=Path)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("DATABASE_URL is required")
    artifact_dir = Path(os.environ.get("ARTIFACT_DIR", "/data/artifacts"))
    agent_id = register_agent(
        args.agent_source,
        database_url=database_url,
        artifact_dir=artifact_dir,
    )
    print(f"registered Omnigent agent {agent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
