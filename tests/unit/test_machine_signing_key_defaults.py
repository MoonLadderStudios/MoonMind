"""Machine authority never inherits a publicly known signing key."""

from importlib import import_module

import pytest

from moonmind.config.settings import SecuritySettings

settings_module = import_module("moonmind.config.settings")


def test_omitted_signing_key_is_private_durable_and_deployment_specific(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setattr(settings_module, "ENV_FILE", tmp_path / ".env")
    first = SecuritySettings(_env_file=None).JWT_SECRET_KEY
    second = SecuritySettings(_env_file=None).JWT_SECRET_KEY
    assert first == second
    assert first != "test_jwt_secret_key"
    assert len(first) >= 32
    key = tmp_path / "var" / "secrets" / "machine_signing_key"
    assert key.is_file()
    assert key.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(settings_module, "ENV_FILE", tmp_path / "other" / ".env")
    assert SecuritySettings(_env_file=None).JWT_SECRET_KEY != first


@pytest.mark.parametrize(
    "key", ["test_jwt_secret_key", "replace_with_a_strong_random_jwt_secret", "short"]
)
def test_explicit_insecure_signing_key_fails_closed(key):
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        SecuritySettings(JWT_SECRET_KEY=key, _env_file=None)


def test_explicit_signing_key_is_preserved():
    key = "deployment-explicit-signing-key-0123456789"
    assert SecuritySettings(JWT_SECRET_KEY=key, _env_file=None).JWT_SECRET_KEY == key


def test_security_keys_load_from_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    key = "deployment-dotenv-machine-key-0123456789"
    dotenv.write_text(
        f"JWT_SECRET_KEY={key}\nENCRYPTION_MASTER_KEY=operator-managed-key\n"
    )
    config = SecuritySettings(_env_file=dotenv)
    assert config.JWT_SECRET_KEY == key
    assert config.ENCRYPTION_MASTER_KEY == "operator-managed-key"
