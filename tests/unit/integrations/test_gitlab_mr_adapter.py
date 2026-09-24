"""Hermetic application-to-adapter tests for the small GitLab MR slice.

MoonLadderStudios/MoonMind#2616: read one GitLab merge request plus
discussions/status, then support one explicitly authorized write (an ordinary
note) through the existing connection/tool boundary. All transport here is
hermetic (httpx.MockTransport); no live server, no external mutation.
"""

from __future__ import annotations

import httpx
import pytest

from moonmind.integrations.gitlab.adapter import GitLabMRAdapter
from moonmind.integrations.gitlab.client import (
    GitLabClient,
    ResolvedGitLabConnection,
    build_gitlab_connection,
)
from moonmind.integrations.gitlab.errors import (
    GitLabCapabilityUnavailable,
    GitLabIdentityError,
    GitLabToolError,
    GitLabTokenExpiredError,
)
from moonmind.integrations.gitlab.identity import (
    GitLabMRRef,
    resolve_gitlab_identity,
)

pytestmark = [pytest.mark.asyncio]

ADMITTED = ("https://gitlab.example.com",)
PROJECT = "group/subgroup/project"
MR_IID = 7


def _connection(handler, *, retry_attempts: int = 3) -> tuple[ResolvedGitLabConnection, httpx.AsyncClient]:
    connection = build_gitlab_connection(
        endpoint="https://gitlab.example.com",
        token="glpat-secret-token",
        admitted_endpoints=ADMITTED,
        retry_attempts=retry_attempts,
    )
    injected = httpx.AsyncClient(
        base_url=connection.base_url,
        headers=connection.headers,
        transport=httpx.MockTransport(handler),
    )
    return connection, injected


