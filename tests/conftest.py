import pytest

from moonmind.config.settings import settings
from api_service.auth import _DEFAULT_USER_ID

@pytest.fixture
def disabled_env_keys(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", _DEFAULT_USER_ID, raising=False)
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_EMAIL", "seed@example.com", raising=False)
    monkeypatch.setattr(settings.openai, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings.google, "google_api_key", "g-test", raising=False)
    yield

@pytest.fixture
def keycloak_mode(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak", raising=False)
    yield
