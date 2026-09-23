"""Repair generic OpenCode validation-failure markers without re-enrollment.

MoonLadderStudios/MoonMind#4526: ``setup_provider_api_key`` used to persist
every validation exception -- including infrastructure and discovery outages --
as a disabled ``validation_failed`` / ``auth_invalid`` record with the single
generic message ``Pinned OpenCode runtime validation failed.`` The code path
no longer writes that marker for non-credential failures, but rows already
written stay stranded: reconciliation never re-probes a disabled profile and
readiness keeps reporting the generic cause.

The historical API path validated the candidate key before
``_upsert_managed_secret`` ran, and it only persisted the generic marker when
the profile had no saved ``opencode_api_key`` role yet. The rows actually
stranded by that path therefore carry the generic marker *without* a saved
credential. Requiring a saved key would skip exactly the rows needing repair.

This revision performs one explicit, safe transition for eligible rows only:

* OpenCode profiles (``runtime_id == 'opencode'``) whose persisted state is
  exactly the automatic generic marker (``validation_failed`` /
  ``auth_invalid`` with the generic pinned-runtime failure reason) and with
  no explicit user/policy disable.

Rows with a saved credential (``secret_refs`` still holds the
``opencode_api_key`` role) return to ``connected`` + enabled with credential
generation and secrets untouched, their readiness restated as unknown-outcome
(``launch_ready`` false with an inconclusive-outcome reason), and any stale
re-validation exhaustion latch retired. The next ordinary reconciliation pass
re-probes them; a genuinely bad credential simply defers again without being
re-disabled by this migration.

Rows without a saved credential return to retryable enrollment pending
(``api_key_pending`` / ``missing_credentials``, disabled but re-enterable)
with the same inconclusive-outcome reason instead of the terminal generic
marker. No credential is fabricated; the operator re-enters the key through
the ordinary setup path.

Rows with explicit disables/revocations, genuinely rejected keys (specific
failure reasons), non-OpenCode runtimes, or newer generations are left
exactly as they are. Re-running the upgrade is harmless: transitioned rows
no longer match the eligibility predicate.

Revision ID: 389_opencode_validation_repair
Revises: 388_github_event_receipts_3967
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "389_opencode_validation_repair"
down_revision: Union[str, None] = "388_github_event_receipts_3967"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GENERIC_PINNED_FAILURE = "Pinned OpenCode runtime validation failed."
_INCONCLUSIVE_REASON = (
    "Previous validation outcome was inconclusive; re-validation scheduled."
)


def _has_saved_credential(row: object) -> bool:
    """Report whether a profile row still holds its saved OpenCode API key ref."""

    get = row.get if isinstance(row, dict) else getattr
    try:
        secret_refs = get("secret_refs")
    except Exception:
        return False
    if not isinstance(secret_refs, dict):
        return False
    return bool(str(secret_refs.get("opencode_api_key") or "").strip())


def _eligible_for_recovery(row: object) -> bool:
    """Report whether one profile row carries only the automatic generic marker.

    The historical enrollment path validated before persisting the secret, so
    genuinely stranded rows usually have *no* saved ``opencode_api_key`` role.
    Credential presence selects the recovery target, never eligibility.
    """

    get = row.get if isinstance(row, dict) else getattr
    try:
        runtime_id = get("runtime_id")
        auth_state = get("auth_state")
        disabled_reason = get("disabled_reason")
        behavior = get("command_behavior")
    except Exception:
        return False
    if str(runtime_id or "") != "opencode":
        return False
    if str(auth_state or "").lower() != "validation_failed":
        return False
    if str(disabled_reason or "").lower() != "auth_invalid":
        return False
    if not isinstance(behavior, dict):
        return False
    readiness = behavior.get("auth_readiness") or {}
    if not isinstance(readiness, dict):
        return False
    return readiness.get("failure_reason") == _GENERIC_PINNED_FAILURE


def _recovered_behavior(behavior: dict, *, has_credential: bool) -> dict:
    """Restate one eligible behavior payload as unknown-outcome recovery."""

    recovered = dict(behavior)
    readiness = dict(recovered.get("auth_readiness") or {})
    readiness["connected"] = bool(has_credential)
    readiness["backing_secret_exists"] = bool(has_credential)
    readiness["launch_ready"] = False
    readiness["failure_reason"] = _INCONCLUSIVE_REASON
    recovered["auth_readiness"] = readiness
    if has_credential:
        recovered["auth_state"] = "connected"
        recovered["auth_status_label"] = "Re-validation scheduled"
    else:
        # No key was ever persisted: return to retryable enrollment pending
        # without fabricating a credential. The operator re-enters the key
        # through the ordinary setup path.
        recovered["auth_state"] = "api_key_pending"
        recovered["auth_status_label"] = "Credential required — re-enter API key"
    recovered.pop("runtime_revalidation_failure", None)
    return recovered


def upgrade() -> None:
    profiles = sa.table(
        "managed_agent_provider_profiles",
        sa.column("profile_id", sa.String()),
        sa.column("runtime_id", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("auth_state", sa.String()),
        sa.column("disabled_reason", sa.String()),
        sa.column("secret_refs", sa.JSON()),
        sa.column("command_behavior", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(profiles)).mappings():
        record = dict(row)
        if not _eligible_for_recovery(record):
            continue
        has_credential = _has_saved_credential(record)
        if has_credential:
            values = {
                "enabled": True,
                "auth_state": "connected",
                "disabled_reason": None,
                "command_behavior": _recovered_behavior(
                    dict(record["command_behavior"] or {}),
                    has_credential=True,
                ),
            }
        else:
            values = {
                "enabled": False,
                "auth_state": "api_key_pending",
                "disabled_reason": "missing_credentials",
                "command_behavior": _recovered_behavior(
                    dict(record["command_behavior"] or {}),
                    has_credential=False,
                ),
            }
        connection.execute(
            profiles.update()
            .where(profiles.c.profile_id == record["profile_id"])
            .values(**values)
        )


def downgrade() -> None:
    # The forward repair only moves automatically stranded rows back into
    # ordinary recovery; it never deletes credentials, generations, or
    # history. Reversing it would re-strand those rows behind the generic
    # marker the current code no longer writes, so there is nothing safe to
    # undo. Re-running the upgrade after a downgrade is harmless.
    pass
