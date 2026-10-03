"""Shared publication failure semantics."""


def is_unpublished_submodule_failure(detail: str) -> bool:
    """Recognize Git's dependency check without treating it as transport failure."""
    normalized = " ".join(str(detail).casefold().split())
    return "submodule" in normalized and any(
        marker in normalized
        for marker in ("not be found on any remote", "not be found on its declared remote")
    )
