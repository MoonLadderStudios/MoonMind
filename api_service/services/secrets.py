import structlog
from typing import Any, Sequence
from datetime import datetime, timezone

from sqlalchemy import select, Row
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManagedSecret, SecretStatus

logger = structlog.get_logger(__name__)


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
        cls, db: AsyncSession, slug: str, status: SecretStatus
    ) -> ManagedSecret | None:
        """Change the status of an existing secret."""
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()

        if not secret:
            logger.warning("secret_not_found_for_status_change", slug=slug)
            return None

        secret.status = status
        secret.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(secret)
        logger.info("secret_status_changed", slug=slug, new_status=status.value)
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
            with db.no_autoflush:
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
