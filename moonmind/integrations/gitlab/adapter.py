"""Small GitLab merge-request adapter: bounded reads plus one note write.

MoonLadderStudios/MoonMind#2616: read one merge request with its
discussions/status, then support one explicitly authorized write (an ordinary
note) through the existing connection/tool boundary. Endpoints follow the
supported server's documented API (``/projects/:id/merge_requests/:iid``,
``.../discussions``, ``.../approvals``, ``.../pipelines``, ``.../notes``).

Scope is deliberately narrow:

* Reads are bounded paginated fetches with a completeness report. GitLab
  populates diff/mergeability fields asynchronously, so unknown mergeability
  is reported as unknown — never as an empty blocker set. Notes, resolvable
  discussions, approvals, and pipeline results stay distinct.
* The only implemented write is an ordinary note. Its intent persists in a
  per-adapter operation ledger keyed by caller-supplied ``operation_id``
  (the existing operation-identity pattern: the same ``operation_id`` is
  never posted twice). A lost acknowledgment triggers a bounded note lookup
  for the operation footer before any repeat mutation. Exhausted or ambiguous
  observations retain the candidate plus a concrete authorized automation
  handoff — never an automatic human-review task, never an exactly-once
  promise.
* Inline review and merge are additional operations, not prerequisites.
  They raise ``GitLabCapabilityUnavailable`` until diff-identity /
  source-head-SHA plus target/check/approval safeguards are enforced, and
  their absence never blocks read/note work.
"""

from __future__ import annotations

import hashlib
from typing import Any

from moonmind.integrations.gitlab.client import GitLabClient
from moonmind.integrations.gitlab.errors import (
    GitLabCapabilityUnavailable,
    GitLabToolError,
)
from moonmind.integrations.gitlab.identity import GitLabMRRef

_NOTE_FOOTER_TEMPLATE = "<!-- moonmind:gitlab-note operation:{operation_id} -->"

#: The only writes this slice implements. Inline review, merge, and resolver
#: support are additional operations with their own safeguards.
SUPPORTED_WRITES = ("note",)

_MERGEABLE_OK = {"mergeable"}
_MERGEABLE_UNKNOWN = {None, "", "unchecked", "checking"}
_LEGACY_MERGEABLE_OK = {"can_be_merged"}
_LEGACY_MERGEABLE_UNKNOWN = {None, "", "unchecked"}

_MAX_LEDGER_ENTRIES = 256


def note_footer_for(operation_id: str) -> str:
    """Idempotency footer embedding existing operation identity in the note."""

    return _NOTE_FOOTER_TEMPLATE.format(operation_id=str(operation_id).strip())


def _mergeability_from_mr(mr: dict[str, Any]) -> dict[str, Any]:
    """Split mergeability into known blockers vs unknown (async) state."""

    detailed = mr.get("detailed_merge_status")
    legacy = mr.get("merge_status")
    blockers: list[dict[str, Any]] = []
    blocking_unknown: list[dict[str, Any]] = []
    if mr.get("has_conflicts") is True:
        blockers.append({"code": "conflict", "source": "has_conflicts"})
    if detailed in _MERGEABLE_UNKNOWN and legacy in _LEGACY_MERGEABLE_UNKNOWN:
        blocking_unknown.append(
            {"code": "mergeability_pending", "source": "detailed_merge_status"}
        )
    elif detailed in _MERGEABLE_OK or legacy in _LEGACY_MERGEABLE_OK:
        pass
    elif detailed is not None or legacy is not None:
        blockers.append(
            {
                "code": str(detailed or legacy),
                "source": "detailed_merge_status",
            }
        )
    else:
        blocking_unknown.append(
            {"code": "mergeability_pending", "source": "detailed_merge_status"}
        )
    return {
        "mergeable_known": not blocking_unknown,
        "blockers": blockers,
        "blocking_unknown": blocking_unknown,
    }


