from pydantic import BaseModel
from typing import Dict, Any, Optional

class BootPayload(BaseModel):
    page: str
    apiBase: str = "/api"
    features: Optional[Dict[str, bool]] = None
    initialData: Optional[Any] = None

def generate_boot_payload(
    page: str,
    api_base: str = "/api",
    features: Optional[Dict[str, bool]] = None,
    initial_data: Optional[Any] = None,
) -> str:
    """Generates the JSON payload for the UI boot script."""
    payload = BootPayload(
        page=page,
        apiBase=api_base,
        features=features,
        initialData=initial_data
    )
    return payload.model_dump_json(exclude_none=True)
