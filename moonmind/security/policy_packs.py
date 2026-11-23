"""Deterministic checks around a pack drafted from an operator's own policy.

The ``security-policy-pack`` Skill classifies policy statements. This module
never classifies or drafts: it checks the Skill's response against the cited
documents, builds the restricted draft and its review sheet, compares revisions
and applies the operator's review. Document text is data; nothing in it can set
pack status, objectives or mappings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from moonmind.security.framework_packs import (
    ArtifactRef,
    FrameworkMapping,
    MappingEdition,
    MappingTarget,
    PackObjective,
    PackRequirement,
    PackSource,
    Sha256,
    VersionedFrameworkPack,
)
from moonmind.security.frameworks import (
    MAX_PACK_REQUIREMENTS,
    PACK_IDENTIFIER_PATTERN,
    AssessmentText,
    FrameworkContract,
    PackIdentifier,
    Text,
)

DRAFT_RESPONSE_SCHEMA = "policy-pack-draft-response/v1"
DRAFT_SCHEMA = "policy-pack-draft/v1"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 16
SUPPORTED_MEDIA_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt")
NORMATIVE = re.compile(r"\b(shall|must|required|requires)\b", re.IGNORECASE)
# Text addressed to a model or workflow rather than to the organisation. A match
# is flagged for review; it never changes what the deterministic checks accept.
INSTRUCTION = re.compile(
    r"\b(ignore|disregard|override|forget)\b[^.\n]{0,40}\b(instructions?|prompts?|rules)\b"
    r"|\b(system|developer)\s+prompt\b"
    r"|\byou\s+are\s+(now\s+)?(an?\s+|the\s+)?(ai|assistant|model|agent|language model)\b"
    r"|\bas\s+an?\s+(ai|assistant|language model)\b"
    r"|\b(ai|assistant|agent|model|llm)s?\s*[:,]\s*(please\s+)?"
    r"(ignore|mark|set|treat|report|skip|approve|output|run|execute)\b"
    r"|\bmark\b[^.\n]{0,60}\bas\s+(compliant|satisfied|passed|met)\b"
    r"|\b(objectivesComplete|publicationStatus)\b",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_CLAUSE_HEADING = re.compile(
    r"^\s*(?P<clause>\d+(?:\.\d+)+)\.?\s+(?P<rest>[^.]{1,120})$"
)
_CLAUSE = re.compile(r"^\s*(?:section\s+|clause\s+)?(?P<clause>\d+(?:\.\d+)*)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
EvidenceMethod = Literal["deterministic_check", "enquiry", "evidence", "observation"]
Relation = Literal["equivalent", "partial", "subset", "superset", "related"]
UNSTATED_APPLICABILITY = (
    "Not stated in the policy; determine it for the assessed scope."
)
DRAFT_OBJECTIVE_BASIS = (
    "Drafted from the operator's policy. No tool rule is adopted before review; "
    "this requirement needs organisational evidence or agent review."
)
# Share of a normative statement that validated quotes at its location must
# cover before it counts as classified; a bare "must" never suppresses one.
STATEMENT_COVERAGE = 0.8
# Requirement fields that change how it is assessed rather than how it reads.
ASSESSMENT_FIELDS = frozenset(
    {
        "applicability",
        "evidence_prompt",
        "required_evidence_methods",
        "evidence_types",
        "responsibilities",
        "objectives",
        "objectives_complete",
        "procedures",
    }
)


def normalize(text: str) -> str:
    """Compare quotes without whitespace, emphasis or case differences."""
    return " ".join(re.sub(r"[*_`]", "", text).split()).casefold()


def excerpt_digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


@dataclass(frozen=True)
class Section:
    title: str
    clause: str | None
    page: int
    text: str  # normalized


@dataclass(frozen=True)
class PolicyDocument:
    artifact_ref: str
    sha256: str
    name: str
    sections: tuple[Section, ...]
    statements: tuple[tuple[Section, str], ...]  # normative sentences, original text

    def locate(self, locator: Locator) -> list[Section]:
        if locator.section is None:
            return [s for s in self.sections if s.page == locator.page]
        wanted = normalize(locator.section)
        clause = _CLAUSE.match(locator.section)
        return [
            s
            for s in self.sections
            if (locator.page is None or s.page == locator.page)
            and (
                wanted in {normalize(s.title), normalize(_CLAUSE.sub("", s.title))}
                or (clause and s.clause == clause["clause"])
            )
        ]


class UnusableDocument(ValueError):
    """A named document that cannot be read as policy text."""

    def __init__(self, status: Literal["unsupported", "unreadable"], reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def read_policy_document(
    *, artifact_ref: str, sha256: str, name: str, media_type: str | None, data: bytes
) -> PolicyDocument:
    """Split a text or Markdown policy into locatable sections and pages."""
    media = (media_type or "").split(";")[0].strip().casefold()
    if media not in SUPPORTED_MEDIA_TYPES and not name.casefold().endswith(
        SUPPORTED_SUFFIXES
    ):
        raise UnusableDocument(
            "unsupported",
            f"{media or 'unknown'} is not supported; supply the policy as text or Markdown.",
        )
    if len(data) > MAX_DOCUMENT_BYTES:
        raise UnusableDocument("unreadable", "The document exceeds the 2 MiB bound.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnusableDocument(
            "unreadable", "The document is not UTF-8 text."
        ) from None
    if not text.strip():
        raise UnusableDocument("unreadable", "The document is empty.")
    sections: list[Section] = []
    statements: list[tuple[Section, str]] = []
    title, clause, lines = "Document", None, []

    def close(page):
        body = "\n".join(lines)
        if not body.strip():
            return
        section = Section(title, clause, page, normalize(body))
        sections.append(section)
        for sentence in _SENTENCE.split(body):
            sentence = sentence.strip(" -*\t")
            heading = _HEADING.match(sentence) or _CLAUSE_HEADING.match(sentence)
            if NORMATIVE.search(sentence) and not heading:
                statements.append((section, sentence))

    for page, page_text in enumerate(text.split("\f"), start=1):
        for line in page_text.splitlines():
            heading = _HEADING.match(line) or _CLAUSE_HEADING.match(line)
            if heading:
                close(page)
                title = (
                    heading["title"]
                    if "title" in heading.groupdict()
                    else f"{heading['clause']} {heading['rest']}"
                ).strip()
                found = _CLAUSE.match(title)
                clause, lines = (found["clause"] if found else None), [line]
            else:
                lines.append(line)
        close(page)
        lines = []
    return PolicyDocument(
        artifact_ref, sha256, name, tuple(sections), tuple(statements)
    )


class Locator(FrameworkContract):
    # A Skill sees attachments by content, so it may cite a document by its
    # SHA-256; the deterministic step resolves it to the operator's artifact.
    artifact_ref: ArtifactRef | None = None
    document_sha256: Sha256 | None = None
    section: AssessmentText | None = None
    page: int | None = Field(default=None, ge=1, le=100000)
    quote: Text

    @model_validator(mode="after")
    def addressed(self):
        if self.artifact_ref is None and self.document_sha256 is None:
            raise ValueError(
                "locator requires a document artifactRef or documentSha256"
            )
        if self.section is None and self.page is None:
            raise ValueError("locator requires a section, clause or page")
        return self

    def label(self) -> str:
        parts = [self.section] if self.section else []
        if self.page is not None:
            parts.append(f"page {self.page}")
        return ", ".join(parts)


class ObjectiveSuggestion(FrameworkContract):
    source: PackIdentifier
    tool: PackIdentifier
    rule_ids: Annotated[list[PackIdentifier], Field(min_length=1, max_length=32)]
    rationale: AssessmentText


class MappingProposal(FrameworkContract):
    target_requirement_ids: Annotated[
        list[PackIdentifier], Field(min_length=1, max_length=64)
    ]
    relation: Relation
    rationale: Text


class DraftedRequirement(FrameworkContract):
    # Identity and locator are checked row by row so one bad row is reported,
    # not silently dropped with the rest of the response.
    id: Text
    locator: Locator | None = None
    title: AssessmentText
    summary: Text
    applicability: AssessmentText | None = None
    evidence_prompt: AssessmentText
    evidence_methods: Annotated[list[EvidenceMethod], Field(max_length=4)] = Field(
        default_factory=list
    )
    objective_suggestions: Annotated[list[ObjectiveSuggestion], Field(max_length=8)] = (
        Field(default_factory=list)
    )
    mapping_proposals: Annotated[list[MappingProposal], Field(max_length=8)] = Field(
        default_factory=list
    )


class DraftedNote(FrameworkContract):
    locator: Locator | None = None
    text: Text


class NamedDocument(FrameworkContract):
    name: AssessmentText
    reason: AssessmentText


class DraftResponse(FrameworkContract):
    """The Skill's response. Unknown fields, such as a status, are refused."""

    schema_version: Literal["policy-pack-draft-response/v1"]
    requirements: Annotated[
        list[DraftedRequirement], Field(max_length=MAX_PACK_REQUIREMENTS)
    ]
    context: Annotated[list[DraftedNote], Field(max_length=512)] = Field(
        default_factory=list
    )
    open_questions: Annotated[list[DraftedNote], Field(max_length=512)] = Field(
        default_factory=list
    )
    unread_documents: Annotated[list[NamedDocument], Field(max_length=32)] = Field(
        default_factory=list
    )


