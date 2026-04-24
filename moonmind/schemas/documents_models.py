from typing import List, Optional

from pydantic import BaseModel, ValidationInfo, field_validator, model_validator

class ConfluenceLoadRequest(BaseModel):
    space_key: Optional[str] = None
    page_id: Optional[str] = None
    page_title: Optional[str] = None
    cql_query: Optional[str] = None
    max_pages_to_fetch: Optional[int] = (
        None  # Renamed for clarity, for space/CQL loading
    )

    @field_validator("page_title")
    @classmethod
    def page_title_requires_space_key(
        cls, v: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if v and not info.data.get("space_key"):
            raise ValueError("space_key is required if page_title is provided")
        return v

    @model_validator(mode="after")
    def check_exclusive_loading_method(self) -> "ConfluenceLoadRequest":
        identifiers_count = sum(
            [
                bool(self.page_id),
                bool(self.cql_query),
                bool(self.page_title and self.space_key),
                bool(self.space_key and not self.page_title and not self.page_id and not self.cql_query),
            ]
        )

        if identifiers_count == 0:
            raise ValueError(
                "A loading method must be specified: page_id, page_title (with space_key), cql_query, or space_key."
            )
        if identifiers_count > 1:
            raise ValueError(
                f"Only one loading method can be specified. Provided: {identifiers_count} methods. "
                "Check inputs for page_id, cql_query, page_title/space_key, or space_key alone."
            )

        return self

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
    service_account_key_path: Optional[str] = (
        None  # Path to Google service account JSON. If None, ADC is assumed by the indexer.
    )
