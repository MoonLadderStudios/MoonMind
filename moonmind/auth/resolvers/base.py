import structlog
from typing import Mapping

from moonmind.auth.secret_refs import (
    ParsedSecretRef,
    SecretBackend,
    SecretUnsupportedBackendError,
)

logger = structlog.get_logger(__name__)


class SecretBackendResolver:
    """Protocol for specialized secret backend resolvers."""

    async def resolve(self, ref: ParsedSecretRef) -> str:
        """
        Extract the plaintext value for the corresponding reference locator.

        Args:
            ref: A parsed secret reference meant for this backend.

        Raises:
            SecretMissingError: If the entity doesn't exist.
            SecretAccessDeniedError: If permission is lacking.
            SecretDecryptionError: If the payload cannot be decrypted.
        """
        raise NotImplementedError


class RootSecretResolver:
    """Aggregates multiple backend resolvers and delegates resolution."""

    def __init__(self, resolvers: Mapping[SecretBackend, SecretBackendResolver]) -> None:
        self._resolvers = dict(resolvers)

    def register(self, backend: SecretBackend, resolver: SecretBackendResolver) -> None:
        """Register a backend resolver dynamically."""
        self._resolvers[backend] = resolver

    async def resolve(self, ref: ParsedSecretRef) -> str:
        """
        Resolve a secret reference by delegating to the appropriate backend.
        
        Args:
            ref: The parsed secret reference structure.

        Raises:
            SecretUnsupportedBackendError: If the backend has no configured resolver.
            SecretMissingError: If the entity doesn't exist.
            SecretAccessDeniedError: If permission is lacking.
            SecretDecryptionError: If the payload cannot be decrypted.
        """
        resolver = self._resolvers.get(ref.backend)
        if not resolver:
            raise SecretUnsupportedBackendError(
                f"No resolver configured for backend type: {ref.backend.value}"
            )

        logger.info("resolving_secret", backend=ref.backend.value, locator=ref.locator)
        # Delegate resolution
        # Structured tracing around failure metrics would happen inherently via raises.
        # Plaintext is intentionally never logged.
        plaintext = await resolver.resolve(ref)
        logger.info("resolved_secret_success", backend=ref.backend.value)
        return plaintext