class PolicyIdentity(FrameworkContract):
    name: AssessmentText
    owner: AssessmentText
    version: PackIdentifier | None = None
    approved_on: date | None = None
    pack_id: PackIdentifier | None = None

    @model_validator(mode="after")
    def dated(self):
        if self.version is None and self.approved_on is None:
            raise ValueError("policy identity requires a version or approval date")
        return self

    @property
    def edition(self) -> str:
        return self.version or self.approved_on.isoformat()

    @property
    def default_pack_id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.casefold()).strip("-")[:100]
        return self.pack_id or f"policy-{slug or 'document'}"


class DocumentRecord(FrameworkContract):
    artifact_ref: ArtifactRef
    sha256: Sha256 | None = None
    name: AssessmentText
    media_type: AssessmentText | None = None
    status: Literal["read", "unsupported", "unreadable"]
    reason: AssessmentText | None = None


class Flag(FrameworkContract):
    kind: AssessmentText
    subject: AssessmentText
    reason: AssessmentText


class Note(FrameworkContract):
    locator: Locator | None = None
    text: Text
    origin: Literal["draft", "validation"] = "draft"


class SuggestionRecord(ObjectiveSuggestion):
    id: PackIdentifier
    requirement_id: PackIdentifier


class MappingSelection(FrameworkContract):
    framework_id: PackIdentifier
    edition: MappingEdition


