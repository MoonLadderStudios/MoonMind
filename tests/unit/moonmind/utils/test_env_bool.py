import pytest

from moonmind.utils.env_bool import env_to_bool


class TestEnvToBool:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("t", True),
            ("yes", True),
            ("y", True),
            ("on", True),
            ("1", True),
            (1, True),
            (True, True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("f", False),
            ("no", False),
            ("n", False),
            ("off", False),
            ("0", False),
            (0, False),
            (False, False),
        ],
    )
    def test_env_to_bool_valid_representations(self, value, expected):
        """Test standard truthy and falsy representations."""
        assert env_to_bool(value) is expected

    @pytest.mark.parametrize(
        "value, default, expected",
        [
            (None, False, False),
            (None, True, True),
            ("", False, False),
            ("", True, True),
        ],
    )
    def test_env_to_bool_empty_or_none(self, value, default, expected):
        """Test behavior when value is None or an empty string."""
        assert env_to_bool(value, default=default) is expected

    @pytest.mark.parametrize(
        "value, default, expected",
        [
            ("invalid", False, False),
            ("invalid", True, True),
            ("2", False, False),
            ("2", True, True),
            (-1, False, False),
            (-1, True, True),
            (["true"], False, False),
            (["true"], True, True),
            ({"key": "value"}, False, False),
            ({"key": "value"}, True, True),
        ],
    )
    def test_env_to_bool_unparsable_values(self, value, default, expected):
        """Test that unparsable values return the specified default."""
        assert env_to_bool(value, default=default) is expected

    @pytest.mark.parametrize(
        "value",
        [
            "invalid",
            None,
            "",
        ],
    )
    def test_env_to_bool_default_argument(self, value):
        """Test the default value of the default argument."""
        # By default, default=False
        assert env_to_bool(value) is False
