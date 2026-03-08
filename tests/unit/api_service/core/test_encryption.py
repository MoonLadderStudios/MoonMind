from unittest.mock import patch

import pytest

from api_service.core.encryption import get_encryption_key


def test_get_encryption_key_success():
    """Test that get_encryption_key returns the key when it is set."""
    with patch("api_service.core.encryption.settings") as mock_settings:
        mock_settings.security.ENCRYPTION_MASTER_KEY = "super_secret_key"
        key = get_encryption_key()
        assert key == "super_secret_key"


def test_get_encryption_key_missing_raises_error():
    """Test that get_encryption_key raises ValueError when key is missing."""
    with patch("api_service.core.encryption.settings") as mock_settings:
        mock_settings.security.ENCRYPTION_MASTER_KEY = None
        with pytest.raises(
            ValueError,
            match="ENCRYPTION_MASTER_KEY is not set in the application settings",
        ):
            get_encryption_key()


def test_get_encryption_key_empty_string_raises_error():
    """Test that get_encryption_key raises ValueError when key is an empty string."""
    with patch("api_service.core.encryption.settings") as mock_settings:
        mock_settings.security.ENCRYPTION_MASTER_KEY = ""
        with pytest.raises(
            ValueError,
            match="ENCRYPTION_MASTER_KEY is not set in the application settings",
        ):
            get_encryption_key()