class ProposalRecord(FrameworkContract):
    id: PackIdentifier
    from_requirement_ids: Annotated[list[PackIdentifier], Field(min_length=1)]
    to_requirement_ids: Annotated[list[PackIdentifier], Field(min_length=1)]
    relation: Relation
    rationale: Text


class PackReference(FrameworkContract):
    artifact_ref: ArtifactRef
    pack_id: PackIdentifier
    edition: PackIdentifier
    revision: PackIdentifier
    sha256: Sha256


class RevisionChanges(FrameworkContract):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    reworded: list[str] = Field(default_factory=list)
    assessment_changed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    documents_added: list[str] = Field(default_factory=list)
    documents_removed: list[str] = Field(default_factory=list)


class PolicyPackDraft(FrameworkContract):
    """The draft pack plus everything the operator reviews before acceptance."""

    schema_version: Literal["policy-pack-draft/v1"] = DRAFT_SCHEMA
    policy: PolicyIdentity
    drafted_on: date
    pack: VersionedFrameworkPack
    documents: list[DocumentRecord]
    objective_suggestions: list[SuggestionRecord] = Field(default_factory=list)
    mapping_target: MappingSelection | None = None
    mapping_proposals: list[ProposalRecord] = Field(default_factory=list)
    context: list[Note] = Field(default_factory=list)
    open_questions: list[Note] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    previous: PackReference | None = None
    changes: RevisionChanges | None = None

    @model_validator(mode="after")
    def never_operative(self):
        if self.pack.publication_status != "draft":
            raise ValueError("a policy pack draft keeps draft status")
        if any(row.objectives_complete for row in self.pack.requirements):
            raise ValueError("a policy pack draft never completes objectives")
        return self


def next_revision(revision: str) -> str:
    if not revision.isdigit():
        raise ValueError(
            "supply the new revision explicitly; the previous one is not numeric"
        )
    return str(int(revision) + 1)


def _flag(flags, kind, subject, reason):
    flags.append(Flag(kind=kind, subject=subject[:1024], reason=reason))


