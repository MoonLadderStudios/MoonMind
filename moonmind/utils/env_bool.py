from typing import Any, Optional

_TRUTHY = frozenset({"y", "yes", "t", "true", "on", "1"})
_FALSY = frozenset({"n", "no", "f", "false", "off", "0"})


def _strtobool(value: str) -> int:
    text = value.strip().lower()
    if text in _TRUTHY:
        return 1
    if text in _FALSY:
        return 0
    raise ValueError(f"invalid truth value {value!r}")

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
        # _strtobool returns 0 or 1, so cast to bool.
        return bool(_strtobool(str(value)))
    except (ValueError, TypeError):
        # Unparsable value – fall back to default
        return default
