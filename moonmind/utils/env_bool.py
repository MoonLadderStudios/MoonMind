try:  # Python < 3.12
    from distutils.util import strtobool  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback for Python >= 3.12
    from setuptools._distutils.util import strtobool
from typing import Any, Optional


def env_to_bool(value: Optional[Any], default: bool = False) -> bool:
    """Convert various string/int/boolean representations to a proper bool.

    Accepts common truthy/falsy strings such as 'true', 'false', '1', '0',
    'yes', 'no', 'on', 'off', regardless of case. If the value is None or
    an empty string, returns the supplied default.
    """
    # Already a boolean? Return as-is.
    if isinstance(value, bool):
        return value

    # If value is None or empty, yield the default
    if value in (None, ""):
        return default

    try:
        # strtobool returns 0 or 1, so cast to bool.
        return bool(strtobool(str(value)))
    except (ValueError, TypeError):
        # Unparsable value – fall back to default
        return default
