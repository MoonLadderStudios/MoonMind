"""Operator-only identity migration command (K3, #4119).

Usage (operator shell with database access)::

    python -m api_service.scripts.migrate_identities \
        --mapping mapping.json --enrollment enrollment.json --dry-run
    python -m api_service.scripts.migrate_identities \
        --mapping mapping.json --enrollment enrollment.json \
        --preflight-digest <digest-from-dry-run> --apply --confirm-operator

``mapping.json`` is the reviewed source-provider-to-issuer mapping
(``{"keycloak": "https://idp.example.invalid/realms/moonmind", ...}``).
The ``"keycloak"`` key names the historical source provider being migrated
away from; it is migration input evidence, not an active ``AUTH_PROVIDER``
selector (retired selectors are rejected per
``docs/Security/AuthenticationContracts.md``).
``enrollment.json`` is the verified target enrollment evidence mapping
``str(user_id)`` to ``{"issuer": ..., "subject": ..., "source_provider": ...}``.

The apply path is bound to the dry-run preflight digest: changed mapping,
enrollment, schema, or database state invalidates the preflight instead of
applying stale assumptions. Apply additionally requires
``--confirm-operator``; without it the command refuses. All output is
sanitized (no identity exports, password hashes, or tokens).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.auth import _DEFAULT_USER_EMAIL
from api_service.db.models import Base
from api_service.services.identity_migration import (
    ConcurrentApplyError,
    MigrationAuthorizationError,
    StalePreflightError,
    apply_migration,
    preflight,
)
from moonmind.config.settings import settings


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _redact_summary(payload: dict[str, Any]) -> dict[str, Any]:
    # Defense in depth: the services already sanitize, but the CLI never
    # prints mapping values, enrollment evidence, or row-level identity
    # material beyond UUIDs and issue codes.
    summary: dict[str, Any] = {}
    for key in ("digest", "migrated", "skipped", "blocked", "total_users",
                "mapped_users", "unmapped_users", "orphan_profiles", "issues"):
        if key in payload:
            summary[key] = payload[key]
    return summary


async def _run(args: argparse.Namespace) -> int:
    provider_to_issuer = _load_json(args.mapping)
    enrollment = _load_json(args.enrollment) or None
    engine = create_async_engine(settings.database.POSTGRES_URL)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            if args.dry_run:
                result = await preflight(
                    session,
                    provider_to_issuer=provider_to_issuer,
                    enrollment_evidence=enrollment,
                    default_email=settings.oidc.DEFAULT_USER_EMAIL or _DEFAULT_USER_EMAIL,
                )
                report = result.report.to_sanitized_dict() if result.report else {}
                print(json.dumps({
                    "mode": "dry-run",
                    "digest": result.digest,
                    "schema_revision": result.schema_revision,
                    "rows": len(result.rows),
                    "report": _redact_summary(report),
                    "before_user_count": len((result.before_snapshot or {}).get("user_ids", [])),
                }, indent=2, sort_keys=True))
                return 0
            if args.apply:
                if not args.confirm_operator:
                    raise MigrationAuthorizationError(
                        "refusing apply without --confirm-operator"
                    )
                if not args.preflight_digest:
                    print("apply requires --preflight-digest from a reviewed dry-run",
                          file=sys.stderr)
                    return 2
                async with maker() as fresh:
                    inspected = await preflight(
                        fresh,
                        provider_to_issuer=provider_to_issuer,
                        enrollment_evidence=enrollment,
                        default_email=settings.oidc.DEFAULT_USER_EMAIL or _DEFAULT_USER_EMAIL,
                    )
                if inspected.digest != args.preflight_digest:
                    raise StalePreflightError(
                        "mapping, enrollment, or database state changed since the "
                        "reviewed dry-run; re-run --dry-run and review the new "
                        "report instead of applying stale assumptions"
                    )
                async with maker() as apply_session:
                    fresh_preflight = await preflight(
                        apply_session,
                        provider_to_issuer=provider_to_issuer,
                        enrollment_evidence=enrollment,
                        default_email=settings.oidc.DEFAULT_USER_EMAIL or _DEFAULT_USER_EMAIL,
                    )
                    outcome = await apply_migration(
                        apply_session,
                        fresh_preflight,
                        operator_authorized=True,
                        provider_to_issuer=provider_to_issuer,
                        enrollment_evidence=enrollment,
                    )
                print(json.dumps({"mode": "apply", **_redact_summary(outcome.to_sanitized_dict())},
                                 indent=2, sort_keys=True))
                return 0 if not outcome.blocked else 3
    except MigrationAuthorizationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (StalePreflightError, ConcurrentApplyError) as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        return 4
    finally:
        await engine.dispose()
    print("specify exactly one of --dry-run or --apply", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", default=None, help="Reviewed provider->issuer JSON file")
    parser.add_argument("--enrollment", default=None, help="Verified enrollment evidence JSON file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preflight-digest", default=None)
    parser.add_argument("--confirm-operator", action="store_true",
                        help="Explicit operator authorization for apply")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.dry_run) == bool(args.apply):
        print("specify exactly one of --dry-run or --apply", file=sys.stderr)
        return 2
    _ = Base  # ensure model metadata is registered for table creation checks
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
