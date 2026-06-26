import structlog
from typing import Any, Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, Row
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedSecret,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)

logger = structlog.get_logger(__name__)

_DEFAULT_SETTINGS_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")

class SecretsService:
    """Service layer for managing securely encrypted secrets."""

    @classmethod
    async def create_secret(
        cls, db: AsyncSession, slug: str, plaintext: str, details: dict[str, Any] | None = None
    ) -> ManagedSecret:
        """Create a new managed secret."""
        if details is None:
            details = {}

        secret = ManagedSecret(
            slug=slug,
            ciphertext=plaintext,  # StringEncryptedType handles encryption
            status=SecretStatus.ACTIVE,
            details=details,
        )
        db.add(secret)
        await db.commit()
        await db.refresh(secret)
        logger.info("secret_created", slug=slug)
        return secret

    @classmethod
    async def update_secret(
        cls, db: AsyncSession, slug: str, plaintext: str
    ) -> ManagedSecret | None:
        """Update an existing secret's value."""
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()
        
        if not secret:
            logger.warning("secret_not_found_for_update", slug=slug)
            return None

        secret.ciphertext = plaintext
        secret.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(secret)
        logger.info("secret_updated", slug=slug)
        return secret

    @classmethod
    async def rotate_secret(
        cls, db: AsyncSession, slug: str, new_plaintext: str
    ) -> ManagedSecret | None:
        """Rotate a secret. Functionally similar to update, but logs intent explicitly."""
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()

        if not secret:
            logger.warning("secret_not_found_for_rotate", slug=slug)
            return None

        secret.ciphertext = new_plaintext
        secret.status = SecretStatus.ROTATED
        secret.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(secret)
        logger.info("secret_rotated", slug=slug)
        
        # After rotation, they typically want to set it back to ACTIVE when verified,
        # but returning it as ROTATED indicates it has been updated in this lifecycle.
        # Alternatively, we could just say it's ACTIVE with a rotated timestamp.
        # The phase plan says: "Define secret state transitions... rotated". 
        return secret

    @classmethod
    async def set_status(
        cls,
        db: AsyncSession,
        slug: str,
        status: SecretStatus,
        *,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ManagedSecret | None:
        """Change the status of an existing secret and record an audit event.

        The audit event captures the lifecycle transition without exposing
        plaintext or ciphertext; ``redacted=True`` enforces that contract at
        the storage layer.
        """
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()

        if not secret:
            logger.warning("secret_not_found_for_status_change", slug=slug)
            return None

        previous_status = (
            secret.status.value
            if isinstance(secret.status, SecretStatus)
            else str(secret.status)
        )
        new_status = status.value if isinstance(status, SecretStatus) else str(status)

        secret.status = status
        secret.updated_at = datetime.now(timezone.utc)
        db.add(
            SettingsAuditEvent(
                event_type="secrets.status.changed",
                key=f"secrets.{slug}",
                scope="system",
                workspace_id=workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID,
                user_id=_DEFAULT_SETTINGS_SUBJECT_ID,
                actor_user_id=actor_user_id,
                old_value_json={"status": previous_status},
                new_value_json={"status": new_status},
                redacted=True,
                reason=reason,
                request_id=request_id,
            )
        )
        await db.commit()
        await db.refresh(secret)
        logger.info("secret_status_changed", slug=slug, new_status=new_status)
        return secret

    @classmethod
    async def delete_secret(cls, db: AsyncSession, slug: str) -> bool:
        """Hard-delete a secret by slug."""
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()
        
        if not secret:
            logger.warning("secret_not_found_for_delete", slug=slug)
            return False
            
        await db.delete(secret)
        await db.commit()
        logger.info("secret_deleted", slug=slug)
        return True

    @classmethod
    async def list_metadata(cls, db: AsyncSession) -> Sequence[Row]:
        """List all secret metadata without leaking plaintext."""
        # Using deferred column loading or just avoiding `ciphertext` access
        # Since `ciphertext` is encrypted at rest, accessing it returns the decrypt,
        # so we MUST avoid reading it or just not returning it in a Pydantic model.
        # Returning SQLAlchemy model is safe as long as the caller doesn't read `ciphertext`.
        # Even safer:
        # We can yield dicts or specific metadata objects.
        result = await db.execute(
            select(
                ManagedSecret.id, 
                ManagedSecret.slug, 
                ManagedSecret.status, 
                ManagedSecret.details, 
                ManagedSecret.created_at, 
                ManagedSecret.updated_at
            )
        )
        return result.all()

    @classmethod
    async def list_secret_usage(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """List metadata-only consumers for a managed secret reference."""
        secret_ref = f"db://{slug}"
        resolved_workspace_id = workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID
        resolved_user_id = user_id or _DEFAULT_SETTINGS_SUBJECT_ID
        status_result = await db.execute(
            select(ManagedSecret.status).where(ManagedSecret.slug == slug)
        )
        status = status_result.scalar_one_or_none()
        if status is None:
            return {
                "secretRef": secret_ref,
                "usages": [],
                "diagnostics": [
                    {
                        "code": "secret_ref_unresolved",
                        "message": "Managed secret is missing.",
                        "severity": "error",
                    }
                ],
            }

        usage_result = await db.execute(
            select(
                SettingsOverride.key,
                SettingsOverride.scope,
                SettingsOverride.value_json,
            ).where(
                SettingsOverride.workspace_id == resolved_workspace_id,
                or_(
                    and_(
                        SettingsOverride.scope == "user",
                        SettingsOverride.user_id == resolved_user_id,
                    ),
                    and_(
                        SettingsOverride.scope != "user",
                        SettingsOverride.user_id == _DEFAULT_SETTINGS_SUBJECT_ID,
                    ),
                ),
            )
        )
        usages = []
        for key, scope, value_json in usage_result:
            if not cls._value_references_secret(value_json, secret_ref):
                continue
            scope = str(scope)
            scope_label = "Workspace" if scope == "workspace" else "User"
            usages.append(
                {
                    "consumerType": "setting_override",
                    "objectName": f"{scope_label} setting {key}",
                    "reference": secret_ref,
                    "scope": scope,
                    "settingKey": key,
                }
            )

        return {"secretRef": secret_ref, "usages": usages, "diagnostics": []}

    @staticmethod
    def _value_references_secret(value: Any, secret_ref: str) -> bool:
        if value == secret_ref:
            return True
        if isinstance(value, dict):
            return any(
                SecretsService._value_references_secret(item, secret_ref)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                SecretsService._value_references_secret(item, secret_ref)
                for item in value
            )
        return False

    @classmethod
    async def get_secret(cls, db: AsyncSession, slug: str) -> str | None:
        """Retrieve the plaintext value of an ACTIVE secret."""
        result = await db.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug == slug, 
                ManagedSecret.status == SecretStatus.ACTIVE
            )
        )
        secret = result.scalar_one_or_none()
        
        if not secret:
            logger.warning("active_secret_not_found", slug=slug)
            return None

        # ciphertext decrypted automatically by StringEncryptedType
        return secret.ciphertext

    @classmethod
    async def validate_secret_ref(cls, db: AsyncSession, slug: str) -> dict[str, Any]:
        """Return redacted metadata-only validation diagnostics for a managed secret."""
        checked_at = datetime.now(timezone.utc).isoformat()
        result = await db.execute(
            select(ManagedSecret.status).where(ManagedSecret.slug == slug)
        )
        status = result.scalar_one_or_none()

        if status is None:
            return {
                "valid": False,
                "status": "missing",
                "checkedAt": checked_at,
                "diagnostics": [
                    {
                        "code": "secret_ref_unresolved",
                        "message": "Managed secret is missing.",
                        "severity": "error",
                    }
                ],
            }

        secret_status = (
            status.value
            if isinstance(status, SecretStatus)
            else str(status)
        )
        if secret_status == SecretStatus.ACTIVE.value:
            return {
                "valid": True,
                "status": secret_status,
                "checkedAt": checked_at,
                "diagnostics": [
                    {
                        "code": "secret_ref_resolvable",
                        "message": "Managed secret is active.",
                        "severity": "info",
                    }
                ],
            }

        return {
            "valid": False,
            "status": secret_status,
            "checkedAt": checked_at,
            "diagnostics": [
                {
                    "code": "secret_ref_unresolved",
                    "message": f"Managed secret is {secret_status}.",
                    "severity": "error",
                }
            ],
        }

    @classmethod
    async def import_from_env(
        cls,
        db: AsyncSession,
        env_dict: dict[str, str],
        *,
        overwrite_active: bool = False,
    ) -> int:
        """
        Migrate legacy .env values.
        Upserts values to ManagedSecrets.
        By default, existing active secrets are skipped.
        """
        imported_count = 0
        for key, value in env_dict.items():
            with db.sync_session.no_autoflush:
                result = await db.execute(
                    select(ManagedSecret).where(ManagedSecret.slug == key)
                )
            existing = result.scalar_one_or_none()

            if existing:
                if existing.status == SecretStatus.ACTIVE and not overwrite_active:
                    continue  # Skip overriding already active managed secrets
                now = datetime.now(timezone.utc)
                existing.ciphertext = value
                existing.status = SecretStatus.ACTIVE
                details = dict(existing.details or {})
                details.update(
                    {"imported_from": ".env", "migrated_at": now.isoformat()}
                )
                existing.details = details
                existing.updated_at = now
                imported_count += 1
            else:
                now = datetime.now(timezone.utc)
                db.add(
                    ManagedSecret(
                        slug=key,
                        ciphertext=value,
                        status=SecretStatus.ACTIVE,
                        details={
                            "imported_from": ".env",
                            "migrated_at": now.isoformat(),
                        },
                    )
                )
                imported_count += 1

        if imported_count > 0:
            await db.commit()
            logger.info("secrets_imported_from_env", count=imported_count)
            
        return imported_count
