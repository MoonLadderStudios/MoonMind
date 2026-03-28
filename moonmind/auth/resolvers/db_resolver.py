import structlog


from moonmind.auth.resolvers.base import SecretBackendResolver
from moonmind.auth.secret_refs import (
    ParsedSecretRef,
    SecretMissingError,
    SecretDecryptionError,
)
from api_service.db.base import async_session_maker
from api_service.services.secrets import SecretsService

logger = structlog.get_logger(__name__)


class DbEncryptedSecretResolver(SecretBackendResolver):
    """Resolves secrets dynamically from the database (`db://`)."""

    async def resolve(self, ref: ParsedSecretRef) -> str:
        slug = ref.locator
        
        async with async_session_maker() as session:
            try:
                plaintext = await SecretsService.get_secret(session, slug)
            except Exception as e:
                # Catch Fernet decryption exceptions thrown by StringEncryptedType
                logger.error("secret_decryption_failed", slug=slug, error=str(e))
                raise SecretDecryptionError(f"Failed to decrypt DB secret: {slug}") from e
                
            if plaintext is None:
                raise SecretMissingError(f"Database secret not found or not active: {slug}")

            return str(plaintext)
