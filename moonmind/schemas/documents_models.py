from typing import List, Optional
from pydantic import BaseModel

class ConfluenceLoadRequest(BaseModel):
    space_key: str
    page_ids: Optional[List[str]] = None
    max_num_results: Optional[int] = 100

class GitHubLoadRequest(BaseModel):
    repo: str
    branch: Optional[str] = "main"
    filter_extensions: Optional[List[str]] = None
    github_token: Optional[str] = None

class GoogleDriveLoadRequest(BaseModel):
    folder_id: Optional[str] = None
    file_ids: Optional[List[str]] = None
    # Note: LlamaIndex GoogleDriveReader's behavior with folders is often recursive by default.
    # The 'recursive' field's direct applicability in the reader itself needs to be confirmed during Indexer development.
    # For now, including it in the request model for API consistency.
    recursive: Optional[bool] = False 
    service_account_key_path: Optional[str] = None # Path to Google service account JSON. If None, ADC is assumed by the indexer.
