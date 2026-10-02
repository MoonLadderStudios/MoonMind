"""Shared publication failure semantics."""


def is_unpublished_submodule_failure(detail: str) -> bool:
    """Recognize Git's dependency check without treating it as transport failure."""
    normalized = " ".join(str(detail).casefold().split())
    return "submodule" in normalized and "not be found on any remote" in normalized
