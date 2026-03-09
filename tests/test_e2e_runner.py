"""Unit tests to verify the E2E test script can be executed."""

import sys
from pathlib import Path


def test_e2e_script_exists_and_is_executable():
    """Verify that test_temporal_e2e.py exists and can be imported."""
    script_path = Path("scripts/test_temporal_e2e.py")
    assert script_path.exists(), "The E2E test script must exist."

    # Try compiling to ensure syntax is valid
    try:
        py_compile = __import__("py_compile")
        py_compile.compile(str(script_path), doraise=True)
    except Exception as e:
        assert False, f"Syntax error in script: {e}"


def test_e2e_script_dry_run(monkeypatch):
    """If there's a dry-run or unit-test mode, we test it. Here we just test we can import it."""
    try:
        sys.path.append(str(Path("scripts").absolute()))
        import test_temporal_e2e

        assert hasattr(test_temporal_e2e, "main")
        assert hasattr(test_temporal_e2e, "wait_for_api")
    finally:
        sys.path.remove(str(Path("scripts").absolute()))