def _mr_payload(**overrides):
    payload = {
        "id": 42,
        "iid": MR_IID,
        "project_id": 99,
        "title": "Small slice",
        "state": "opened",
        "source_branch": "feature/slice",
        "target_branch": "main",
        "sha": "abc123",
        "merge_status": "can_be_merged",
        "detailed_merge_status": "mergeable",
        "has_conflicts": False,
        "web_url": "https://gitlab.example.com/group/subgroup/project/-/merge_requests/7",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# R1: connection identity
# ---------------------------------------------------------------------------


async def test_identity_resolves_subgroup_project_and_iid() -> None:
    ref = resolve_gitlab_identity(
        endpoint="https://gitlab.example.com/",
        project=PROJECT,
        mr_iid=MR_IID,
        admitted_endpoints=ADMITTED,
    )
    assert isinstance(ref, GitLabMRRef)
    assert ref.project.project_path == PROJECT
    assert ref.mr_iid == MR_IID
    assert ref.api_project_path == "group%2Fsubgroup%2Fproject"


async def test_identity_accepts_numeric_project_id() -> None:
    ref = resolve_gitlab_identity(
        endpoint="https://gitlab.example.com",
        project="99",
        mr_iid="7",
        admitted_endpoints=ADMITTED,
    )
    assert ref.project.project_id == 99
    assert ref.api_project_path == "99"


async def test_identity_preserves_fork_source_and_target() -> None:
    ref = resolve_gitlab_identity(
        endpoint="https://gitlab.example.com",
        project=PROJECT,
        mr_iid=MR_IID,
        admitted_endpoints=ADMITTED,
        source_project="fork-owner/project",
        target_project=PROJECT,
    )
    assert ref.source_project == "fork-owner/project"
    assert ref.target_project == PROJECT


async def test_identity_rejects_unapproved_instance() -> None:
    with pytest.raises(GitLabIdentityError):
        resolve_gitlab_identity(
            endpoint="https://gitlab.evil.example/",
            project=PROJECT,
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )


async def test_identity_rejects_arbitrary_internal_endpoint() -> None:
    with pytest.raises(GitLabIdentityError):
        resolve_gitlab_identity(
            endpoint="http://169.254.169.254/",
            project=PROJECT,
            mr_iid=MR_IID,
            admitted_endpoints=("http://169.254.169.254",),
        )


async def test_identity_rejects_embedded_credentials_and_bad_iid() -> None:
    with pytest.raises(GitLabIdentityError):
        resolve_gitlab_identity(
            endpoint="https://token@gitlab.example.com/",
            project=PROJECT,
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
    with pytest.raises(GitLabIdentityError):
        resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project=PROJECT,
            mr_iid=0,
            admitted_endpoints=ADMITTED,
        )


async def test_identity_models_provider_profile_vcs_separate_from_code_host() -> None:
    ref = resolve_gitlab_identity(
        endpoint="https://gitlab.example.com",
        project=PROJECT,
        mr_iid=MR_IID,
        admitted_endpoints=ADMITTED,
    )
    profile = ref.as_provider_profile()
    assert profile["codeHost"] == "gitlab"
    assert profile["vcs"] == "git"
    assert profile["codeHost"] != profile["vcs"]


async def test_client_never_forwards_credentials_across_instances() -> None:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/redirect-probe"):
            return httpx.Response(
                302, headers={"location": "https://gitlab.evil.example/api/v4/x"}
            )
        return httpx.Response(200, json={"ok": True})

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        with pytest.raises(GitLabIdentityError):
            await client.request_json(
                method="GET", path="/redirect-probe", action="redirect_probe"
            )
    finally:
        await injected.aclose()
    assert len(seen) == 1
    assert "gitlab.evil.example" not in str(seen[0].url)


# ---------------------------------------------------------------------------
# R2: paginated MR/discussion/status reads
# ---------------------------------------------------------------------------


async def test_read_mr_returns_parsed_fields() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/projects/99/merge_requests/7"
        return httpx.Response(200, json=_mr_payload())

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        read = await adapter.read_mr()
    finally:
        await injected.aclose()
    assert read["iid"] == MR_IID
    assert read["title"] == "Small slice"
    assert read["source_branch"] == "feature/slice"
    assert read["target_branch"] == "main"


async def test_read_discussions_paginates_with_completeness_report() -> None:
    pages = {
        "1": [{"id": "d1", "notes": [{"id": 1, "body": "first", "system": False}]}],
        "2": [{"id": "d2", "notes": [{"id": 2, "body": "second", "system": False}]}],
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        headers = {"X-Page": page, "X-Per-Page": "1", "X-Total-Pages": "2"}
        if page == "1":
            headers["X-Next-Page"] = "2"
        return httpx.Response(200, json=pages[page], headers=headers)

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        result = await adapter.read_discussions()
    finally:
        await injected.aclose()
    assert [d["id"] for d in result["discussions"]] == ["d1", "d2"]
    assert result["completeness"]["complete"] is True
    assert result["completeness"]["pages_fetched"] == 2


async def test_read_discussions_reports_incomplete_when_page_bound_hit() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        return httpx.Response(
            200,
            json=[{"id": f"d{page}", "notes": []}],
            headers={"X-Page": page, "X-Next-Page": "2", "X-Total-Pages": "9"},
        )

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref, max_discussion_pages=1)
        result = await adapter.read_discussions()
    finally:
        await injected.aclose()
    assert result["completeness"]["complete"] is False
    assert result["completeness"]["next_page"] == "2"


async def test_async_merge_fields_unknown_is_not_an_empty_blocker_set() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_mr_payload(detailed_merge_status=None, merge_status="unchecked"),
        )

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        status = await adapter.read_status()
    finally:
        await injected.aclose()
    assert status["mergeable_known"] is False
    assert status["blockers"] == []
    assert status["blocking_unknown"] != []


async def test_status_keeps_notes_discussions_approvals_pipelines_distinct() -> None:
    routes: dict[str, httpx.Response] = {
        "/api/v4/projects/99/merge_requests/7": httpx.Response(200, json=_mr_payload()),
        "/api/v4/projects/99/merge_requests/7/approvals": httpx.Response(
            200, json={"approved": False, "approvals_required": 1}
        ),
        "/api/v4/projects/99/merge_requests/7/pipelines": httpx.Response(
            200, json=[{"id": 5, "status": "success"}]
        ),
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        return routes[request.url.path]

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        status = await adapter.read_status()
    finally:
        await injected.aclose()
    assert status["approvals"]["approvals_required"] == 1
    assert status["pipelines"] == [{"id": 5, "status": "success"}]
    assert "discussions" not in status


# ---------------------------------------------------------------------------
# R3: one authorized note write with safe retry
# ---------------------------------------------------------------------------


async def test_post_note_persists_intent_and_dedupes_duplicate_delivery() -> None:
    posts: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(201, json={"id": 4242, "body": "hello"})
        return httpx.Response(200, json=[])

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        first = await adapter.post_note(body="hello", operation_id="op:1")
        second = await adapter.post_note(body="hello", operation_id="op:1")
    finally:
        await injected.aclose()
    assert first["note_id"] == 4242
    assert second["note_id"] == 4242
    assert len(posts) == 1


async def test_lost_acknowledgment_looks_up_before_repeat_mutation() -> None:
    calls: list[tuple[str, str]] = []
    note_body = "retry me\n\n<!-- moonmind:gitlab-note operation:op:lost -->"

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"id": 777, "body": note_body}], headers={}
            )
        return httpx.Response(201, json={"id": 999, "body": "unexpected repost"})

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        adapter.mark_note_uncertain(operation_id="op:lost", body="retry me")
        result = await adapter.post_note(body="retry me", operation_id="op:lost")
    finally:
        await injected.aclose()
    assert result["note_id"] == 777
    assert result["reconciled"] is True
    assert all(method == "GET" for method, _ in calls)


