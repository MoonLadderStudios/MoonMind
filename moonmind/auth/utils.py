import base64
import hashlib


class RedactedSecret(str):
    def __repr__(self) -> str:  # pragma: no cover - simple repr
        h = hashlib.sha256(self.encode()).digest()
        return f"<redacted-sha256:{base64.b32encode(h)[:6].decode().lower()}>"


def manifest_key_to_profile_field(key: str) -> str:
    return f"{key.lower()}_encrypted"
