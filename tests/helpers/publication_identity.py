"""Stub the existing Temporal memo read at isolated runtime boundaries."""

from unittest.mock import AsyncMock


def recorded_publication_identity(monkeypatch, identity):
    from moonmind.omnigent import git_identity

    memo = (
        {
            git_identity.PUBLICATION_GIT_IDENTITY_MEMO_KEY: {
                "schemaVersion": "v1",
                "identity": identity,
            }
        }
        if identity is not None
        else {}
    )
    monkeypatch.setattr(
        git_identity,
        "_recorded_publication_memo",
        AsyncMock(return_value=memo),
        raising=False,
    )
