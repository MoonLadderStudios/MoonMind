#!/usr/bin/env python3
"""Fail CI unless MoonMind's Alembic graph is linear and storable.

Two invariants are enforced:

* exactly one head, so migrations apply in a single deterministic order, and
* every revision id fits ``alembic_version.version_num``, which Alembic creates
  as ``VARCHAR(32)``. A longer id passes every offline graph check and then
  fails at ``UPDATE alembic_version`` against a real database, after the
  migration body has already run.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Alembic creates alembic_version.version_num as VARCHAR(32).
VERSION_NUM_MAX_LENGTH = 32


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migration_dir = root / "api_service" / "migrations"

    config = Config(str(migration_dir / "alembic.ini"))
    # Make the check independent of the current working directory.
    config.set_main_option("script_location", str(migration_dir))

    script = ScriptDirectory.from_config(config)

    oversized = sorted(
        revision.revision
        for revision in script.walk_revisions()
        if len(revision.revision) > VERSION_NUM_MAX_LENGTH
    )
    if oversized:
        print(
            "::error title=Alembic revision id is too long::"
            f"alembic_version.version_num holds {VERSION_NUM_MAX_LENGTH} "
            f"characters; these revision ids do not fit: {', '.join(oversized)}"
        )
        print(
            "Rename the revision id (and its file) to fit, updating "
            "down_revision in any child migration in the same change."
        )
        return 1

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
        f"Expected exactly one head, found {len(heads)}: {', '.join(rendered_heads)}"
    )
    print(
        "Rebase onto current main and regenerate/reparent an unshipped "
        "migration, or create an Alembic merge revision when both migration "
        "branches must remain valid."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