def _covered(sentence: str, quotes) -> bool:
    """Whether validated quotes at the statement's location cover most of it."""
    text = normalize(sentence).rstrip(".;!? ")
    marked = [False] * len(text)
    for quote in quotes:
        quote = quote.rstrip(".;!? ")
        if quote and text in quote:
            return True
        start = text.find(quote) if quote else -1
        if start >= 0:
            marked[start : start + len(quote)] = [True] * len(quote)
    return sum(marked) >= STATEMENT_COVERAGE * len(text)


def _check_locator(locator, documents, *, normative):
    """Return the cited section, or the reason the locator does not hold."""
    document = documents.get(locator.artifact_ref) or next(
        (
            doc
            for doc in documents.values()
            if locator.artifact_ref is None and doc.sha256 == locator.document_sha256
        ),
        None,
    )
    if document is None:
        return (
            None,
            "unknown_document",
            "The locator names a document that was not read.",
        )
    sections = document.locate(locator)
    if not sections:
        return (
            None,
            "locator_not_found",
            "The cited section, clause or page is not in the document.",
        )
    quote = normalize(locator.quote)
    found = next((s for s in sections if quote in s.text), None)
    if found is None:
        return (
            None,
            "quote_not_found",
            "The quoted statement is not at the cited location.",
        )
    if INSTRUCTION.search(locator.quote):
        return (
            None,
            "instruction_text",
            "The cited text addresses a model or workflow; it is untrusted data, not a requirement.",
        )
    if normative and not NORMATIVE.search(locator.quote):
        return (
            None,
            "non_normative",
            "The statement has no shall, must or required; list it as context.",
        )
    return (found, document), None, None


