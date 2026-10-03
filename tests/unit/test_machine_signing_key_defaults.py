"""Machine authority never inherits a publicly known signing key."""

import hashlib
import subprocess
import sys
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
    "key",
    [
        "test_jwt_secret_key",
        "replace-with-a-strong-random-jwt-secret",
        "replace_with_other_weak_material",
        "devsecret",
        "short",
    ],
)
def test_explicit_insecure_signing_key_fails_closed(key):
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        SecuritySettings(JWT_SECRET_KEY=key, _env_file=None)


@pytest.mark.parametrize("source", ["field", "retained_dotenv"])
@pytest.mark.parametrize("existing_key", [None, b"K" * 32])
def test_legacy_template_signing_key_uses_existing_durable_owner(
    tmp_path, monkeypatch, source, existing_key
):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(settings_module, "ENV_FILE", dotenv)
    legacy = "replace_with_a_strong_random_jwt_secret"
    retained = f'JWT_SECRET_KEY="{legacy}"\nENCRYPTION_MASTER_KEY=retained-key\n'
    dotenv.write_text(retained)
    key_path = tmp_path / "var" / "secrets" / "machine_signing_key"
    if existing_key is not None:
        key_path.parent.mkdir(parents=True)
        key_path.write_bytes(existing_key)

    if source == "retained_dotenv":
        config = SecuritySettings(_env_file=dotenv)
        assert config.ENCRYPTION_MASTER_KEY == "retained-key"
    else:
        config = SecuritySettings(JWT_SECRET_KEY=legacy, _env_file=None)

    omitted = SecuritySettings(JWT_SECRET_KEY=None, _env_file=None)
    assert config.JWT_SECRET_KEY == omitted.JWT_SECRET_KEY
    assert config.JWT_SECRET_KEY == key_path.read_bytes().hex()
    assert config.JWT_SECRET_KEY != legacy
    if existing_key is not None:
        assert key_path.read_bytes() == existing_key
    else:
        assert key_path.stat().st_mode & 0o777 == 0o600
    assert dotenv.read_text() == retained


def test_legacy_template_signing_key_survives_independent_process_restart(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text('JWT_SECRET_KEY="replace_with_a_strong_random_jwt_secret"\n')
    script = """
import hashlib
import sys
from importlib import import_module
from pathlib import Path

module = import_module("moonmind.config.settings")
module.ENV_FILE = Path(sys.argv[1])
key = module.SecuritySettings(_env_file=sys.argv[1]).JWT_SECRET_KEY
print(hashlib.sha256(key.encode()).hexdigest())
"""
    command = [sys.executable, "-c", script, str(dotenv)]
    first = subprocess.check_output(command, text=True).strip()
    key_path = tmp_path / "var" / "secrets" / "machine_signing_key"
    material = key_path.read_bytes()
    second = subprocess.check_output(command, text=True).strip()
    assert first == second
    assert first == hashlib.sha256(material.hex().encode()).hexdigest()
    assert key_path.read_bytes() == material


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
