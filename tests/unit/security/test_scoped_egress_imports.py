"""Pure scoped policy rendering and HTTP fixtures need no application settings."""

import subprocess
import sys
from pathlib import Path


def test_scoped_render_and_actual_http_fixture_start_without_app_settings():
    root = Path(__file__).resolve().parents[3]
    script = r"""
import http.client
import importlib.abc
import runpy
import sys
import tempfile
from pathlib import Path

class NoApplicationSettings(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "moonmind.config.settings":
            raise AssertionError("pure scoped import initialized application settings")
        return None

sys.meta_path.insert(0, NoApplicationSettings())
from moonmind.security.egress_policy import ScopedEgressPolicy
from moonmind.security.scoped_egress import render_scoped_proxy

policy = ScopedEgressPolicy.model_validate({
    "policyRef": "fixture", "allowedWorkflowIds": ["fixture"],
    "allowedImageSourceRefs": ["fixture"], "destinations": [{
        "host": "scope.example.test", "port": 8080,
        "protocol": "http", "purpose": "live-target",
    }],
})
config, hosts = render_scoped_proxy(policy, {"scope.example.test": ("93.184.216.34",)})
assert b"http_access deny all" in config
assert b"93.184.216.34 scope.example.test" in hosts
fixture = runpy.run_path("tests/integration/security/test_scoped_egress_connections.py")
with tempfile.TemporaryDirectory() as directory:
    with fixture["fixture_servers"](Path(directory)) as (http_port, _):
        connection = http.client.HTTPConnection("127.0.0.1", http_port, timeout=3)
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert b"admitted-scope-fixture" in response.read()
        connection.close()
assert "moonmind.config.settings" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
