#!/usr/bin/env python3
"""Fail CI unless MoonMind's Alembic graph has exactly one head."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migration_dir = root / "api_service" / "migrations"

    config = Config(str(migration_dir / "alembic.ini"))
    # Make the check independent of the current working directory.
    config.set_main_option("script_location", str(migration_dir))

    script = ScriptDirectory.from_config(config)
    heads = tuple(script.get_heads())

    if len(heads) == 1:
        print(f"Alembic migration graph has one head: {heads[0]}")
        return 0

    rendered_heads: list[str] = []
    for revision_id in heads:
        revision = script.get_revision(revision_id)
        doc = (revision.doc or "").strip() if revision else ""
        summary = doc.splitlines()[0] if doc else ""
        rendered_heads.append(
            f"{revision_id}: {summary}" if summary else revision_id
        )

    print(
        "::error title=Alembic migration graph is not linear::"
        f"Expected exactly one head, found {len(heads)}: "
        + ", ".join(rendered_heads)
    )
    print(
        "Rebase onto current main and regenerate/reparent an unshipped "
        "migration, or create an Alembic merge revision when both migration "
        "branches must remain valid."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
