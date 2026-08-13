"""Reconcile historical duplicate chat authorities for MoonLadderStudios/MoonMind#3685."""

from __future__ import annotations

import argparse
import asyncio
import json

from api_service.db.base import async_session_maker
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore


async def _run(*, apply: bool) -> None:
    report = await OmnigentBridgeSessionStore(
        async_session_maker
    ).reconcile_canonical_chat_authorities(dry_run=not apply)
    print(json.dumps(report, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canonicalize duplicate Omnigent Workflow Chat bindings."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist unambiguous aliases; default is a dry run.",
    )
    args = parser.parse_args()
    asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    main()
