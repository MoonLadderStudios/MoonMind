from typing import Optional

from pydantic import BaseModel


class ConfluenceLoadRequest(BaseModel):
    space_key: str
    include_attachments: Optional[bool] = False
    limit: Optional[int] = 50