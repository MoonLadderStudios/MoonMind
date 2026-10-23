import pytest

from moonmind.auth.env_provider import EnvAuthProvider

@pytest.mark.asyncio
async def test_env_provider_returns_secret(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_KEY", "supersecretvalue")
    provider = EnvAuthProvider()

    secret = await provider.get_secret(key="TEST_SECRET_KEY")

    assert secret == "supersecretvalue"
    # RedactedSecret's repr contains 'redacted'
    assert "redacted" in repr(secret).lower()

@pytest.mark.asyncio
async def test_env_provider_returns_none_when_missing(monkeypatch):
    monkeypatch.delenv("NON_EXISTENT_KEY", raising=False)
    provider = EnvAuthProvider()

    secret = await provider.get_secret(key="NON_EXISTENT_KEY")

    assert secret is None

@pytest.mark.asyncio
async def test_env_provider_rejects_retired_person_arguments(monkeypatch):
    monkeypatch.setenv("ANOTHER_KEY", "anothervalue")
    provider = EnvAuthProvider()
    with pytest.raises(TypeError):
        await provider.get_secret(key="ANOTHER_KEY", user=None)
