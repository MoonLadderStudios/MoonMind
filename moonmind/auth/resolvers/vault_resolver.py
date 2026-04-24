import structlog

from moonmind.auth.resolvers.base import SecretBackendResolver
from moonmind.auth.secret_refs import ParsedSecretRef, VaultSecretResolver

logger = structlog.get_logger(__name__)

class AdapterVaultSecretResolver(SecretBackendResolver):
    """
    Adapter that wraps the legacy `VaultSecretResolver` to adhere
    to the universal `SecretBackendResolver` protocol.
    """

    def __init__(self, vault_resolver: VaultSecretResolver) -> None:
        self._vault_resolver = vault_resolver

    async def resolve(self, ref: ParsedSecretRef) -> str:
        # VaultSecretResolver expects the raw `vault://...` normalized reference string
        return await self._vault_resolver.resolve_plain_kv_value(ref.normalized_ref)
