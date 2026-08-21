#!/usr/bin/env python3
"""Generate or verify the exact Omnigent route inventory for #3635."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from moonmind.omnigent.native_ui_route_inventory import (  # noqa: E402
    generate_native_ui_route_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/omnigent/native_ui_network_contract_v2.json"),
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--facade-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--omnigent-revision")
    parser.add_argument("--compiled-ui-root", type=Path)
    parser.add_argument("--omnigent-server-image")
    parser.add_argument("--omnigent-host-image")
    parser.add_argument("--moonmind-facade-image")
    parser.add_argument("--observed-omnigent-host-digest")
    parser.add_argument("--observed-moonmind-facade-digest")
    parser.add_argument("--observed-moonmind-harness-digest")
    args = parser.parse_args()

    repo_root = args.source_root.resolve()
    images = {
        key: value
        for key, value in {
            "omnigentServer": args.omnigent_server_image,
            "omnigentHost": args.omnigent_host_image,
            "moonmindFacade": args.moonmind_facade_image,
        }.items()
        if value
    }
    generated = json.dumps(
        generate_native_ui_route_inventory(
            repo_root,
            facade_root=args.facade_root,
            omnigent_revision=args.omnigent_revision,
            compiled_ui_root=args.compiled_ui_root,
            deployable_images=images,
            observed_artifact_digests={
                key: value
                for key, value in {
                    "omnigentHost": args.observed_omnigent_host_digest,
                    "moonmindFacade": args.observed_moonmind_facade_digest,
                    "moonmindHarness": args.observed_moonmind_harness_digest,
                }.items()
                if value
            },
        ),
        indent=2,
        sort_keys=True,
    ) + "\n"
    output = args.output if args.output.is_absolute() else _REPO_ROOT / args.output
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != generated:
            print(
                "exact Omnigent route inventory drifted; regenerate the versioned "
                "classification fixture",
                file=sys.stderr,
            )
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