async def test_exhausted_lookup_retains_candidate_with_automation_handoff() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(201, json={"id": 1, "body": "x"})

    connection, injected = _connection(_handler, retry_attempts=1)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        adapter.mark_note_uncertain(operation_id="op:stuck", body="stuck body")
        result = await adapter.post_note(body="stuck body", operation_id="op:stuck")
    finally:
        await injected.aclose()
    assert result["reconciled"] is False
    handoff = result["automation_handoff"]
    assert handoff["operation_id"] == "op:stuck"
    assert handoff["mr_iid"] == MR_IID
    assert "human_review" not in str(handoff).lower()
    assert "exactly_once" not in str(handoff).lower()


async def test_token_expiry_maps_distinctly_from_forbidden() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401 Unauthorized"})

    connection, injected = _connection(_handler, retry_attempts=1)
    client = GitLabClient(connection=connection, client=injected)
    try:
        with pytest.raises(GitLabTokenExpiredError):
            await client.request_json(
                method="GET", path="/projects/99", action="expiry_probe"
            )
    finally:
        await injected.aclose()

    def _forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "403 Forbidden"})

    connection2, injected2 = _connection(_forbidden, retry_attempts=1)
    client2 = GitLabClient(connection=connection2, client=injected2)
    try:
        with pytest.raises(GitLabToolError) as excinfo:
            await client2.request_json(
                method="GET", path="/projects/99", action="forbidden_probe"
            )
    finally:
        await injected2.aclose()
    assert not isinstance(excinfo.value, GitLabTokenExpiredError)


# ---------------------------------------------------------------------------
# R4/R5/R7: gating and capability inventory
# ---------------------------------------------------------------------------


async def test_inline_and_merge_are_unavailable_without_blocking_reads_or_notes() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 11, "body": "note"})
        if request.url.path.endswith("/notes"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/approvals"):
            return httpx.Response(200, json={"approved": True})
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_mr_payload())

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        assert "post_inline_comment" not in adapter.advertised_capabilities()
        assert "merge" not in adapter.advertised_capabilities()
        assert "resolve" not in adapter.advertised_capabilities()
        with pytest.raises(GitLabCapabilityUnavailable):
            await adapter.post_inline_comment(body="x", operation_id="op:i")
        with pytest.raises(GitLabCapabilityUnavailable):
            await adapter.merge(operation_id="op:m")
        # Reads and the authorized note write still work.
        assert (await adapter.read_mr())["iid"] == MR_IID
        assert (await adapter.post_note(body="n", operation_id="op:n"))["note_id"] == 11
    finally:
        await injected.aclose()


async def test_github_only_resolver_rejects_gitlab_input_before_paid_execution() -> None:
    from pr_resolver_core.code_hosts import ensure_github_only_selector

    for gitlab_selector in (
        "https://gitlab.example.com/group/project/-/merge_requests/7",
        "gitlab:group/project!7",
        "https://gitlab.example.com/group/project!7",
    ):
        with pytest.raises(GitLabToolError):
            ensure_github_only_selector(gitlab_selector)
    # GitHub behavior is preserved.
    assert ensure_github_only_selector("123") == "123"
    assert (
        ensure_github_only_selector("https://github.com/o/r/pull/123")
        == "https://github.com/o/r/pull/123"
    )


# ---------------------------------------------------------------------------
# P1 review findings for #4548: bound credential, safe retry, fresh reconcile
# ---------------------------------------------------------------------------