def build_draft(
    response: DraftResponse,
    *,
    policy: PolicyIdentity,
    documents: list[PolicyDocument],
    records: list[DocumentRecord],
    drafted_on: date,
    installed_rules: set[tuple[str, str, str]] | None = None,
    mapping_target: MappingSelection | None = None,
    mapping_universe: set[str] | None = None,
    previous: tuple[PackReference, VersionedFrameworkPack] | None = None,
    revision: str | None = None,
) -> PolicyPackDraft:
    """Keep only rows that the cited documents support; flag every other row."""
    readable = {doc.artifact_ref: doc for doc in documents}
    if not readable:
        raise ValueError("no policy document could be read; see the named documents")
    flags: list[Flag] = []
    for record in records:
        if record.status != "read":
            _flag(
                flags,
                f"{record.status}_document",
                record.name,
                record.reason or record.status,
            )
    for item in response.unread_documents:
        _flag(flags, "skill_unread_document", item.name, item.reason)
    for doc in documents:
        for section in doc.sections:
            if INSTRUCTION.search(section.text):
                _flag(
                    flags,
                    "instruction_in_document",
                    f"{doc.name}: {section.title}",
                    "The document contains text addressed to a model or workflow. It was treated as data.",
                )

    prior_pack = previous[1] if previous else None
    prior_by_excerpt = {
        source.excerpt_sha256: row.id
        for row in (prior_pack.requirements if prior_pack else [])
        for source in row.sources
        if source.excerpt_sha256
    }
    claimed = {row.id for row in response.requirements}
    seen_ids, seen_quotes = set(), {}
    # Validated quotes by (document, section); only these classify statements.
    classified: dict[tuple[str, Section], list[str]] = {}
    requirements, suggestions, proposals = [], [], []
    for index, row in enumerate(response.requirements):
        subject = f"requirements[{index}] {row.id}"
        if row.locator is None:
            _flag(
                flags,
                "missing_locator",
                subject,
                "Every requirement must cite its source location.",
            )
            continue
        cited, kind, reason = _check_locator(row.locator, readable, normative=True)
        if cited is None:
            _flag(flags, kind, subject, reason)
            continue
        section, document = cited
        if INSTRUCTION.search(
            " ".join(
                [row.title, row.summary, row.evidence_prompt, row.applicability or ""]
            )
        ):
            _flag(
                flags,
                "instruction_text",
                subject,
                "The drafted text addresses a model or workflow; source text is data, not an instruction.",
            )
            continue
        digest = excerpt_digest(row.locator.quote)
        if digest in seen_quotes:
            _flag(
                flags,
                "duplicate_statement",
                subject,
                f"The statement is already requirement {seen_quotes[digest]}.",
            )
            continue
        requirement_id = row.id
        reused = prior_by_excerpt.get(digest)
        if reused and reused != requirement_id and reused not in claimed:
            # An unchanged statement keeps its earlier identity across revisions.
            requirement_id = reused
        if not re.fullmatch(PACK_IDENTIFIER_PATTERN, requirement_id):
            _flag(
                flags,
                "invalid_identifier",
                subject,
                "Requirement IDs use letters, digits and . _ : / -.",
            )
            continue
        if requirement_id in seen_ids:
            _flag(
                flags,
                "duplicate_identifier",
                subject,
                "Requirement IDs must be unique.",
            )
            continue
        seen_ids.add(requirement_id)
        seen_quotes[digest] = requirement_id
        classified.setdefault((document.artifact_ref, section), []).append(
            normalize(row.locator.quote)
        )
        requirements.append(
            PackRequirement(
                id=requirement_id,
                title=row.title,
                summary=row.summary,
                category=section.title[:1024],
                applicability=row.applicability or UNSTATED_APPLICABILITY,
                evidence_prompt=row.evidence_prompt,
                sources=[
                    PackSource(
                        artifact_ref=document.artifact_ref,
                        sha256=document.sha256,
                        excerpt_sha256=digest,
                        section=row.locator.label(),
                        reviewed_on=drafted_on,
                    )
                ],
                required_evidence_methods=list(dict.fromkeys(row.evidence_methods))
                or ["evidence"],
                objective_basis=DRAFT_OBJECTIVE_BASIS,
            )
        )
        for suggestion in row.objective_suggestions:
            name = f"{requirement_id} {suggestion.tool}"
            unknown = [
                rule
                for rule in suggestion.rule_ids
                if (suggestion.source, suggestion.tool, rule)
                not in (installed_rules or set())
            ]
            if installed_rules is None:
                _flag(
                    flags,
                    "unverifiable_rule",
                    name,
                    "No installed rule inventory was supplied; the suggestion was not kept.",
                )
            elif unknown:
                _flag(
                    flags,
                    "invented_rule",
                    name,
                    f"Not an installed rule: {', '.join(unknown)[:900]}.",
                )
            else:
                suggestions.append(
                    SuggestionRecord(
                        id=f"S{len(suggestions) + 1}",
                        requirement_id=requirement_id,
                        **suggestion.model_dump(),
                    )
                )
        for proposal in row.mapping_proposals:
            name = f"{requirement_id} mapping"
            if mapping_target is None:
                _flag(
                    flags,
                    "mapping_target_missing",
                    name,
                    "No catalog standard was selected; the mapping was not kept.",
                )
            elif set(proposal.target_requirement_ids) - (mapping_universe or set()):
                _flag(
                    flags,
                    "unknown_mapping_target",
                    name,
                    "The mapping names requirements outside the selected standard.",
                )
            else:
                proposals.append(
                    ProposalRecord(
                        id=f"M{len(proposals) + 1}",
                        from_requirement_ids=[requirement_id],
                        to_requirement_ids=proposal.target_requirement_ids,
                        relation=proposal.relation,
                        rationale=proposal.rationale,
                    )
                )
    if not requirements:
        raise ValueError("no drafted requirement passed deterministic validation")

    notes = {"context": [], "open_questions": []}
    for field in notes:
        for index, item in enumerate(getattr(response, field)):
            subject = f"{field}[{index}]"
            if item.locator is None:
                _flag(
                    flags,
                    "missing_locator",
                    subject,
                    "Context and open questions cite their source location.",
                )
                continue
            cited, kind, reason = _check_locator(
                item.locator, readable, normative=False
            )
            if cited is None:
                _flag(flags, kind, subject, reason)
                continue
            classified.setdefault((cited[1].artifact_ref, cited[0]), []).append(
                normalize(item.locator.quote)
            )
            if field == "context" and NORMATIVE.search(item.locator.quote):
                _flag(
                    flags,
                    "normative_context",
                    subject,
                    "A shall, must or required statement was listed as context; review it.",
                )
            located = item.locator.model_copy(
                update={"artifact_ref": cited[1].artifact_ref, "document_sha256": None}
            )
            notes[field].append(Note(locator=located, text=item.text))

    for doc in documents:
        for section, sentence in doc.statements:
            if INSTRUCTION.search(sentence) or _covered(
                sentence, classified.get((doc.artifact_ref, section), ())
            ):
                continue
            notes["open_questions"].append(
                Note(
                    locator=Locator(
                        artifact_ref=doc.artifact_ref,
                        section=section.title[:1024],
                        quote=sentence[:4096],
                    ),
                    text="The draft did not classify this normative statement. Decide whether it is a requirement.",
                    origin="validation",
                )
            )

    if prior_pack is not None:
        pack_id = prior_pack.id
        revision = revision or next_revision(prior_pack.revision)
        if revision == prior_pack.revision:
            raise ValueError("a revised draft needs a new revision")
    else:
        pack_id, revision = policy.default_pack_id, revision or "1"
    primary = documents[0]
    pack = VersionedFrameworkPack(
        id=pack_id,
        edition=policy.edition,
        revision=revision,
        title=policy.name,
        source=PackSource(
            artifact_ref=primary.artifact_ref,
            sha256=primary.sha256,
            section="Whole document",
            reviewed_on=drafted_on,
        ),
        publication_status="draft",
        provenance=f"Drafted on {drafted_on.isoformat()} from policy documents owned by {policy.owner}. Operator review is pending.",
        license="Operator-owned policy, confidential to the organisation. No redistribution right is granted.",
        distribution="restricted",
        requirements=requirements,
    )
    return PolicyPackDraft(
        policy=policy,
        drafted_on=drafted_on,
        pack=pack,
        documents=records,
        objective_suggestions=suggestions,
        mapping_target=mapping_target,
        mapping_proposals=proposals,
        context=notes["context"],
        open_questions=notes["open_questions"],
        flags=flags,
        previous=previous[0] if previous else None,
        changes=revision_changes(prior_pack, pack) if prior_pack else None,
    )


