from pathlib import Path
import subprocess
import sys
import textwrap


def test_cli_import_does_not_require_api_service():
    """RAG/manifest CLI startup must not load API-only workflow schema modules."""

    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockApiService(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "api_service" or fullname.startswith("api_service."):
                    raise ModuleNotFoundError(fullname)
                return None

        sys.meta_path.insert(0, BlockApiService())
        cli_module = __import__("moonmind.cli", fromlist=["app"])
        assert cli_module.app is not None
        assert "api_service" not in sys.modules
        assert not any(name.startswith("api_service.") for name in sys.modules)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[3],
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
