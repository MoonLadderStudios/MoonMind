"""SQL-backed claim authority for isolated policy/tool tests.

Real HTTP and restart behavior is qualified in test_issue_claim_journey.
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture(autouse=True)
async def issue_claim_store(tmp_path, monkeypatch, request):
    from api_service.db.models import GitHubIssueClaim
    from moonmind.workflows.temporal import story_output_tools
    from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(GitHubIssueClaim.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        story_output_tools, "IssueClaimStore", lambda: IssueClaimStore(sessions)
    )
    original_owner = story_output_tools.claim_owner

    def owner(context=None):
        return original_owner(
            {"execution_owner": request.node.nodeid, **dict(context or {})}
        )

    monkeypatch.setattr(story_output_tools, "claim_owner", owner)
    yield
    await engine.dispose()


class ClaimCommentFixture:
    async def issue_claim_actor(self, **_kwargs):
        return {"ok": True, "actorId": "123"}

    async def list_issue_comments(self, **_kwargs):
        return {"ok": True, "comments": list(getattr(self, "claim_comments", []))}

    async def create_issue_comment(self, *, body, **_kwargs):
        if not hasattr(self, "claim_comments"):
            self.claim_comments = []
        comment = {
            "id": len(self.claim_comments) + 1,
            "body": body,
            "user": {"id": 123},
        }
        self.claim_comments.append(comment)
        return {"ok": True, "commentId": comment["id"], "comment": comment}
