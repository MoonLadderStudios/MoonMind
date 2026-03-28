"""Secret resolution boundary.
Defines the strictly isolated boundary where `ManagedSecret` values are decrypted
into memory just-in-time for process environment shaping, preventing leaked credentials.
"""

from abc import ABC, abstractmethod
from typing import Dict

class SecretResolverBoundary(ABC):
    @abstractmethod
    def resolve_secrets(self, secret_refs: Dict[str, str]) -> Dict[str, str]:
        """Convert a dictionary of ENV_KEY -> db_secret_id into ENV_KEY -> Plaintext."""
        pass

class NullSecretResolver(SecretResolverBoundary):
    def resolve_secrets(self, secret_refs: Dict[str, str]) -> Dict[str, str]:
        return {}
