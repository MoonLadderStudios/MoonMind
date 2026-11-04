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
* The only implemented write is an ordinary note carrying a footer with the
  caller-supplied ``operation_id``. The per-adapter ledger only short-cuts a
  confirmed note; any other state, including a fresh adapter after a worker
  restart, first pages through the MR's notes for that footer. Only a
  complete lookup without the footer authorizes one POST: an exhausted page
  budget, an ambiguous match, or a failed read stays uncertain and returns a
  concrete authorized automation handoff — never an automatic human-review
  task, never an exactly-once promise.
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
    GitLabIdentityError,
    GitLabTokenExpiredError,
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
        max_note_lookup_pages: int = 10,
    ) -> None:
        self._client = client
        self._identity = identity
        self._max_pages = max(1, int(max_discussion_pages))
        self._per_page = max(1, min(100, int(discussion_per_page)))
        self._max_note_pages = max(1, int(max_note_lookup_pages))
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
        self._check_mr_identity(mr)
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

    def _check_mr_identity(self, mr: Any) -> None:
        """Fail closed when the response is not the requested merge request."""

        if not isinstance(mr, dict):
            raise GitLabToolError(
                "GitLab MR response has an unexpected shape.",
                code="gitlab_request_failed",
                status_code=502,
                action="read_mr",
            )
        project_id = self._identity.project.project_id
        if mr.get("iid") != self._identity.mr_iid or (
            project_id is not None and mr.get("project_id") != project_id
        ):
            raise GitLabIdentityError(
                "GitLab returned a different merge request than requested.",
                action="read_mr",
            )

    async def _read_pages(
        self,
        path: str,
        *,
        action: str,
        max_pages: int,
        params: dict[str, str] | None = None,
        stop: Any = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Follow ``X-Next-Page`` within ``max_pages``; report what was read.

        ``stop(batch)`` ends the walk early once a page holds what the caller
        looks for. ``complete`` is true only when the server reported no
        further page; an unrecognized page ends the walk incomplete.
        """

        items: list[Any] = []
        page: str | None = "1"
        pages_fetched = 0
        total_pages: int | None = None
        while page is not None and pages_fetched < max_pages:
            response = await self._client._request_raw(
                method="GET",
                path=path,
                action=action,
                params={
                    **(params or {}),
                    "page": page,
                    "per_page": str(self._per_page),
                },
            )
            try:
                batch = response.json()
            except Exception as exc:
                raise GitLabToolError(
                    "GitLab response could not be decoded.",
                    code="gitlab_request_failed",
                    status_code=502,
                    action=action,
                ) from exc
            if not isinstance(batch, list):
                break
            items.extend(batch)
            pages_fetched += 1
            next_page = (response.headers.get("X-Next-Page") or "").strip()
            total_raw = (response.headers.get("X-Total-Pages") or "").strip()
            total_pages = int(total_raw) if total_raw.isdigit() else total_pages
            page = next_page or None
            if stop is not None and stop(batch):
                break
        return items, {
            "complete": page is None,
            "pages_fetched": pages_fetched,
            "total_pages": total_pages,
            "next_page": page,
        }

    async def read_discussions(self) -> dict[str, Any]:
        """Bounded paginated discussion read with a completeness report."""

        discussions, completeness = await self._read_pages(
            f"{self._base_path()}/discussions",
            action="read_discussions",
            max_pages=self._max_pages,
        )
        return {"discussions": discussions, "completeness": completeness}

    async def read_status(self) -> dict[str, Any]:
        """Read approvals and pipelines distinctly from notes/discussions.

        A pipeline page budget left unread or an unrecognized approvals shape
        makes the status incomplete rather than an apparently complete set.
        """

        mr = await self._client.request_json(
            method="GET", path=self._base_path(), action="read_mr"
        )
        self._check_mr_identity(mr)
        approvals = await self._client.request_json(
            method="GET", path=f"{self._base_path()}/approvals", action="read_approvals"
        )
        pipelines, pipeline_pages = await self._read_pages(
            f"{self._base_path()}/pipelines",
            action="read_pipelines",
            max_pages=self._max_pages,
        )
        approvals_known = isinstance(approvals, dict)
        mergeability = _mergeability_from_mr(mr)
        return {
            "state": mr.get("state"),
            "head_sha": mr.get("sha"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "approvals": approvals if approvals_known else None,
            "pipelines": pipelines,
            "completeness": {
                "complete": approvals_known and pipeline_pages["complete"],
                "approvals_known": approvals_known,
                "pipelines": pipeline_pages,
            },
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
        # Only a confirmed note skips reconciliation. A fresh adapter (worker
        # restart, Activity retry) cannot prove from memory that the note was
        # never posted, and an uncertain attempt may have landed.
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
        if lookup.get("uncertain"):
            self._remember(key, {"state": "uncertain", "body": clean_body})
            return {
                "note_id": None,
                "reconciled": False,
                "automation_handoff": self._automation_handoff(
                    operation_id=key,
                    body=clean_body,
                    reason=lookup["reason"],
                    pages_examined=lookup["pages_examined"],
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
        """Page through the MR's notes for the footer before any mutation.

        Absence is confirmed only when every page was read. A page budget left
        unread, more than one match, or a failed read is uncertain, because a
        partial lookup is no evidence that the earlier write did not happen.
        An expired credential or identity rejection is raised as itself.
        """

        def _matches(batch: list[Any]) -> list[Any]:
            return [
                note
                for note in batch
                if isinstance(note, dict) and footer in str(note.get("body") or "")
            ]

        try:
            notes, pages = await self._read_pages(
                f"{self._base_path()}/notes",
                action="lookup_note",
                max_pages=self._max_note_pages,
                params={"order_by": "created_at", "sort": "desc"},
                stop=lambda batch: bool(_matches(batch)),
            )
        except (GitLabTokenExpiredError, GitLabIdentityError):
            raise
        except GitLabToolError:
            return {
                "uncertain": True,
                "reason": "note_lookup_failed",
                "pages_examined": 0,
            }
        matches = _matches(notes)
        if len(matches) == 1:
            return {"found": True, "note_id": matches[0].get("id")}
        if matches:
            reason = "note_lookup_ambiguous"
        elif not pages["complete"]:
            reason = "note_lookup_exhausted"
        else:
            return {"found": False}
        return {
            "uncertain": True,
            "reason": reason,
            "pages_examined": pages["pages_fetched"],
        }

    def _automation_handoff(
        self, *, operation_id: str, body: str, reason: str, pages_examined: int
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
            "pages_examined": pages_examined,
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
