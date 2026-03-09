"""Unit tests to verify the teardown script can be executed."""

import os
import sys
from pathlib import Path

def test_teardown_script_exists_and_is_executable():
    """Verify that teardown_temporal.py exists and is syntactically valid."""
    script_path = Path("scripts/teardown_temporal.py")
    assert script_path.exists(), "The teardown script must exist."

    # Try compiling to ensure syntax is valid
    try:
        py_compile = __import__("py_compile")
        py_compile.compile(str(script_path), doraise=True)
    except Exception as e:
        assert False, f"Syntax error in script: {e}"

def test_teardown_script_importable(monkeypatch):
    """Ensure the script can be imported without executing its side effects."""
    try:
        sys.path.append(str(Path("scripts").absolute()))
        import teardown_temporal
        assert hasattr(teardown_temporal, "main")
    finally:
        sys.path.remove(str(Path("scripts").absolute()))
