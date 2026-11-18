"""Exercise the release smoke's fail-closed decision with simulated HTTP peers."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "mode,success",
    [
        ("ready", True),
        ("unauthorized", False),
        ("not-found", False),
        ("wrong-version", False),
        ("plugin-failed", False),
    ],
)
def test_v2_image_smoke_requires_authenticated_active_sdk_plugin(
    tmp_path, mode, success
):
    if shutil.which("jq") is None:
        pytest.skip("the host-image smoke uses jq")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    seed = tmp_path / "seed"
    seed.mkdir()

    def executable(name, source):
        path = binaries / name
        path.write_text(source)
        path.chmod(0o755)

    executable(
        "docker",
        f"#!{sys.executable}\n" + """
import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('--network') + 1] == 'none'
assert '--read-only' in args
assert args[args.index('--user') + 1] == '1000:1000'
script = args[-1].replace('/opt/moonmind/opencode-npm-cache', os.environ['TEST_SEED'])
result = subprocess.run(['/bin/sh', '-eu', '-c', script], env=os.environ)
sys.exit(result.returncode)
""",
    )
    executable(
        "npm",
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" > \"$HOME/npm-args\"\nmkdir -p node_modules/@opencode/plugin\necho '{}' > node_modules/@opencode/plugin/package.json\n",
    )
    executable(
        "opencode",
        "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'opencode v2.0.32'; else exec sleep 20; fi\n",
    )
    executable(
        "curl",
        f"#!{sys.executable}\n" + """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('-u') + 1] == 'opencode:moonmind-offline-probe'
mode = os.environ['TEST_MODE']
path = Path(args[args.index('-o') + 1])
if args[-1].endswith('/api/info'):
    body = {'version': '1.18.11' if mode == 'wrong-version' else '2.0.32'}
else:
    assert args[-1].endswith('/api/plugin')
    status = 'failed' if mode == 'plugin-failed' else 'active'
    body = {'data': [{'id': 'moonmind-offline-probe', 'state': {'status': status}}]}
    # A marker alone is insufficient: the API must also report active.
    (path.parent / 'plugin-loaded').write_text('ready')
path.write_text(json.dumps(body))
print({'unauthorized': '401', 'not-found': '404'}.get(mode, '200'), end='')
""",
    )
    result = subprocess.run(
        [
            "sh",
            "services/omnigent/moonmind-host/verify-warm-plugin-cache.sh",
            "test-image",
            "1",
        ],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "HOME": str(home),
            "MOONMIND_READY_TIMEOUT": "1",
            "TEST_SEED": str(seed),
            "TEST_MODE": mode,
        },
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    assert "--offline" in (home / "npm-args").read_text()