def _bound_acquired(*, endpoint: str, operations: tuple[str, ...]):
    from types import SimpleNamespace

    class _Cred:
        def use_now(self, fn):
            return fn(b"glpat-bound-token")

    return SimpleNamespace(
        binding=SimpleNamespace(endpoint=endpoint, operations=operations),
        credential=_Cred(),
    )


async def test_bound_credential_rejects_endpoint_mismatch() -> None:
    from moonmind.integrations.gitlab.client import connection_from_bound_credential

    acquired = _bound_acquired(
        endpoint="https://gitlab.example.com", operations=("read", "write")
    )
    with pytest.raises(GitLabIdentityError):
        connection_from_bound_credential(
            acquired,
            endpoint="https://gitlab-other.example.com",
            admitted_endpoints=(
                "https://gitlab.example.com",
                "https://gitlab-other.example.com",
            ),
            action="read_mr",
        )
    # Matching endpoint still builds.
    connection = connection_from_bound_credential(
        acquired,
        endpoint="https://gitlab.example.com/",
        admitted_endpoints=ADMITTED,
        action="read_mr",
    )
    assert connection.base_url.startswith("https://gitlab.example.com")


async def test_bound_credential_enforces_write_permission() -> None:
    from moonmind.integrations.gitlab.client import connection_from_bound_credential

    read_only = _bound_acquired(
        endpoint="https://gitlab.example.com", operations=("read",)
    )
    with pytest.raises(GitLabIdentityError):
        connection_from_bound_credential(
            read_only,
            endpoint="https://gitlab.example.com",
            admitted_endpoints=ADMITTED,
            action="post_note",
        )
    # Read-only acquisition still authorizes reads.
    connection = connection_from_bound_credential(
        read_only,
        endpoint="https://gitlab.example.com",
        admitted_endpoints=ADMITTED,
        action="read_mr",
    )
    assert connection.base_url.startswith("https://gitlab.example.com")


async def test_mutation_transient_status_does_not_auto_repeat() -> None:
    posts: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(502, json={"message": "bad gateway"})
        return httpx.Response(200, json=[])

    connection, injected = _connection(_handler, retry_attempts=3)
    client = GitLabClient(connection=connection, client=injected)
    try:
        with pytest.raises(GitLabToolError):
            await client.request_json(
                method="POST",
                path="/projects/99/merge_requests/7/notes",
                action="post_note",
                json_body={"body": "x"},
            )
    finally:
        await injected.aclose()
    assert len(posts) == 1


async def test_safe_read_transient_status_still_retries() -> None:
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"message": "busy"})
        return httpx.Response(200, json={"ok": True})

    connection, injected = _connection(_handler, retry_attempts=3)
    client = GitLabClient(connection=connection, client=injected)
    try:
        result = await client.request_json(
            method="GET", path="/projects/99", action="read_mr"
        )
    finally:
        await injected.aclose()
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_fresh_adapter_reconciles_footer_before_first_post() -> None:
    calls: list[tuple[str, str]] = []
    footer_body = "hello\n\n<!-- moonmind:gitlab-note operation:op:fresh -->"

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"id": 4242, "body": footer_body}]
            )
        return httpx.Response(201, json={"id": 9999, "body": "duplicate"})

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        result = await adapter.post_note(body="hello", operation_id="op:fresh")
    finally:
        await injected.aclose()
    assert result["note_id"] == 4242
    assert result["reconciled"] is True
    assert all(method == "GET" for method, _ in calls)


async def test_fresh_adapter_posts_after_empty_footer_lookup() -> None:
    calls: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 31337, "body": "new"})

    connection, injected = _connection(_handler)
    client = GitLabClient(connection=connection, client=injected)
    try:
        ref = resolve_gitlab_identity(
            endpoint="https://gitlab.example.com",
            project="99",
            mr_iid=MR_IID,
            admitted_endpoints=ADMITTED,
        )
        adapter = GitLabMRAdapter(client=client, identity=ref)
        result = await adapter.post_note(body="new note", operation_id="op:new")
    finally:
        await injected.aclose()
    assert result["note_id"] == 31337
    assert result["reconciled"] is False
    assert ("GET", f"/api/v4/projects/99/merge_requests/{MR_IID}/notes") in calls
    assert ("POST", f"/api/v4/projects/99/merge_requests/{MR_IID}/notes") in calls
