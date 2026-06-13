import builtins
import importlib
import sys


def test_cli_import_does_not_require_api_service(monkeypatch):
    """RAG/manifest CLI startup must not load API-only workflow schema modules."""

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "api_service" or name.startswith("api_service."):
            raise ModuleNotFoundError(name)
        return real_import(name, globals, locals, fromlist, level)

    for module_name in list(sys.modules):
        if module_name == "api_service" or module_name.startswith(
            (
                "api_service.",
                "moonmind.cli",
                "moonmind.manifest",
                "moonmind.schemas",
            )
        ):
            sys.modules.pop(module_name, None)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    cli_module = importlib.import_module("moonmind.cli")

    assert cli_module.app is not None
