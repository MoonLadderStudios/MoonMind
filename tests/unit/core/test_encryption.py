import os
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet

from api_service.core import encryption
from moonmind.config.settings import SecuritySettings


@pytest.fixture(autouse=True)
def reset_encryption_key():
    """Reset the module-level cached key before and after each test."""
    encryption._ACTIVE_ENCRYPTION_KEY = None
    yield
    encryption._ACTIVE_ENCRYPTION_KEY = None


@pytest.fixture
def mock_settings():
    with patch("api_service.core.encryption.settings") as mock_set:
        mock_secure = MagicMock()
        mock_secure.ENCRYPTION_MASTER_KEY = None
        mock_set.security = mock_secure
        
        mock_workflow = MagicMock()
        mock_workflow.repo_root = "/tmp/mock_repo"
        mock_set.workflow = mock_workflow
        
        yield mock_set


def test_get_encryption_key_from_settings(mock_settings):
    """Test that settings override takes highest precedence."""
    mock_settings.security.ENCRYPTION_MASTER_KEY = "dummy_settings_key"
    
    key = encryption.get_encryption_key()
    assert key == "dummy_settings_key"
    assert encryption._ACTIVE_ENCRYPTION_KEY == "dummy_settings_key"


@patch("api_service.core.encryption.Path")
def test_get_encryption_key_from_docker_secret(mock_path, mock_settings):
    """Test falling back to Docker secret."""
    mock_settings.security.ENCRYPTION_MASTER_KEY = None
    
    docker_secret = MagicMock()
    docker_secret.is_file.return_value = True
    docker_secret.read_text.return_value = "dummy_docker_key"

    def side_effect(path):
        if str(path) == "/run/secrets/moonmind_master_key":
            return docker_secret
        return MagicMock() # fallback for other paths

    mock_path.side_effect = side_effect
    
    key = encryption.get_encryption_key()
    assert key == "dummy_docker_key"


def test_get_encryption_key_from_local_file_generated(mock_settings, tmp_path):
    """Test that if no key exists, one is generated securely."""
    mock_settings.workflow.repo_root = str(tmp_path)
    
    key = encryption.get_encryption_key()
    
    # Assert key is valid Fernet key
    assert Fernet(key.encode("utf-8"))
    
    # Check that file was created
    local_key_file = tmp_path / "var" / "secrets" / "encryption_master_key"
    assert local_key_file.is_file()
    assert local_key_file.read_text(encoding="utf-8") == key
    
    # Check permissions (approximate check for 0o600 or rw-------)
    st_mode = os.stat(local_key_file).st_mode
    assert bool(st_mode & stat.S_IRUSR)
    assert bool(st_mode & stat.S_IWUSR)
    # Should not be readable by group or others
    assert not bool(st_mode & stat.S_IRGRP)
    assert not bool(st_mode & stat.S_IROTH)


def test_get_encryption_key_from_local_file_reads_existing(mock_settings, tmp_path):
    """Test that existing local key is read rather than overwritten."""
    mock_settings.workflow.repo_root = str(tmp_path)
    
    local_key_file = tmp_path / "var" / "secrets" / "encryption_master_key"
    local_key_file.parent.mkdir(parents=True, exist_ok=True)
    existing_key = Fernet.generate_key().decode("utf-8")
    local_key_file.write_text(existing_key, encoding="utf-8")
    
    key = encryption.get_encryption_key()
    assert key == existing_key


def test_get_encryption_key_cached_in_memory(mock_settings):
    """Test that subsequent calls return the cached key without checking files/settings."""
    encryption._ACTIVE_ENCRYPTION_KEY = "cached_key"
    key = encryption.get_encryption_key()
    assert key == "cached_key"
    
    # Even if settings change, cached key is retained until process exit
    mock_settings.security.ENCRYPTION_MASTER_KEY = "other_key"
    assert encryption.get_encryption_key() == "cached_key"
