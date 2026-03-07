import os
from unittest.mock import patch

from moonmind.config.settings import SecuritySettings


def test_security_settings_defaults_to_none():
    """Test that SecuritySettings defaults JWT_SECRET_KEY and ENCRYPTION_MASTER_KEY to None."""
    # Ensure environment variables are not set during the test
    with patch.dict(os.environ, clear=True):
        settings = SecuritySettings()
        assert settings.JWT_SECRET_KEY is None
        assert settings.ENCRYPTION_MASTER_KEY is None


def test_security_settings_reads_from_env():
    """Test that SecuritySettings correctly reads JWT_SECRET_KEY and ENCRYPTION_MASTER_KEY from the environment."""
    mock_env = {
        "JWT_SECRET_KEY": "env_jwt_secret",
        "ENCRYPTION_MASTER_KEY": "env_encryption_key",
    }
    with patch.dict(os.environ, mock_env, clear=True):
        settings = SecuritySettings()
        assert settings.JWT_SECRET_KEY == "env_jwt_secret"
        assert settings.ENCRYPTION_MASTER_KEY == "env_encryption_key"
