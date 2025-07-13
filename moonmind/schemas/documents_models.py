from typing import List, Optional

from pydantic import BaseModel, validator


class ConfluenceLoadRequest(BaseModel):
    space_key: Optional[str] = None
    page_id: Optional[str] = None
    page_title: Optional[str] = None
    cql_query: Optional[str] = None
    max_pages_to_fetch: Optional[int] = (
        None  # Renamed for clarity, for space/CQL loading
    )

    @validator("page_title", always=True)
    def page_title_requires_space_key(cls, v, values):
        if v and not values.get("space_key"):
            raise ValueError("space_key is required if page_title is provided")
        return v

    # Pydantic v1 style root_validator. For Pydantic v2, use @model_validator
    from pydantic import root_validator

    @root_validator(
        pre=False, skip_on_failure=True
    )  # post-validation, ensure other field validations passed
    def check_exclusive_loading_method(cls, values):
        page_id = values.get("page_id")
        cql_query = values.get("cql_query")
        page_title = values.get("page_title")
        space_key = values.get("space_key")

        identifiers_count = sum(
            [
                bool(page_id),
                bool(cql_query),
                bool(
                    page_title and space_key
                ),  # page_title is only an identifier if space_key is also present
                bool(
                    space_key and not page_title and not page_id and not cql_query
                ),  # space_key alone
            ]
        )

        if identifiers_count == 0:
            raise ValueError(
                "A loading method must be specified: page_id, page_title (with space_key), cql_query, or space_key."
            )
        if identifiers_count > 1:
            # Refine which specific combination caused the error if possible, or keep generic
            active_methods = []
            if page_id:
                active_methods.append("page_id")
            if cql_query:
                active_methods.append("cql_query")
            if page_title and space_key:
                active_methods.append("page_title_with_space_key")
            # This check for space_key alone might be redundant if one of the above is true,
            # but explicit for clarity in the sum. If it's the *only* one, then sum is 1.
            # The error is for sum > 1.
            if space_key and not page_title and not page_id and not cql_query:
                # This case means only space_key was provided, which is valid.
                # The error is about *multiple* identifiers.
                pass  # This specific combination, if it's the only one, is fine.

            # Re-evaluate the condition for "multiple identifiers" based on provided fields
            # The sum check is the primary guard.
            raise ValueError(
                f"Only one loading method can be specified. Provided: {identifiers_count} methods. Check inputs for page_id, cql_query, page_title/space_key, or space_key alone."
            )

        return values


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
