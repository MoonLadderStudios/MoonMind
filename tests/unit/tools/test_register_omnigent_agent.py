from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _module(name: str, **members: object) -> ModuleType:
    module = ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    return module


def test_registration_normalizes_compose_postgres_url(monkeypatch, tmp_path: Path):
    observed: dict[str, object] = {}

    class AgentStore:
        def __init__(self, database_url: str) -> None:
            observed["database_url"] = database_url

    class ArtifactStore:
        def __init__(self, location: str) -> None:
            observed["artifact_location"] = location

    class Cache:
        def __init__(self, **kwargs: object) -> None:
            observed["cache"] = kwargs

    def preregister(*args: object) -> str:
        observed["preregister"] = args
        return "ag_live123"

    monkeypatch.setitem(sys.modules, "omnigent", _module("omnigent"))
    monkeypatch.setitem(
        sys.modules,
        "omnigent.cli",
        _module("omnigent.cli", _preregister_agent=preregister),
    )
    monkeypatch.setitem(sys.modules, "omnigent.db", _module("omnigent.db"))
    monkeypatch.setitem(
        sys.modules,
        "omnigent.db.utils",
        _module(
            "omnigent.db.utils",
            normalize_database_url=lambda url: url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        ),
    )
    monkeypatch.setitem(sys.modules, "omnigent.runtime", _module("omnigent.runtime"))
    monkeypatch.setitem(
        sys.modules,
        "omnigent.runtime.agent_cache",
        _module("omnigent.runtime.agent_cache", AgentCache=Cache),
    )
    monkeypatch.setitem(sys.modules, "omnigent.stores", _module("omnigent.stores"))
    monkeypatch.setitem(
        sys.modules,
        "omnigent.stores.agent_store",
        _module("omnigent.stores.agent_store"),
    )
    monkeypatch.setitem(
        sys.modules,
        "omnigent.stores.agent_store.sqlalchemy_store",
        _module(
            "omnigent.stores.agent_store.sqlalchemy_store",
            SqlAlchemyAgentStore=AgentStore,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "omnigent.stores.artifact_store",
        _module("omnigent.stores.artifact_store"),
    )
    monkeypatch.setitem(
        sys.modules,
        "omnigent.stores.artifact_store.local",
        _module(
            "omnigent.stores.artifact_store.local",
            LocalArtifactStore=ArtifactStore,
        ),
    )

    spec = importlib.util.spec_from_file_location(
        "register_omnigent_agent", Path("tools/register_omnigent_agent.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.register_agent(
        tmp_path / "agent",
        database_url="postgresql://omnigent:secret@postgres:5432/omnigent",
        artifact_dir=tmp_path / "artifacts",
    )

    assert result == "ag_live123"
    assert observed["database_url"] == (
        "postgresql+psycopg://omnigent:secret@postgres:5432/omnigent"
    )