def _excerpts(row):
    return sorted(s.excerpt_sha256 or s.section for s in row.sources)


def revision_changes(
    previous: VersionedFrameworkPack, current: VersionedFrameworkPack
) -> RevisionChanges:
    """Requirement-level differences for review; neither pack is modified."""
    before = {row.id: row for row in previous.requirements}
    after = {row.id: row for row in current.requirements}
    changes = RevisionChanges(
        added=[key for key in after if key not in before],
        removed=[key for key in before if key not in after],
    )
    for key in after.keys() & before.keys():
        old, new = before[key], after[key]
        reworded = (old.title, old.summary, _excerpts(old)) != (
            new.title,
            new.summary,
            _excerpts(new),
        )
        # A redraft carries no adopted objectives; that is a change to review.
        assessed = old.model_dump(include=ASSESSMENT_FIELDS) != new.model_dump(
            include=ASSESSMENT_FIELDS
        )
        if reworded:
            changes.reworded.append(key)
        if assessed:
            changes.assessment_changed.append(key)
        if not (reworded or assessed):
            changes.unchanged.append(key)
    for keys in (changes.reworded, changes.assessment_changed, changes.unchanged):
        keys.sort()
    old_docs, new_docs = previous.source_documents(), current.source_documents()
    changes.documents_added = sorted(
        f"{ref} ({sha})" for ref, sha in new_docs.items() if old_docs.get(ref) != sha
    )
    changes.documents_removed = sorted(
        f"{ref} ({sha})" for ref, sha in old_docs.items() if new_docs.get(ref) != sha
    )
    return changes


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def render_review_sheet(draft: PolicyPackDraft) -> str:
    """A readable sheet; it is restricted like the policy it summarizes."""
    pack, policy = draft.pack, draft.policy
    lines = [
        f"# Policy pack review: {_cell(policy.name)}",
        "",
        f"- Pack: `{pack.id}` edition `{pack.edition}` revision `{pack.revision}`",
        f"- Owner: {_cell(policy.owner)}; drafted on {draft.drafted_on.isoformat()}",
        "- Status: **draft, restricted**. A draft is never operative. Accept it outside any model step to publish a revision.",
        "- Most policy statements need organisational evidence or agent review. Suggested tool rules are not adopted and never complete objectives.",
        "",
        "## Documents",
        "",
        "| Document | Artifact | SHA-256 | Status |",
        "| --- | --- | --- | --- |",
    ]
    for record in draft.documents:
        status = record.status + (f": {record.reason}" if record.reason else "")
        lines.append(
            f"| {_cell(record.name)} | `{record.artifact_ref}` | `{record.sha256 or '-'}` | {_cell(status)} |"
        )
    lines += [
        "",
        f"## Requirements ({len(pack.requirements)})",
        "",
        "| ID | Locator | Summary | Applicability | Evidence | Prompt |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in pack.requirements:
        source = row.sources[0]
        lines.append(
            f"| `{row.id}` | `{source.artifact_ref}` {_cell(source.section)} | {_cell(row.summary)} "
            f"| {_cell(row.applicability)} | {', '.join(row.required_evidence_methods)} | {_cell(row.evidence_prompt)} |"
        )
    if draft.objective_suggestions:
        lines += ["", "## Suggested tool rules (not adopted)", ""]
        lines += [
            f"- `{s.id}` for `{s.requirement_id}`: {s.source}/{s.tool} {', '.join(s.rule_ids)}. {_cell(s.rationale)}"
            for s in draft.objective_suggestions
        ]
    if draft.mapping_proposals:
        target = draft.mapping_target
        lines += [
            "",
            f"## Proposed mappings to {target.framework_id} {target.edition} (not asserted)",
            "",
        ]
        lines += [
            f"- `{m.id}` {', '.join(m.from_requirement_ids)} {m.relation} {', '.join(m.to_requirement_ids)}: {_cell(m.rationale)}"
            for m in draft.mapping_proposals
        ]
    for title, notes in (
        ("Context (not requirements)", draft.context),
        ("Open questions", draft.open_questions),
    ):
        if notes:
            lines += ["", f"## {title}", ""]
            lines += [
                f"- `{n.locator.artifact_ref}` {_cell(n.locator.label())}: {_cell(n.locator.quote)}. {_cell(n.text)}"
                for n in notes
            ]
    if draft.changes is not None:
        c = draft.changes
        lines += [
            "",
            f"## Changes from revision {draft.previous.revision}",
            "",
            f"- Added: {', '.join(c.added) or 'none'}",
            f"- Removed: {', '.join(c.removed) or 'none'}",
            f"- Reworded: {', '.join(c.reworded) or 'none'}",
            "- Assessment changed (applicability, evidence or objectives): "
            f"{', '.join(c.assessment_changed) or 'none'}",
            f"- Unchanged: {len(c.unchanged)}",
            f"- Source documents added or changed: {', '.join(c.documents_added) or 'none'}",
            f"- Source documents no longer cited: {', '.join(c.documents_removed) or 'none'}",
            "- The operative revision and the assessments that used it are unchanged.",
        ]
    if draft.flags:
        lines += ["", "## Validation flags", ""]
        lines += [
            f"- **{f.kind}** {_cell(f.subject)}: {_cell(f.reason)}" for f in draft.flags
        ]
    return "\n".join(lines) + "\n"


class RequirementEdit(FrameworkContract):
    title: AssessmentText | None = None
    summary: Text | None = None
    applicability: AssessmentText | None = None
    evidence_prompt: AssessmentText | None = None
    required_evidence_methods: (
        Annotated[list[EvidenceMethod], Field(min_length=1, max_length=4)] | None
    ) = None


class PackReview(FrameworkContract):
    reviewed_by: AssessmentText
    reviewed_on: date
    effective_from: date
    effective_until: date | None = None
    published_on: date | None = None
    revision: PackIdentifier | None = None
    distribution: Literal["restricted", "redistributable"] = "restricted"
    remove_requirement_ids: Annotated[list[PackIdentifier], Field(max_length=512)] = (
        Field(default_factory=list)
    )
    requirement_edits: Annotated[
        dict[PackIdentifier, RequirementEdit], Field(max_length=512)
    ] = Field(default_factory=dict)
    adopt_objective_suggestions: Annotated[
        list[PackIdentifier], Field(max_length=256)
    ] = Field(default_factory=list)
    accept_mapping_proposals: Annotated[list[PackIdentifier], Field(max_length=256)] = (
        Field(default_factory=list)
    )


def accept_pack(
    base: VersionedFrameworkPack, review: PackReview, *, draft: PolicyPackDraft | None
) -> VersionedFrameworkPack:
    """Apply an operator's review and return a new published revision."""
    if base.publication_status == "superseded":
        raise ValueError(
            "a superseded pack cannot be accepted; start from its successor"
        )
    if (base.publication_status == "draft") != (draft is not None):
        raise ValueError("accept a draft from its policy-pack-draft artifact")
    revision = review.revision or base.revision
    if base.publication_status == "published":
        revision = review.revision or next_revision(base.revision)
        if revision == base.revision:
            raise ValueError("editing a published pack creates a new revision")
    ids = {row.id for row in base.requirements}
    removed = set(review.remove_requirement_ids)
    if (removed | set(review.requirement_edits)) - ids:
        raise ValueError("review names an unknown requirement")
    suggestions = {s.id: s for s in draft.objective_suggestions} if draft else {}
    proposals = {m.id: m for m in draft.mapping_proposals} if draft else {}
    if set(review.adopt_objective_suggestions) - set(suggestions) or set(
        review.accept_mapping_proposals
    ) - set(proposals):
        raise ValueError("review names an unknown suggestion or mapping proposal")
    adopted: dict[str, list[PackObjective]] = {}
    for key in review.adopt_objective_suggestions:
        s = suggestions[key]
        if s.requirement_id in removed:
            raise ValueError("an adopted suggestion belongs to a removed requirement")
        adopted.setdefault(s.requirement_id, []).append(
            PackObjective(
                id=s.id,
                description=s.rationale,
                source=s.source,
                tool=s.tool,
                rule_ids=s.rule_ids,
            )
        )
    requirements = []
    for row in base.requirements:
        if row.id in removed:
            continue
        edit = review.requirement_edits.get(row.id)
        values = row.model_dump(by_alias=False)
        if edit is not None:
            values.update(edit.model_dump(exclude_none=True))
        objectives = [*row.objectives, *adopted.get(row.id, [])]
        values.update(
            objectives=objectives,
            # Suggested rules cover part of a policy statement, never all of it.
            objectives_complete=row.objectives_complete and not adopted.get(row.id),
            sources=[
                (
                    s.model_copy(update={"reviewed_on": review.reviewed_on})
                    if s.artifact_ref
                    else s
                )
                for s in row.sources
            ],
        )
        if adopted.get(row.id):
            values["objective_basis"] = (
                f"Operator adopted suggested tool rules on {review.reviewed_on.isoformat()}; "
                "they support part of the policy statement, so completeness is withheld."
            )
        requirements.append(values)
    if not requirements:
        raise ValueError("an accepted pack keeps at least one requirement")
    mappings, targets = list(base.mappings), list(base.mapping_targets)
    accepted = [proposals[key] for key in review.accept_mapping_proposals]
    if accepted:
        if any(set(m.from_requirement_ids) & removed for m in accepted):
            raise ValueError("an accepted mapping cites a removed requirement")
        selection = draft.mapping_target
        target_id = selection.framework_id
        targets = [t for t in targets if t.id != target_id]
        targets.append(
            MappingTarget(
                id=target_id,
                framework_id=selection.framework_id,
                edition=selection.edition,
                requirement_ids=sorted(
                    {i for m in accepted for i in m.to_requirement_ids}
                ),
            )
        )
        mappings += [
            FrameworkMapping(
                id=m.id,
                from_requirement_ids=m.from_requirement_ids,
                target_id=target_id,
                to_requirement_ids=m.to_requirement_ids,
                relation=m.relation,
                rationale=m.rationale,
                source=(
                    base.source.model_copy(update={"reviewed_on": review.reviewed_on})
                    if base.source.artifact_ref
                    else base.source
                ),
                basis="evaluated",
                reviewed_by=review.reviewed_by,
                reviewed_on=review.reviewed_on,
            )
            for m in accepted
        ]
    mappings = [m for m in mappings if not set(m.from_requirement_ids) & removed]
    source = base.source
    if source.artifact_ref:
        source = source.model_copy(update={"reviewed_on": review.reviewed_on})
    values = base.model_dump(by_alias=False)
    values.update(
        revision=revision,
        source=source,
        reviewed_by=review.reviewed_by,
        reviewed_on=review.reviewed_on,
        publication_status="published",
        published_on=review.published_on or review.reviewed_on,
        effective_from=review.effective_from,
        effective_until=review.effective_until,
        distribution=review.distribution,
        requirements=requirements,
        mapping_targets=targets,
        mappings=mappings,
    )
    return VersionedFrameworkPack.model_validate(values)
