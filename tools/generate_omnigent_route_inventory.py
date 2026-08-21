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
    args = parser.parse_args()

    repo_root = _REPO_ROOT
    generated = json.dumps(
        generate_native_ui_route_inventory(repo_root),
        indent=2,
        sort_keys=True,
    ) + "\n"
    output = args.output if args.output.is_absolute() else repo_root / args.output
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