class GitLabMRAdapter:
    """Application-to-adapter boundary for one GitLab merge request."""

    def __init__(
        self,
        *,
        client: GitLabClient,
        identity: GitLabMRRef,
        max_discussion_pages: int = 10,
        discussion_per_page: int = 100,
    ) -> None:
        self._client = client
        self._identity = identity
        self._max_pages = max(1, int(max_discussion_pages))
        self._per_page = max(1, min(100, int(discussion_per_page)))
        self._note_ledger: dict[str, dict[str, Any]] = {}

    @property
    def identity(self) -> GitLabMRRef:
        return self._identity

    def advertised_capabilities(self) -> tuple[str, ...]:
        """Only implemented capabilities; inline/merge/resolver stay unadvertised."""

        return ("read_mr", "read_discussions", "read_status", "post_note")

    def _base_path(self) -> str:
        return (
            f"/projects/{self._identity.api_project_path}"
            f"/merge_requests/{self._identity.mr_iid}"
        )

    # -- reads ----------------------------------------------------------

    async def read_mr(self) -> dict[str, Any]:
        """Read the single merge request (fail-closed on transport errors)."""

        mr = await self._client.request_json(
            method="GET", path=self._base_path(), action="read_mr"
        )
        if not isinstance(mr, dict):
            raise GitLabToolError(
                "GitLab MR response has an unexpected shape.",
                code="gitlab_request_failed",
                status_code=502,
                action="read_mr",
            )
        return {
            "id": mr.get("id"),
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "state": mr.get("state"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "head_sha": mr.get("sha"),
            "web_url": mr.get("web_url"),
            "raw": mr,
        }

    async def read_discussions(self) -> dict[str, Any]:
        """Bounded paginated discussion read with a completeness report."""

        discussions: list[Any] = []
        page: str | None = "1"
        pages_fetched = 0
        total_pages: int | None = None
        while page is not None and pages_fetched < self._max_pages:
            response = await self._client._request_raw(
                method="GET",
                path=f"{self._base_path()}/discussions",
                action="read_discussions",
                params={"page": page, "per_page": str(self._per_page)},
            )
            try:
                batch = response.json()
            except Exception as exc:
                raise GitLabToolError(
                    "GitLab response could not be decoded.",
                    code="gitlab_request_failed",
                    status_code=502,
                    action="read_discussions",
                ) from exc
            if isinstance(batch, list):
                discussions.extend(batch)
            pages_fetched += 1
            next_page = (response.headers.get("X-Next-Page") or "").strip()
            total_raw = (response.headers.get("X-Total-Pages") or "").strip()
            total_pages = int(total_raw) if total_raw.isdigit() else total_pages
            page = next_page or None
        complete = page is None
        return {
            "discussions": discussions,
            "completeness": {
                "complete": complete,
                "pages_fetched": pages_fetched,
                "total_pages": total_pages,
                "next_page": page,
            },
        }

    async def read_status(self) -> dict[str, Any]:
        """Read approvals and pipelines distinctly from notes/discussions."""

        mr = await self._client.request_json(
            method="GET", path=self._base_path(), action="read_mr"
        )
        approvals = await self._client.request_json(
            method="GET", path=f"{self._base_path()}/approvals", action="read_approvals"
        )
        pipelines = await self._client.request_json(
            method="GET", path=f"{self._base_path()}/pipelines", action="read_pipelines"
        )
        if not isinstance(mr, dict):
            raise GitLabToolError(
                "GitLab MR response has an unexpected shape.",
                code="gitlab_request_failed",
                status_code=502,
                action="read_mr",
            )
        mergeability = _mergeability_from_mr(mr)
        return {
            "state": mr.get("state"),
            "head_sha": mr.get("sha"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "approvals": approvals,
            "pipelines": pipelines if isinstance(pipelines, list) else [],
            **mergeability,
        }

    # -- one authorized write -------------------------------------------

    def mark_note_uncertain(self, *, operation_id: str, body: str) -> None:
        """Record a possibly-issued note whose acknowledgment was lost."""

        key = str(operation_id).strip()
        if not key:
            raise GitLabToolError(
                "operation_id is required for note intent.",
                code="gitlab_invalid_request",
                status_code=400,
                action="post_note",
            )
        self._remember(key, {"state": "uncertain", "body": str(body or "")})

    async def post_note(self, *, body: str, operation_id: str) -> dict[str, Any]:
        """Post one ordinary note with safe retry on the operation ledger."""

        key = str(operation_id).strip()
        if not key:
            raise GitLabToolError(
                "operation_id is required for note intent.",
                code="gitlab_invalid_request",
                status_code=400,
                action="post_note",
            )
        clean_body = str(body or "").strip()
        if not clean_body:
            raise GitLabToolError(
                "note body is required.",
                code="gitlab_invalid_request",
                status_code=400,
                action="post_note",
            )
        existing = self._note_ledger.get(key)
        if existing is not None and existing.get("state") == "completed":
            return {
                "note_id": existing.get("note_id"),
                "reconciled": bool(existing.get("reconciled", False)),
                "duplicate": True,
            }
        footer = note_footer_for(key)
        full_body = clean_body if footer in clean_body else f"{clean_body}\n\n{footer}"
        if existing is None:
            # Fresh adapter (or first use of this operation_id): the
            # in-memory ledger cannot prove the note was never posted
            # (worker restart / Activity retry / adapter recreation all
            # clear it). Reconcile the footer before the first mutation so
            # a retried operation_id never posts twice.
            lookup = await self._lookup_note_by_footer(footer)
            if lookup.get("found"):
                self._remember(
                    key,
                    {
                        "state": "completed",
                        "note_id": lookup.get("note_id"),
                        "reconciled": True,
                    },
                )
                return {"note_id": lookup.get("note_id"), "reconciled": True}
            if lookup.get("exhausted"):
                self._remember(key, {"state": "exhausted", "body": clean_body})
                return {
                    "note_id": None,
                    "reconciled": False,
                    "automation_handoff": self._automation_handoff(
                        operation_id=key, body=clean_body, reason="note_lookup_exhausted"
                    ),
                }
        if existing is not None and existing.get("state") == "uncertain":
            lookup = await self._lookup_note_by_footer(footer)
            if lookup.get("found"):
                self._remember(
                    key,
                    {
                        "state": "completed",
                        "note_id": lookup.get("note_id"),
                        "reconciled": True,
                    },
                )
                return {"note_id": lookup.get("note_id"), "reconciled": True}
            if lookup.get("exhausted"):
                self._remember(key, {"state": "exhausted", "body": clean_body})
                return {
                    "note_id": None,
                    "reconciled": False,
                    "automation_handoff": self._automation_handoff(
                        operation_id=key, body=clean_body, reason="note_lookup_exhausted"
                    ),
                }
        try:
            created = await self._client.request_json(
                method="POST",
                path=f"{self._base_path()}/notes",
                action="post_note",
                json_body={"body": full_body},
            )
        except GitLabToolError:
            # The mutation may have landed despite the failed acknowledgment:
            # retain uncertainty so the next call looks up before re-posting.
            self._remember(key, {"state": "uncertain", "body": clean_body})
            raise
        note_id = created.get("id") if isinstance(created, dict) else None
        self._remember(
            key, {"state": "completed", "note_id": note_id, "reconciled": False}
        )
        return {"note_id": note_id, "reconciled": False}

    async def _lookup_note_by_footer(self, footer: str) -> dict[str, Any]:
        """Bounded lookup for a possibly-issued note before repeat mutation."""

        try:
            notes = await self._client.request_json(
                method="GET",
                path=f"{self._base_path()}/notes",
                action="lookup_note",
                params={"order_by": "created_at", "sort": "desc", "per_page": "20"},
            )
        except GitLabToolError:
            return {"found": False, "exhausted": True}
        if not isinstance(notes, list):
            return {"found": False, "exhausted": True}
        matches = [
            note
            for note in notes
            if isinstance(note, dict) and footer in str(note.get("body") or "")
        ]
        if len(matches) == 1:
            return {"found": True, "note_id": matches[0].get("id")}
        if len(matches) == 0:
            # Successful lookup with no footer present: safe for the caller
            # to proceed to exactly one mutation.
            return {"found": False, "exhausted": False}
        return {"found": False, "exhausted": True}

    def _automation_handoff(
        self, *, operation_id: str, body: str, reason: str
    ) -> dict[str, Any]:
        """Concrete authorized automation handoff retaining the candidate."""

        project = self._identity.project
        return {
            "operation_id": operation_id,
            "endpoint": project.endpoint,
            "project": project.project_path or str(project.project_id),
            "mr_iid": self._identity.mr_iid,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body_chars": len(body),
            "reason": reason,
            "retry": "authorized automation may retry post_note with the same operation_id",
        }

    def _remember(self, key: str, entry: dict[str, Any]) -> None:
        self._note_ledger[key] = entry
        while len(self._note_ledger) > _MAX_LEDGER_ENTRIES:
            oldest = next(iter(self._note_ledger))
            self._note_ledger.pop(oldest, None)

    # -- gated additional operations ------------------------------------

    async def post_inline_comment(self, *, body: str, operation_id: str) -> dict[str, Any]:
        """Inline review is an additional operation: unavailable in this slice."""

        raise GitLabCapabilityUnavailable(
            "Inline review requires enforced diff identity and position "
            "safeguards; it is unavailable until those land. Read and note "
            "work are unaffected.",
            action="post_inline_comment",
        )

    async def merge(self, *, operation_id: str) -> dict[str, Any]:
        """Merge is an additional operation: unavailable in this slice."""

        raise GitLabCapabilityUnavailable(
            "Merge requires expected source-head SHA plus actual target, "
            "check, and approval policy; it is unavailable until those "
            "safeguards land. Read and note work are unaffected.",
            action="merge",
        )
