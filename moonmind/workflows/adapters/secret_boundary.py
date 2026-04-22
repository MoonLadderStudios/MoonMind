"""Secret resolution boundary.
Defines the strictly isolated boundary where `ManagedSecret` values are decrypted
into memory just-in-time for process environment shaping, preventing leaked credentials.
"""

from abc import ABC, abstractmethod
from typing import Dict

class SecretResolverBoundary(ABC):
    @abstractmethod
    async def resolve_secrets(self, secret_refs: Dict[str, str]) -> Dict[str, str]:
        """Convert a dictionary of ENV_KEY -> db_secret_id into ENV_KEY -> Plaintext."""
        pass

class NullSecretResolver(SecretResolverBoundary):
    async def resolve_secrets(self, secret_refs: Dict[str, str]) -> Dict[str, str]:
        return {}

class DatabaseSecretResolver(SecretResolverBoundary):
    def __init__(self, db_session) -> None:
        self.db = db_session

    async def resolve_secrets(self, secret_refs: Dict[str, str]) -> Dict[str, str]:
        if not secret_refs:
            return {}

        import uuid
        from sqlalchemy.future import select
        from api_service.db.models import ManagedSecret, SecretStatus
        from moonmind.auth.secret_refs import SecretBackend, SecretReferenceError, parse_secret_ref

        resolved = {}
        for env_key, ref_id_str in secret_refs.items():
            if not ref_id_str:
                continue

            secret = None
            try:
                parsed_ref = parse_secret_ref(ref_id_str)
            except SecretReferenceError:
                parsed_ref = None

            if parsed_ref and parsed_ref.backend == SecretBackend.DB_ENCRYPTED:
                result = await self.db.execute(
                    select(ManagedSecret).where(
                        ManagedSecret.slug == parsed_ref.locator,
                        ManagedSecret.status == SecretStatus.ACTIVE,
                    )
                )
                secret = result.scalars().first()
            elif parsed_ref is not None:
                continue
            else:
                # Legacy payloads stored raw UUIDs before the general secret-ref
                # format was introduced. Keep resolving those while new profiles
                # use explicit db:// slug references.
                try:
                    ref_id = uuid.UUID(ref_id_str)
                except ValueError:
                    continue

                result = await self.db.execute(
                    select(ManagedSecret).where(
                        ManagedSecret.id == ref_id,
                        ManagedSecret.status == SecretStatus.ACTIVE
                    )
                )
                secret = result.scalars().first()

            if secret and secret.ciphertext:
                resolved[env_key] = secret.ciphertext

        return resolved
