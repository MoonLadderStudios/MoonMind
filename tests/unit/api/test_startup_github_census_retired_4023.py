"""Ordinary startup no longer re-imports legacy GitHub token sources.

MoonLadderStudios/MoonMind#4023: the one-time migration records the legacy
GitHub credential reference on the default repository connection. Startup
must not copy ``GITHUB_TOKEN``/``GITHUB_PAT`` into managed secrets on every
boot, which kept a second, silently refreshed copy of the token. The
unrelated Atlassian import is unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


def test_startup_secret_sync_skips_github_token_sources(monkeypatch, tmp_path):
    import api_service.main as api_main
    from api_service.services.secrets import SecretsService
    from moonmind.config import paths

    imported: list[dict[str, str]] = []

    async def _import_from_env(_session, values, *, overwrite_active):
        imported.append(dict(values))
        return len(values)

    @asynccontextmanager
    async def _session():
        yield object()

    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(SecretsService, "import_from_env", _import_from_env)
    monkeypatch.setattr(api_main, "get_async_session_context", _session)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-startup-token")
    monkeypatch.setenv("GITHUB_PAT", "ghp-startup-pat")
    monkeypatch.setenv("ATLASSIAN_API_KEY", "atlassian-key")

    asyncio.run(api_main._sync_env_managed_secrets())

    assert imported == [{"ATLASSIAN_API_KEY": "atlassian-key"}]


def test_startup_secret_sync_without_atlassian_imports_nothing(monkeypatch, tmp_path):
    import api_service.main as api_main
    from api_service.services.secrets import SecretsService
    from moonmind.config import paths

    calls: list[object] = []

    async def _import_from_env(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(SecretsService, "import_from_env", _import_from_env)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-startup-token")
    monkeypatch.delenv("ATLASSIAN_API_KEY", raising=False)

    assert asyncio.run(api_main._sync_env_managed_secrets()) == 0
    assert calls == []
