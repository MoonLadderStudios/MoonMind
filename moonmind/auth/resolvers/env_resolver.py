import os
import structlog

from moonmind.auth.resolvers.base import SecretBackendResolver
from moonmind.auth.secret_refs import ParsedSecretRef, SecretMissingError

logger = structlog.get_logger(__name__)

class EnvSecretResolver(SecretBackendResolver):
    """Resolves secrets dynamically from the process environment (`env://`)."""

    async def resolve(self, ref: ParsedSecretRef) -> str:
        locator = ref.locator
        value = os.environ.get(locator)
        
        if value is None:
            raise SecretMissingError(f"Environment variable not found: {locator}")
        
        if not str(value).strip():
            logger.warning("empty_environment_variable", locator=locator)
            raise SecretMissingError(f"Environment variable is empty: {locator}")
            
        return str(value)
