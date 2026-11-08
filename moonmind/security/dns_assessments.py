"""Pure bounded DNS contracts and resolver-view observations, without transport.

Question authority is an exact name/type set, never the resolver's network
permission. No record, mail mechanism or alias creates authority. Retained
records are a bounded semantic projection, not encoded copies of peer bytes.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.opcode
import dns.rdataclass
import dns.rdatatype
from cryptography.exceptions import UnsupportedAlgorithm
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from moonmind.utils.logging import redact_evidence_text

# A single bounded invocation fits the native ContainerJob argv envelope. The
# count ceilings also limit semantic amplification of compressed DNS messages.
MAX_DNS_NAMES = 64
MAX_DNS_ATTEMPTS = 256
MAX_DNS_DEPTH = 8
MAX_DNS_RESPONSE_BYTES = 65536
MAX_DNS_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_DNS_PLAN_BYTES = 90 * 1024
MAX_DNS_RECORDS = 512
DNS_RUNNER_MODULE = "moonmind.security.dns_assessment_runner"
DNS_OUTPUT_DIRECTORY = "/outputs/dns-assessment"
DNS_ARGV_CHUNK_CHARS = 4096
# Reserve a value-free terminal observation, final timestamps and stop-state
# changes in both artifacts. Uncovered questions are already present in every
# checkpoint, so finalization never appends an unbounded diagnostic list.
DNS_TERMINAL_RESERVE_BYTES = 4096
DNS_OUTPUT_FILES = {"execution": "execution.json", "summary": "summary.json"}
RecordType = Literal[
    "A", "AAAA", "CNAME", "NS", "SOA", "CAA", "TXT", "MX", "DS", "DNSKEY"
]
Check = Literal["cname", "delegation", "caa", "dnssec", "spf", "dkim", "dmarc"]
SafeText = Annotated[str, Field(strict=True, max_length=4096)]
SmallInt = Annotated[int, Field(strict=True, ge=0, le=4294967295)]
INTERPRETATION = (
    "Query-only observations through the identified HTTPS resolver. DNSSEC AD is "
    "that resolver's assertion, not independent chain validation. Resolver-view "
    "NS/SOA observations do not verify authoritative parent-child delegation. "
    "Unresolved aliases are hypotheses, not proof of takeover. Mail observations "
    "do not send messages, discover selectors or contact report recipients."
)


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
        revalidate_instances="always",
    )

    @field_validator(
        "started_at",
        "checkpoint_at",
        "finished_at",
        "coverage_expires_at",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def explicit_timestamp(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("timestamps require timezone-aware datetime or ISO text")
        return value


def canonical_dns_name(value: str) -> str:
    """Canonical ASCII/A-label DNS identity; no escaped or inferred labels."""
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError("DNS names require bounded ASCII or A-labels")
    name = value.removesuffix(".").lower()
    if not 1 <= len(name) <= 253 or redact_evidence_text(name) != name:
        raise ValueError("DNS name is invalid or cannot be retained safely")
    for label in name.split("."):
        if not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label):
            raise ValueError("DNS labels are not canonical")
        if label.startswith("xn--"):
            try:
                if (
                    label.encode("ascii").decode("idna").encode("idna").decode("ascii")
                    != label
                ):
                    raise ValueError("noncanonical A-label")
            except UnicodeError as exc:
                raise ValueError("invalid A-label") from exc
    return name


class DNSQuestion(_Model):
    name: str = Field(strict=True, min_length=1, max_length=254)
    record_type: RecordType

    _canonical = field_validator("name")(canonical_dns_name)


class DNSCheckSelection(_Model):
    check: Check
    subject: str = Field(strict=True, max_length=254)
    selectors: tuple[str, ...] = Field((), max_length=16)
    # This is supplied coverage, not a PSL-derived organizational-domain claim.
    organizational_domain: str | None = Field(None, strict=True, max_length=254)

    _subject = field_validator("subject")(canonical_dns_name)

    @field_validator("organizational_domain")
    @classmethod
    def fallback_name(cls, value):
        return canonical_dns_name(value) if value is not None else None

    @field_validator("selectors")
    @classmethod
    def explicit_selectors(cls, values):
        names = tuple(canonical_dns_name(value) for value in values)
        if len(set(names)) != len(names):
            raise ValueError("duplicate DKIM selector")
        return tuple(sorted(names))

    @model_validator(mode="after")
    def selected_fields(self):
        if (self.check == "dkim") != bool(self.selectors):
            raise ValueError("only DKIM requires explicit selectors")
        if self.organizational_domain is not None and (
            self.check != "dmarc"
            or not (
                self.subject == self.organizational_domain
                or self.subject.endswith("." + self.organizational_domain)
            )
        ):
            raise ValueError("DMARC fallback must be a supplied ancestor")
        return self

    def required_questions(self) -> tuple[DNSQuestion, ...]:
        if self.check == "dkim":
            return tuple(
                DNSQuestion(name=f"{s}._domainkey.{self.subject}", record_type="TXT")
                for s in self.selectors
            )
        if self.check == "dmarc":
            names = [self.subject]
            if self.organizational_domain is not None:
                names.append(self.organizational_domain)
            return tuple(
                DNSQuestion(name=f"_dmarc.{name}", record_type="TXT")
                for name in dict.fromkeys(names)
            )
        types = {
            "spf": ("TXT",),
            "delegation": ("NS", "SOA"),
            "caa": ("CAA",),
            "cname": ("CNAME",),
        }.get(self.check, ())
        return tuple(DNSQuestion(name=self.subject, record_type=kind) for kind in types)


class DNSAssessment(_Model):
    questions: tuple[DNSQuestion, ...] = Field(
        min_length=1, max_length=MAX_DNS_ATTEMPTS
    )
    allowed_followups: tuple[DNSQuestion, ...] = Field((), max_length=MAX_DNS_ATTEMPTS)
    checks: tuple[DNSCheckSelection, ...] = Field((), max_length=64)
    max_followup_depth: int = Field(4, strict=True, ge=0, le=MAX_DNS_DEPTH)
    max_attempts: int = Field(64, strict=True, ge=1, le=MAX_DNS_ATTEMPTS)
    timeout_seconds: int = Field(120, strict=True, ge=1, le=600)
    query_timeout_seconds: int = Field(10, strict=True, ge=1, le=30)
    max_response_bytes: int = Field(
        MAX_DNS_RESPONSE_BYTES, strict=True, ge=12, le=MAX_DNS_RESPONSE_BYTES
    )
    max_output_bytes: int = Field(
        MAX_DNS_OUTPUT_BYTES, strict=True, ge=4096, le=MAX_DNS_OUTPUT_BYTES
    )
    queries_per_second: int = Field(5, strict=True, ge=1, le=20)

    @field_validator("questions", "allowed_followups")
    @classmethod
    def ordered_questions(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("duplicate DNS question")
        return tuple(sorted(values, key=lambda q: (q.name, q.record_type)))

    @model_validator(mode="after")
    def bounded_selection(self):
        authority = set(self.questions + self.allowed_followups)
        if set(self.questions) & set(self.allowed_followups):
            raise ValueError("initial and follow-up questions must be disjoint")
        if (
            len(authority) > MAX_DNS_ATTEMPTS
            or len({q.name for q in authority}) > MAX_DNS_NAMES
        ):
            raise ValueError("DNS question authority exceeds its count bound")
        if len(self.questions) > self.max_attempts:
            raise ValueError("initial questions exceed the original attempt budget")
        if self.query_timeout_seconds > self.timeout_seconds:
            raise ValueError("query timeout exceeds the original duration budget")
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("duplicate DNS check selection")
        for check in self.checks:
            if not set(check.required_questions()) <= authority:
                raise ValueError("selected check requires exact admitted questions")
            if check.check == "dnssec" and not any(
                q.name == check.subject for q in authority
            ):
                raise ValueError(
                    "DNSSEC observation requires an admitted subject question"
                )
        if len(self.model_dump_json(by_alias=True).encode()) > MAX_DNS_PLAN_BYTES:
            raise ValueError("DNS options exceed the native argv bound")
        return self


def require_dns_subset(
    selected: DNSAssessment, authority: DNSAssessment | None
) -> None:
    """Preserve exact query phases, selected checks and the original ceilings."""
    if authority is None or (
        not set(selected.questions) <= set(authority.questions)
        or not set(selected.allowed_followups)
        <= set(authority.questions + authority.allowed_followups)
        or not set(selected.checks) <= set(authority.checks)
        or any(
            getattr(selected, name) > getattr(authority, name)
            for name in (
                "max_followup_depth",
                "max_attempts",
                "timeout_seconds",
                "query_timeout_seconds",
                "max_response_bytes",
                "max_output_bytes",
                "queries_per_second",
            )
        )
    ):
        raise ValueError("DNS selection exceeds exact admitted question authority")


class DNSResolverConfig(_Model):
    """Deployment-owned endpoint, not authority granted by assessment options."""

    endpoint: str = Field(strict=True, min_length=1, max_length=2048)

    @field_validator("endpoint")
    @classmethod
    def https_endpoint(cls, value):
        if (
            not value.isascii()
            or any(c.isspace() or ord(c) < 32 for c in value)
            or any(c in value for c in "\\%?#")
        ):
            raise ValueError("resolver endpoint is not an unambiguous HTTPS URL")
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("resolver requires credential-free HTTPS")
        host = canonical_dns_name(parts.hostname)
        if parts.netloc.endswith(":") or (
            parts.port is not None and not 1 <= parts.port <= 65535
        ):
            raise ValueError("resolver port is invalid")
        if any(part in {".", ".."} for part in parts.path.split("/")):
            raise ValueError("resolver path must be canonical")
        netloc = host + (f":{parts.port}" if parts.port and parts.port != 443 else "")
        result = urlunsplit(("https", netloc, parts.path or "/", "", ""))
        if redact_evidence_text(result) != result:
            raise ValueError("resolver endpoint cannot be retained safely")
        return result


class DNSQueryPlan(_Model):
    schema_version: Literal["dns-query-plan/v1"] = "dns-query-plan/v1"
    assessment: DNSAssessment
    resolver: DNSResolverConfig
    plan_key: str = Field(pattern=r"^[0-9a-f]{64}$")


def _key(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def build_dns_query_plan(
    assessment: DNSAssessment, resolver: DNSResolverConfig
) -> DNSQueryPlan:
    assessment = DNSAssessment.model_validate(assessment)
    resolver = DNSResolverConfig.model_validate(resolver)
    body = {
        "assessment": assessment.model_dump(mode="json", by_alias=True),
        "resolver": resolver.model_dump(mode="json", by_alias=True),
    }
    plan = DNSQueryPlan(assessment=assessment, resolver=resolver, plan_key=_key(body))
    if len(plan.model_dump_json(by_alias=True).encode()) > MAX_DNS_PLAN_BYTES:
        raise ValueError("DNS plan exceeds the native argv bound")
    return plan


def encode_dns_query_plan(plan: DNSQueryPlan) -> tuple[str, ...]:
    if build_dns_query_plan(plan.assessment, plan.resolver) != plan:
        raise ValueError("DNS plan differs from its canonical identity")
    body = plan.model_dump_json(by_alias=True)
    chunks = tuple(
        body[index : index + DNS_ARGV_CHUNK_CHARS]
        for index in range(0, len(body), DNS_ARGV_CHUNK_CHARS)
    )
    if len(chunks) + 3 > 128:
        raise ValueError("DNS plan exceeds the native argument count")
    return chunks


def decode_dns_query_plan(chunks: tuple[str, ...] | list[str]) -> DNSQueryPlan:
    if (
        not isinstance(chunks, (tuple, list))
        or not 1 <= len(chunks) <= 125
        or any(
            type(chunk) is not str or not 1 <= len(chunk) <= DNS_ARGV_CHUNK_CHARS
            for chunk in chunks
        )
    ):
        raise ValueError("DNS plan requires bounded canonical JSON chunks")
    body = "".join(chunks)
    if len(body.encode()) > MAX_DNS_PLAN_BYTES:
        raise ValueError("DNS plan exceeds the native argv bound")
    plan = DNSQueryPlan.model_validate_json(body)
    if tuple(chunks) != encode_dns_query_plan(plan):
        raise ValueError("DNS plan chunks are not canonical")
    return plan


def build_dns_command(plan: DNSQueryPlan) -> list[str]:
    return ["-I", "-m", DNS_RUNNER_MODULE, *encode_dns_query_plan(plan)]


def permitted_followup(
    plan: DNSQueryPlan, question: DNSQuestion, *, depth: int
) -> bool:
    return (
        type(depth) is int
        and 1 <= depth <= plan.assessment.max_followup_depth
        and question in (plan.assessment.questions + plan.assessment.allowed_followups)
    )


def build_dns_query_wire(question: DNSQuestion) -> bytes:
    question = DNSQuestion.model_validate(question)
    query = dns.message.make_query(
        question.name + ".",
        question.record_type,
        rdclass=dns.rdataclass.IN,
        use_edns=0,
        want_dnssec=True,
    )
    query.id = 0
    return query.to_wire()


class DNSPublicKeyEvidence(_Model):
    status: Literal["observed", "revoked", "malformed", "unsupported"]
    code: Literal[
        "dkim_public_key_observed_not_message_verified",
        "dkim_key_revoked",
        "dkim_malformed_key_or_tags",
        "dkim_key_algorithm_unsupported",
        "dkim_service_unsupported",
    ]
    algorithm: Literal["rsa", "ed25519"] | None = None
    fingerprint: str | None = Field(None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent_key(self):
        codes = {
            "observed": {"dkim_public_key_observed_not_message_verified"},
            "revoked": {"dkim_key_revoked"},
            "malformed": {"dkim_malformed_key_or_tags"},
            "unsupported": {
                "dkim_key_algorithm_unsupported",
                "dkim_service_unsupported",
            },
        }
        if self.code not in codes[self.status] or (self.status == "observed") != (
            self.algorithm is not None and self.fingerprint is not None
        ):
            raise ValueError("public key projection is inconsistent")
        return self


class DNSRecord(_Model):
    section: Literal["answer", "authority", "additional"]
    name: str | None = Field(None, strict=True, max_length=253)
    record_type: str = Field(strict=True, pattern=r"^[A-Z][A-Z0-9]{0,15}$")
    ttl: SmallInt
    values: tuple[SafeText, ...] = Field((), max_length=8)
    interpretation: Literal[
        "retained", "redacted", "uninterpretable", "unsupported"
    ] = "retained"
    public_key: DNSPublicKeyEvidence | None = None

    @model_validator(mode="after")
    def safe_projection(self):
        if (
            self.name is not None
            and self.name != "."
            and canonical_dns_name(self.name) != self.name
        ):
            raise ValueError("record owner is not canonical")
        if self.name is None and self.interpretation == "retained":
            raise ValueError("retained records require a semantic owner")
        for value in self.values:
            if (
                any(ord(c) < 32 or ord(c) == 127 for c in value)
                or redact_evidence_text(value) != value
            ):
                raise ValueError("record contains unsafe retained text")
        if self.public_key is not None and (
            self.record_type != "TXT" or self.name is None
        ):
            raise ValueError("public key projection requires a DKIM TXT owner")
        is_dkim = (
            self.record_type == "TXT"
            and self.name is not None
            and ("_domainkey" in self.name.split(".") or self.public_key is not None)
        )
        if (
            is_dkim
            and self.interpretation == "retained"
            and (
                self.public_key is None
                or any(
                    match.group(1).strip() != "[public key withheld]"
                    for value in self.values
                    for match in re.finditer(
                        r"(?:^|;)\s*p\s*=([^;]*)", value, re.IGNORECASE
                    )
                )
            )
        ):
            raise ValueError("DKIM TXT requires a value-free public key projection")
        if self.interpretation != "retained":
            return self
        try:
            if self.record_type in {"A", "AAAA"}:
                if (
                    len(self.values) != 1
                    or str(ipaddress.ip_address(self.values[0])) != self.values[0]
                    or ipaddress.ip_address(self.values[0]).version
                    != (4 if self.record_type == "A" else 6)
                ):
                    raise ValueError("invalid address")
            elif self.record_type in {"CNAME", "NS"}:
                if (
                    len(self.values) != 1
                    or _record_name(self.values[0]) != self.values[0]
                ):
                    raise ValueError("invalid target")
            elif self.record_type == "SOA":
                if (
                    len(self.values) != 7
                    or any(_record_name(v) != v for v in self.values[:2])
                    or any(
                        not v.isdecimal() or int(v) > 4294967295
                        for v in self.values[2:]
                    )
                ):
                    raise ValueError("invalid SOA")
            elif self.record_type == "MX":
                if (
                    len(self.values) != 2
                    or not self.values[0].isdecimal()
                    or int(self.values[0]) > 65535
                    or _record_name(self.values[1]) != self.values[1]
                ):
                    raise ValueError("invalid MX")
            elif self.record_type == "CAA":
                if (
                    len(self.values) != 3
                    or not self.values[0].isdecimal()
                    or int(self.values[0]) > 255
                    or not re.fullmatch(r"[a-z0-9]{1,15}", self.values[1])
                ):
                    raise ValueError("invalid CAA")
            elif self.record_type == "TXT":
                if len(self.values) != 1:
                    raise ValueError("invalid TXT projection")
            elif self.record_type in {"DS", "DNSKEY"}:
                if (
                    len(self.values) != 4
                    or any(not v.isdecimal() or int(v) > 65535 for v in self.values[:3])
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.values[3])
                ):
                    raise ValueError("invalid key projection")
            else:
                raise ValueError("unsupported retained type")
        except (IndexError, ValueError) as exc:
            raise ValueError("record semantic projection is invalid") from exc
        return self


class DNSExtendedError(_Model):
    code: int = Field(strict=True, ge=0, le=65535)
    text: SafeText = ""
    redacted: bool = Field(False, strict=True)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value):
        if redact_evidence_text(value) != value or any(
            ord(c) < 32 or ord(c) == 127 for c in value
        ):
            raise ValueError("EDE text is unsafe")
        return value


DNSOutcome = Literal[
    "answer",
    "nxdomain",
    "nodata",
    "referral",
    "incomplete",
    "incomplete_cname",
    "cname_loop",
    "servfail",
    "refused",
    "denied",
    "rate_limited",
    "dns_error",
    "truncated",
    "malformed",
    "oversized",
]


class DNSResponse(_Model):
    dns_outcome: DNSOutcome
    response_bytes: int = Field(strict=True, ge=0, le=MAX_DNS_RESPONSE_BYTES + 1)
    rcode: int | None = Field(None, strict=True, ge=0, le=4095)
    truncated: bool = Field(False, strict=True)
    authenticated_data: bool = Field(False, strict=True)
    checking_disabled: bool = Field(False, strict=True)
    authoritative_answer: bool = Field(False, strict=True)
    recursion_available: bool = Field(False, strict=True)
    records: tuple[DNSRecord, ...] = Field((), max_length=MAX_DNS_RECORDS)
    extended_errors: tuple[DNSExtendedError, ...] = Field((), max_length=16)
    http_age_seconds: int | None = Field(None, strict=True, ge=0, le=4294967295)
    cache_state: Literal["unknown", "not_reported_cached", "cached", "stale"] = (
        "unknown"
    )
    freshness: Literal["unknown", "fresh", "stale"] = "unknown"
    remaining_ttl: SmallInt | None = None
    validation: Literal[
        "unverified", "resolver_asserted", "resolver_reported_failure", "contradictory"
    ] = "unverified"
    validation_method: Literal["https-resolver-ad-flag"] = "https-resolver-ad-flag"


def _record_name(value) -> str:
    text = value.to_text() if hasattr(value, "to_text") else value
    return "." if text == "." else canonical_dns_name(text)


def _safe_text(value: str) -> tuple[str, bool]:
    if len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "[uninterpretable text]", True
    safe = redact_evidence_text(value)
    return safe, safe != value


def _project_record(section, rrset, rd, *, dkim_question=False) -> DNSRecord:
    kind = dns.rdatatype.to_text(rrset.rdtype)
    public_key = None
    try:
        name = _record_name(rrset.name)
        if rrset.rdclass != dns.rdataclass.IN:
            raise ValueError("non-IN record")
        if kind in {"A", "AAAA"}:
            values = (rd.address,)
        elif kind in {"CNAME", "NS"}:
            values = (_record_name(rd.target),)
        elif kind == "SOA":
            values = (
                _record_name(rd.mname),
                _record_name(rd.rname),
                *(
                    str(getattr(rd, k))
                    for k in ("serial", "refresh", "retry", "expire", "minimum")
                ),
            )
        elif kind == "MX":
            values = (str(rd.preference), _record_name(rd.exchange))
        elif kind == "CAA":
            values = (
                str(rd.flags),
                rd.tag.decode("ascii").lower(),
                rd.value.decode("utf-8"),
            )
        elif kind == "TXT":
            text = b"".join(rd.strings).decode("utf-8")
            if dkim_question or "_domainkey" in name.split("."):
                status, code, details = _dkim(text)
                public_key = DNSPublicKeyEvidence(
                    status=status,
                    code=code,
                    algorithm=next(
                        (
                            value.partition(":")[2]
                            for value in details
                            if value.startswith("algorithm:")
                        ),
                        None,
                    ),
                    fingerprint=next(
                        (value for value in details if value.startswith("sha256:")),
                        None,
                    ),
                )
                # Do not make arbitrary Base64 into a covert retained payload.
                # Validation runs on transient key bytes; only identity survives.
                text = re.sub(
                    r"((?:^|;)\s*p\s*=)[^;]*",
                    r"\1[public key withheld]",
                    text,
                    flags=re.IGNORECASE,
                )
            values = (text,)
        elif kind == "DS":
            values = (
                str(rd.key_tag),
                str(rd.algorithm),
                str(rd.digest_type),
                "sha256:" + hashlib.sha256(rd.digest).hexdigest(),
            )
        elif kind == "DNSKEY":
            values = (
                str(rd.flags),
                str(rd.protocol),
                str(rd.algorithm),
                "sha256:" + hashlib.sha256(rd.key).hexdigest(),
            )
        else:
            return DNSRecord(
                section=section,
                name=name,
                record_type=kind,
                ttl=rrset.ttl,
                interpretation="unsupported",
            )
        safe = tuple(_safe_text(value) for value in values)
        return DNSRecord(
            section=section,
            name=name,
            record_type=kind,
            ttl=rrset.ttl,
            values=tuple(v for v, _ in safe),
            interpretation=(
                "redacted" if any(redacted for _, redacted in safe) else "retained"
            ),
            public_key=public_key,
        )
    except (UnicodeError, ValueError):
        return DNSRecord(
            section=section,
            name=None,
            record_type=kind,
            ttl=rrset.ttl,
            interpretation="uninterpretable",
        )


def _answers(response, name, kind):
    return tuple(
        r
        for r in response.records
        if r.section == "answer" and r.name == name and r.record_type == kind
    )


def _chain(response, question):
    current, seen, chain = question.name, set(), []
    for _ in range(MAX_DNS_DEPTH + 1):
        if current in seen:
            return current, chain, "cname_loop"
        seen.add(current)
        aliases = _answers(response, current, "CNAME")
        direct = _answers(response, current, question.record_type)
        if aliases and question.record_type != "CNAME" and direct:
            return current, chain, "incomplete_cname"
        if question.record_type == "CNAME" and direct:
            return current, [*chain, *direct], "answer"
        if not aliases:
            return current, chain, None
        if len(aliases) != 1 or aliases[0].interpretation != "retained":
            return current, chain, "incomplete_cname"
        chain.append(aliases[0])
        current = aliases[0].values[0]
    return current, chain, "incomplete_cname"


def _encloses(owner, name):
    return owner is not None and (
        owner == "." or name == owner or name.endswith("." + owner)
    )


def _derived_response(question: DNSQuestion, response: DNSResponse) -> dict:
    if response.dns_outcome in {"malformed", "oversized"}:
        if (
            response.records
            or response.extended_errors
            or response.rcode is not None
            or response.truncated
            or response.authenticated_data
            or response.checking_disabled
            or response.authoritative_answer
            or response.recursion_available
        ):
            raise ValueError("invalid messages cannot carry parsed evidence")
        return {
            "dns_outcome": response.dns_outcome,
            "cache_state": "unknown",
            "freshness": "unknown",
            "remaining_ttl": None,
            "validation": "unverified",
        }
    if response.rcode is None:
        raise ValueError("parsed DNS response requires an rcode")
    codes = {e.code for e in response.extended_errors}
    terminal, chain, chain_outcome = _chain(response, question)
    direct = _answers(response, terminal, question.record_type)
    soa = tuple(
        r
        for r in response.records
        if r.section == "authority"
        and r.record_type == "SOA"
        and r.interpretation == "retained"
        and _encloses(r.name, terminal)
    )
    if response.truncated:
        outcome = "truncated"
    elif codes & {31, 32}:
        outcome = "rate_limited"
    elif codes & {15, 16, 17, 18}:
        outcome = "denied"
    elif response.rcode == 2:
        outcome = "servfail"
    elif response.rcode == 5:
        outcome = "refused"
    elif response.rcode not in {0, 3}:
        outcome = "dns_error"
    elif chain_outcome in {"cname_loop", "incomplete_cname"}:
        outcome = chain_outcome
    elif response.rcode == 3:
        outcome = (
            "dns_error" if direct and question.record_type != "CNAME" else "nxdomain"
        )
    elif direct:
        outcome = "answer"
    elif soa:
        outcome = "nodata"
    elif chain:
        outcome = "incomplete_cname"
    elif any(
        r.section == "authority"
        and r.record_type == "NS"
        and _encloses(r.name, terminal)
        for r in response.records
    ):
        outcome = "referral"
    else:
        outcome = "incomplete"
    validation_failure = bool(codes & {1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 24})
    contradiction = response.authenticated_data and (
        validation_failure
        or response.checking_disabled
        or outcome not in {"answer", "nodata", "nxdomain"}
    )
    validation = (
        "contradictory"
        if contradiction
        else (
            "resolver_reported_failure"
            if validation_failure
            else "resolver_asserted" if response.authenticated_data else "unverified"
        )
    )
    ttl = None
    if outcome == "answer" and all(
        r.interpretation == "retained" for r in (*chain, *direct)
    ):
        ttl = min(r.ttl for r in (*chain, *direct))
    elif (
        outcome == "incomplete_cname"
        and chain
        and all(r.interpretation == "retained" for r in chain)
    ):
        ttl = min(r.ttl for r in chain)
    elif outcome in {"nodata", "nxdomain"} and soa:
        ttl = min(
            [*(r.ttl for r in chain), *(min(r.ttl, int(r.values[6])) for r in soa)]
        )
    cache = (
        "unknown"
        if response.http_age_seconds is None
        else "cached" if response.http_age_seconds > 0 else "not_reported_cached"
    )
    remaining = (
        None
        if ttl is None or response.http_age_seconds is None
        else max(0, ttl - response.http_age_seconds)
    )
    stale = bool(codes & {3, 19}) or remaining == 0
    freshness = "stale" if stale else "fresh" if remaining is not None else "unknown"
    return {
        "dns_outcome": outcome,
        "cache_state": "stale" if stale else cache,
        "freshness": freshness,
        "remaining_ttl": remaining,
        "validation": validation,
    }


def parse_dns_response(
    question: DNSQuestion,
    wire: bytes,
    *,
    http_age_seconds: int | None = None,
    max_response_bytes: int = MAX_DNS_RESPONSE_BYTES,
) -> DNSResponse:
    """Strict bounded RFC8484 response parsing; errors never retain peer bytes."""
    question = DNSQuestion.model_validate(question)
    if (
        type(wire) is not bytes
        or type(max_response_bytes) is not int
        or not 12 <= max_response_bytes <= MAX_DNS_RESPONSE_BYTES
    ):
        raise ValueError("DNS parser requires bytes and a bounded response limit")
    if http_age_seconds is not None and (
        type(http_age_seconds) is not int or not 0 <= http_age_seconds <= 4294967295
    ):
        raise ValueError("HTTP Age must be a bounded integer or unknown")
    size = min(len(wire), MAX_DNS_RESPONSE_BYTES + 1)
    if len(wire) > max_response_bytes:
        return DNSResponse(dns_outcome="oversized", response_bytes=size)
    try:
        message = dns.message.from_wire(
            wire,
            ignore_trailing=False,
            continue_on_error=False,
            raise_on_truncation=False,
            one_rr_per_rrset=True,
        )
        if (
            message.id != 0
            or not message.flags & dns.flags.QR
            or message.opcode() != dns.opcode.QUERY
            or len(message.question) != 1
        ):
            raise ValueError("invalid response header")
        actual = message.question[0]
        if (
            actual.rdclass != dns.rdataclass.IN
            or dns.rdatatype.to_text(actual.rdtype) != question.record_type
            or _record_name(actual.name) != question.name
        ):
            raise ValueError("response question mismatch")
        records = []
        for section in ("answer", "authority", "additional"):
            for rrset in getattr(message, section):
                for rd in rrset:
                    if len(records) >= MAX_DNS_RECORDS:
                        raise ValueError("response record budget exceeded")
                    records.append(
                        _project_record(
                            section,
                            rrset,
                            rd,
                            dkim_question="_domainkey" in question.name.split("."),
                        )
                    )
        errors = []
        for option in message.options:
            if isinstance(option, dns.edns.EDEOption):
                text, redacted = _safe_text(option.text or "")
                errors.append(
                    DNSExtendedError(
                        code=int(option.code), text=text, redacted=redacted
                    )
                )
        raw = DNSResponse(
            dns_outcome="incomplete",
            response_bytes=size,
            rcode=message.rcode(),
            truncated=bool(message.flags & dns.flags.TC),
            authenticated_data=bool(message.flags & dns.flags.AD),
            checking_disabled=bool(message.flags & dns.flags.CD),
            authoritative_answer=bool(message.flags & dns.flags.AA),
            recursion_available=bool(message.flags & dns.flags.RA),
            records=tuple(records),
            extended_errors=tuple(errors),
            http_age_seconds=http_age_seconds,
        )
        return DNSResponse.model_validate(
            {**raw.model_dump(), **_derived_response(question, raw)}
        )
    except (dns.exception.DNSException, ValueError, UnicodeError, IndexError):
        return DNSResponse(dns_outcome="malformed", response_bytes=size)


class DNSObservation(_Model):
    """One counted reservation, replaced in place when its result is known.

    ``in_flight`` means reserved and possibly sent, not proof of transmission.
    ``interrupted`` preserves an unresolved reservation after recovery; neither
    state invents a completion time or refunds the original attempt budget.
    """

    question: DNSQuestion
    attempt: int = Field(strict=True, ge=1, le=MAX_DNS_ATTEMPTS)
    depth: int = Field(0, strict=True, ge=0, le=MAX_DNS_DEPTH)
    parent_attempt: int | None = Field(None, strict=True, ge=1, le=MAX_DNS_ATTEMPTS)
    started_at: datetime
    finished_at: datetime | None = None
    transport_outcome: Literal[
        "in_flight",
        "interrupted",
        "response",
        "timeout",
        "denied",
        "rate_limited",
        "http_error",
        "network_error",
        "canceled",
        "untested",
    ]
    http_status: int | None = Field(None, strict=True, ge=100, le=599)
    response: DNSResponse | None = None
    response_omitted: Literal["output_limit"] | None = None
    source: Literal["deployment-https-resolver"] = "deployment-https-resolver"

    @model_validator(mode="after")
    def coherent_observation(self):
        uncertain = self.transport_outcome in {"in_flight", "interrupted"}
        if uncertain != (self.finished_at is None):
            raise ValueError("only unresolved reservations omit a completion time")
        if self.started_at.utcoffset() is None or (
            self.finished_at is not None
            and (
                self.finished_at.utcoffset() is None
                or self.finished_at < self.started_at
            )
        ):
            raise ValueError("observation requires ordered timezone-aware times")
        has_response = self.response is not None or self.response_omitted is not None
        if (self.transport_outcome == "response") != has_response or (
            self.response is not None and self.response_omitted is not None
        ):
            raise ValueError(
                "successful HTTP transport requires DNS evidence or its explicit omission"
            )
        if uncertain and self.http_status is not None:
            raise ValueError("unresolved reservations cannot assert an HTTP outcome")
        if self.transport_outcome == "response" and self.http_status not in {None, 200}:
            raise ValueError("DNS responses require HTTP 200")
        if (self.depth == 0) != (self.parent_attempt is None):
            raise ValueError("follow-up requires a parent attempt")
        return self


class DNSExecution(_Model):
    """An immutable checkpoint, including useful progress before job completion."""

    schema_version: Literal["dns-execution/v1"] = "dns-execution/v1"
    plan_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolver: DNSResolverConfig
    started_at: datetime
    checkpoint_at: datetime
    finished_at: datetime | None = None
    observations: tuple[DNSObservation, ...] = Field((), max_length=MAX_DNS_ATTEMPTS)
    stop_reason: Literal[
        "in_progress",
        "complete",
        "attempt_limit",
        "duration_limit",
        "output_limit",
        "canceled",
        "failed",
    ]

    @model_validator(mode="after")
    def ordered_times(self):
        if (
            self.started_at.utcoffset() is None
            or self.checkpoint_at.utcoffset() is None
            or self.checkpoint_at < self.started_at
        ):
            raise ValueError("execution requires ordered timezone-aware times")
        if (self.stop_reason == "in_progress") != (self.finished_at is None):
            raise ValueError("unfinished execution has no terminal completion time")
        if self.finished_at is not None and (
            self.finished_at.utcoffset() is None
            or self.finished_at != self.checkpoint_at
        ):
            raise ValueError("terminal checkpoint must identify its actual finish time")
        return self


def followup_questions(
    plan: DNSQueryPlan, parent: DNSObservation
) -> tuple[DNSQuestion, ...]:
    """Enumerate useful, explicitly admitted follow-ups without granting names."""
    if parent.response is None or parent.response.dns_outcome in {
        "malformed",
        "oversized",
        "truncated",
        "servfail",
        "refused",
        "denied",
        "rate_limited",
        "dns_error",
    }:
        return ()
    choices = set()
    terminal, chain, _ = _chain(parent.response, parent.question)
    for record in parent.response.records:
        if record.interpretation != "retained" or record.section != "answer":
            continue
        if (
            record.record_type == "CNAME"
            and record.values[0] != "."
            and (record.name == parent.question.name or record in chain)
        ):
            choices.add(
                DNSQuestion(
                    name=record.values[0], record_type=parent.question.record_type
                )
            )
        if (
            record.name == parent.question.name
            and record.record_type in {"NS", "MX"}
            and parent.question.record_type == record.record_type
            and record.values[-1] != "."
        ):
            for kind in ("A", "AAAA"):
                choices.add(DNSQuestion(name=record.values[-1], record_type=kind))
    if parent.question.record_type == "CAA" and parent.response.dns_outcome in {
        "nodata",
        "nxdomain",
    }:
        ancestor = terminal.partition(".")[2]
        if ancestor:
            choices.add(DNSQuestion(name=ancestor, record_type="CAA"))
    for check in plan.assessment.checks:
        if (
            check.check == "dmarc"
            and check.organizational_domain is not None
            and parent.question
            == DNSQuestion(name=f"_dmarc.{check.subject}", record_type="TXT")
            and _usable(parent)
            and not _mail_policies(parent, "dmarc")
        ):
            choices.add(
                DNSQuestion(
                    name=f"_dmarc.{check.organizational_domain}", record_type="TXT"
                )
            )
    return tuple(
        sorted(
            (q for q in choices if permitted_followup(plan, q, depth=parent.depth + 1)),
            key=lambda q: (q.name, q.record_type),
        )
    )


def validate_dns_execution(
    plan: DNSQueryPlan, execution: DNSExecution | dict
) -> DNSExecution:
    """Recheck original authority/bounds and recompute semantic claims at collection."""
    if build_dns_query_plan(plan.assessment, plan.resolver) != plan:
        raise ValueError("DNS plan does not match its canonical identity")
    if isinstance(execution, DNSExecution):
        execution = execution.model_dump(mode="json", by_alias=True)
    body = json.dumps(execution, allow_nan=False, separators=(",", ":"))
    if len(body.encode()) > plan.assessment.max_output_bytes:
        raise ValueError("DNS execution exceeds the original output budget")
    result = DNSExecution.model_validate_json(body)
    if result.plan_key != plan.plan_key or result.resolver != plan.resolver:
        raise ValueError("DNS execution belongs to a different plan or resolver")
    limits = plan.assessment
    if len(result.observations) > limits.max_attempts:
        raise ValueError("DNS execution exceeds its original attempt budget")
    # Checkpoint/publication may happen later after recovery. Only admitted
    # operations consume the original deadline; a later report grants no time.
    deadline = result.started_at + timedelta(seconds=limits.timeout_seconds)
    prior, completed = {}, set()
    last_start = None
    for index, observation in enumerate(result.observations, 1):
        if observation.question in completed:
            raise ValueError("a completed successful DNS question cannot be replayed")
        if (
            observation.attempt != index
            or not result.started_at <= observation.started_at <= result.checkpoint_at
            or observation.started_at >= deadline
        ):
            raise ValueError(
                "DNS attempt sequence or original start deadline is invalid"
            )
        if observation.transport_outcome == "in_flight" and (
            index != len(result.observations) or result.stop_reason != "in_progress"
        ):
            raise ValueError(
                "only the final reservation of unfinished execution can be in flight"
            )
        if observation.finished_at is not None:
            if observation.finished_at > result.checkpoint_at:
                raise ValueError("observation completes after its checkpoint")
            # A fixed one-second allowance records timeout cancellation/cleanup,
            # never permission to initiate an operation after the deadline.
            if (
                observation.finished_at > deadline + timedelta(seconds=1)
                or (observation.finished_at - observation.started_at).total_seconds()
                > limits.query_timeout_seconds + 1
            ):
                raise ValueError("DNS query duration exceeds its original budget")
        if (
            last_start is not None
            and (observation.started_at - last_start).total_seconds() + 0.001
            < 1 / limits.queries_per_second
        ):
            raise ValueError("DNS attempts exceed the original query rate")
        last_start = observation.started_at
        if observation.depth == 0:
            if observation.question not in limits.questions:
                raise ValueError("unadmitted initial DNS question")
        else:
            parent = prior.get(observation.parent_attempt)
            if (
                parent is None
                or observation.depth != parent.depth + 1
                or observation.question not in followup_questions(plan, parent)
            ):
                raise ValueError("DNS follow-up lacks exact authority and provenance")
        if observation.response is not None:
            response = observation.response
            if (
                response.response_bytes > limits.max_response_bytes
                and response.dns_outcome != "oversized"
            ):
                raise ValueError("oversized response cannot have interpreted evidence")
            for key, value in _derived_response(observation.question, response).items():
                if getattr(response, key) != value:
                    raise ValueError(
                        "DNS response claims differ from retained semantic evidence"
                    )
        if _usable(observation):
            completed.add(observation.question)
        prior[index] = observation
    return result


class DNSCheckResult(_Model):
    check: Check
    subject: str
    selector: str | None = None
    status: Literal[
        "observed", "missing", "malformed", "revoked", "incomplete", "unsupported"
    ]
    code: str = Field(pattern=r"^[a-z0-9_]{1,128}$")
    questions: tuple[DNSQuestion, ...] = Field((), max_length=MAX_DNS_ATTEMPTS)
    observation_attempts: tuple[int, ...] = Field((), max_length=MAX_DNS_ATTEMPTS)
    # Only fixed semantic facts or previously sanitized record identities.
    details: tuple[SafeText, ...] = Field((), max_length=64)


class DNSAssessmentSummary(_Model):
    schema_version: Literal["dns-assessment/v1"] = "dns-assessment/v1"
    status: Literal["complete", "partial", "failed"]
    evidence_ref: str = Field(pattern=r"^(?:execution\.json|art_[A-Za-z0-9_-]{1,124})$")
    resolver: DNSResolverConfig
    settings: DNSAssessment
    execution: DNSExecution
    checks: tuple[DNSCheckResult, ...] = Field((), max_length=1024)
    covered_questions: tuple[DNSQuestion, ...] = Field((), max_length=MAX_DNS_ATTEMPTS)
    uncovered_questions: tuple[DNSQuestion, ...] = Field(
        (), max_length=MAX_DNS_ATTEMPTS
    )
    comparison_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_expires_at: datetime | None = None
    query_count: int = Field(strict=True, ge=0, le=MAX_DNS_ATTEMPTS)
    interpretation: str = INTERPRETATION


def _usable(observation, *, at=None):
    response = observation.response
    if (
        response is not None
        and at is not None
        and (
            response.remaining_ttl is None
            or response.remaining_ttl <= (at - observation.finished_at).total_seconds()
        )
    ):
        return False
    return (
        response is not None
        and response.dns_outcome in {"answer", "nodata", "nxdomain"}
        and response.freshness == "fresh"
        and response.validation not in {"contradictory", "resolver_reported_failure"}
        and all(
            r.interpretation == "retained"
            for r in response.records
            if r.section == "answer"
            and (
                r.name is None
                or r.record_type in {observation.question.record_type, "CNAME"}
            )
        )
    )


def _txt(observation):
    terminal, _, _ = _chain(observation.response, observation.question)
    return tuple(
        r.values[0]
        for r in _answers(observation.response, terminal, "TXT")
        if r.interpretation == "retained"
    )


def _mail_policies(observation, check):
    """Select exact policy versions, shared by scheduling and interpretation.

    DKIM's version tag is optional. Explicit versions other than DKIM1 are
    ignored, while malformed supported records remain available for diagnosis.
    """
    texts = _txt(observation)
    if check == "spf":
        return tuple(
            text for text in texts if re.match(r"^v=spf1(?: |$)", text, re.IGNORECASE)
        )
    candidates = []
    for text in texts:
        try:
            first = next(_tag_pairs(text), None)
        except ValueError:
            first = None
        if check == "dmarc":
            if first is not None and first[0] == "v" and first[1].lower() == "dmarc1":
                candidates.append(text)
        elif first is None or first[0] != "v" or first[1] == "DKIM1":
            candidates.append(text)
    return tuple(candidates)


def _record_expiry(observation, records):
    """Freshness of the exact records supporting a derived conclusion."""
    if (
        not records
        or observation.response.http_age_seconds is None
        or any(record.interpretation != "retained" for record in records)
    ):
        return None
    return observation.finished_at + timedelta(
        seconds=min(record.ttl for record in records)
        - observation.response.http_age_seconds
    )


def _tag_pairs(text):
    """One grammar for first-tag selection and complete policy parsing."""
    for item in text.split(";"):
        if not item.strip():
            continue
        name, sep, value = item.strip().partition("=")
        if not sep or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError("invalid policy tags")
        yield name, value.strip()


def _tags(text):
    tags = {}
    for name, value in _tag_pairs(text):
        if name in tags:
            raise ValueError("invalid policy tags")
        tags[name] = value
    return tags


def _spf_domain(value):
    if "%" not in value:
        canonical_dns_name(value)
        return
    # Syntax-only macro validation: expansion never occurs here or in transport.
    scrubbed = re.sub(
        r"%(?:[%_-]|\{[slodipvhSLODIPVH][0-9]*r?[.\-+,/_=]*\})", "macro", value
    )
    if "%" in scrubbed:
        raise ValueError("invalid SPF macro")
    canonical_dns_name(scrubbed)


def _spf_mechanism(token, mechanism):
    rest = token[len(mechanism) :]
    if mechanism in {"include", "exists"}:
        if not rest.startswith(":") or not rest[1:]:
            raise ValueError("SPF domain missing")
        _spf_domain(rest[1:])
    elif mechanism == "ptr":
        if rest:
            if not rest.startswith(":"):
                raise ValueError("invalid PTR syntax")
            _spf_domain(rest[1:])
    else:
        match = re.fullmatch(
            r"(?::((?:%\{[^}]*\}|[^/])+))?(?:/([0-9]{1,3}))?(?://([0-9]{1,3}))?", rest
        )
        if match is None:
            raise ValueError("invalid SPF address mechanism")
        domain, ipv4, ipv6 = match.groups()
        if domain:
            _spf_domain(domain)
        if (ipv4 is not None and int(ipv4) > 32) or (
            ipv6 is not None and int(ipv6) > 128
        ):
            raise ValueError("invalid SPF prefix")


def _spf(text):
    terms = text.split()
    if not terms or terms[0].lower() != "v=spf1":
        return "malformed", "spf_invalid_version", ()
    incomplete, details, modifiers = False, [], set()
    for term in terms[1:]:
        token = term.lstrip("+-~?")
        if len(term) - len(token) > 1:
            return "malformed", "spf_invalid_qualifier", ()
        if re.match(r"^[A-Za-z][A-Za-z0-9_.-]*=", token):
            name, value = token.split("=", 1)
            name = name.lower()
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name)
                or not value
                or name in modifiers
                or token != term
            ):
                return "malformed", "spf_invalid_modifier", ()
            try:
                if name.lower() in {"redirect", "exp"}:
                    _spf_domain(value)
            except ValueError:
                return "malformed", "spf_invalid_modifier_domain", ()
            modifiers.add(name)
            incomplete = True
            details.append("modifier:" + name)
            continue
        mechanism = re.split(r"[:/]", token, maxsplit=1)[0].lower()
        if mechanism not in {
            "all",
            "ip4",
            "ip6",
            "include",
            "a",
            "mx",
            "ptr",
            "exists",
        }:
            return "malformed", "spf_unknown_mechanism", ()
        details.append("mechanism:" + mechanism)
        if mechanism == "all":
            if token.lower() != "all":
                return "malformed", "spf_invalid_all", ()
        elif mechanism in {"ip4", "ip6"}:
            try:
                address = token.split(":", 1)[1]
                network = ipaddress.ip_network(address, strict=False)
                if network.version != (4 if mechanism == "ip4" else 6):
                    raise ValueError("wrong family")
            except (IndexError, ValueError):
                return "malformed", "spf_invalid_address", ()
        else:
            try:
                _spf_mechanism(token, mechanism)
            except ValueError:
                return "malformed", "spf_invalid_mechanism_domain_or_prefix", ()
            incomplete = True
    return (
        ("incomplete", "spf_expansion_not_performed", tuple(details))
        if incomplete
        else ("observed", "spf_syntax_observed_not_message_verified", tuple(details))
    )


def _dkim(text):
    try:
        tags = _tags(text)
        if (
            tags.get("v", "DKIM1") != "DKIM1"
            or "p" not in tags
            or ("v" in tags and next(iter(tags)) != "v")
        ):
            raise ValueError("invalid DKIM version/key")
        if not tags["p"]:
            return "revoked", "dkim_key_revoked", ()
        kind = tags.get("k", "rsa")
        if kind not in {"rsa", "ed25519"}:
            return "unsupported", "dkim_key_algorithm_unsupported", ()
        key = base64.b64decode(re.sub(r"\s", "", tags["p"]), validate=True)
        if kind == "ed25519":
            if len(key) != 32:
                raise ValueError("invalid Ed25519 key")
        else:
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
            from cryptography.hazmat.primitives.serialization import load_der_public_key

            parsed = load_der_public_key(key)
            if not isinstance(parsed, RSAPublicKey):
                raise ValueError("not an RSA key")
        if tags.get("s", "*") not in {"*", "email"}:
            return "unsupported", "dkim_service_unsupported", ()
        return (
            "observed",
            "dkim_public_key_observed_not_message_verified",
            ("algorithm:" + kind, "sha256:" + hashlib.sha256(key).hexdigest()),
        )
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return "malformed", "dkim_malformed_key_or_tags", ()


def _dmarc(text):
    try:
        tags = _tags(text)
        if (
            next(iter(tags), None) != "v"
            or tags.get("v") != "DMARC1"
            or tags.get("p") not in {"none", "quarantine", "reject"}
        ):
            raise ValueError("invalid DMARC version/policy")
        if "sp" in tags and tags["sp"] not in {"none", "quarantine", "reject"}:
            raise ValueError("invalid subdomain policy")
        if any(tags.get(key, "r") not in {"r", "s"} for key in ("adkim", "aspf")):
            raise ValueError("invalid alignment")
        if (
            not re.fullmatch(r"[0-9]{1,3}", tags.get("pct", "100"))
            or int(tags.get("pct", "100")) > 100
        ):
            raise ValueError("invalid percentage")
        if "ri" in tags and not re.fullmatch(r"[0-9]{1,10}", tags["ri"]):
            raise ValueError("invalid report interval")
        if "fo" in tags and any(
            v not in {"0", "1", "d", "s"} for v in tags["fo"].split(":")
        ):
            raise ValueError("invalid failure option")
        # Report endpoints are never followed and are not needed to state policy.
        return (
            "observed",
            "dmarc_exact_policy_observed",
            tuple(
                f"{key}:{tags[key]}"
                for key in ("p", "sp", "pct", "adkim", "aspf")
                if key in tags
            ),
        )
    except ValueError:
        return "malformed", "dmarc_malformed_policy", ()


def _check_results(plan, observations, selection, as_of, coverage_deadlines):
    required = selection.required_questions()
    rows = [observations[q] for q in required if q in observations]

    def result(
        status, code, *, questions=required, selected=rows, selector=None, details=()
    ):
        details = tuple(details)
        if len(details) > 64 or any(len(detail) > 4096 for detail in details):
            # Preserve complete semantic values, not a truncated assertion.
            # Native records remain intact; the omitted summary is a coverage gap.
            details = (
                *tuple(detail for detail in details if len(detail) <= 4096)[:62],
                f"original_result:{code}",
                "semantic_details_omitted",
            )
            status = "unsupported" if status == "unsupported" else "incomplete"
            code = selection.check + "_semantic_details_limited"
        return DNSCheckResult(
            check=selection.check,
            subject=selection.subject,
            selector=selector,
            status=status,
            code=code,
            questions=questions,
            observation_attempts=tuple(row.attempt for row in selected),
            details=details,
        )

    if selection.check == "dnssec":
        rows = [row for q, row in observations.items() if q.name == selection.subject]
        questions = tuple(row.question for row in rows)
        if not rows or not all(_usable(row, at=as_of) for row in rows):
            return [
                result(
                    "incomplete",
                    "dnssec_missing_or_invalid_resolver_evidence",
                    questions=questions,
                    selected=rows,
                )
            ]
        asserted = all(row.response.validation == "resolver_asserted" for row in rows)
        return [
            result(
                "observed",
                (
                    "resolver_asserted_not_independently_validated"
                    if asserted
                    else "ad_not_asserted_security_unknown"
                ),
                questions=questions,
                selected=rows,
            )
        ]
    if selection.check in {"spf", "dkim", "dmarc"}:
        results = []
        for q in required[:1] if selection.check == "dmarc" else required:
            row = observations.get(q)
            selector = (
                q.name.removesuffix("._domainkey." + selection.subject)
                if selection.check == "dkim"
                else None
            )
            if row is None or not _usable(row, at=as_of):
                results.append(
                    result(
                        "incomplete",
                        "mail_question_uncovered",
                        questions=(q,),
                        selected=[] if row is None else [row],
                        selector=selector,
                    )
                )
                continue
            texts = _mail_policies(row, selection.check)
            if not texts and selection.check == "dmarc":
                fallback = next((other for other in required if other != q), None)
                fallback_row = observations.get(fallback)
                if fallback_row is not None and _usable(fallback_row, at=as_of):
                    fallback_texts = _mail_policies(fallback_row, "dmarc")
                    if len(fallback_texts) == 1:
                        status, code, details = _dmarc(fallback_texts[0])
                        results.append(
                            result(
                                status,
                                (
                                    "dmarc_supplied_fallback_policy"
                                    if status == "observed"
                                    else code
                                ),
                                questions=(q, fallback),
                                selected=[row, fallback_row],
                                details=details,
                            )
                        )
                    elif len(fallback_texts) > 1:
                        results.append(
                            result(
                                "malformed",
                                "dmarc_multiple_fallback_policies",
                                questions=(q, fallback),
                                selected=[row, fallback_row],
                            )
                        )
                    else:
                        results.append(
                            result(
                                "missing",
                                "dmarc_missing_at_exact_and_supplied_fallback",
                                questions=(q, fallback),
                                selected=[row, fallback_row],
                            )
                        )
                else:
                    results.append(
                        result(
                            "incomplete",
                            "dmarc_organizational_fallback_uncovered",
                            questions=(q,),
                            selected=[row],
                        )
                    )
                continue
            if not texts:
                results.append(
                    result(
                        "missing",
                        selection.check + "_policy_missing",
                        questions=(q,),
                        selected=[row],
                        selector=selector,
                    )
                )
            elif len(texts) > 1:
                results.append(
                    result(
                        "malformed",
                        selection.check + "_multiple_policies",
                        questions=(q,),
                        selected=[row],
                        selector=selector,
                    )
                )
            else:
                if selection.check == "dkim":
                    terminal, _, _ = _chain(row.response, q)
                    key = next(
                        record.public_key
                        for record in _answers(row.response, terminal, "TXT")
                        if record.values == (texts[0],)
                    )
                    if key is None:
                        status, code, details = (
                            "incomplete",
                            "dkim_key_projection_uncovered",
                            (),
                        )
                    else:
                        status, code = key.status, key.code
                        details = tuple(
                            value
                            for value in (
                                (
                                    ("algorithm:" + key.algorithm)
                                    if key.algorithm
                                    else None
                                ),
                                key.fingerprint,
                            )
                            if value is not None
                        )
                else:
                    status, code, details = {"spf": _spf, "dmarc": _dmarc}[
                        selection.check
                    ](texts[0])
                results.append(
                    result(
                        status,
                        code,
                        questions=(q,),
                        selected=[row],
                        selector=selector,
                        details=details,
                    )
                )
        return results
    if selection.check == "delegation":
        if len(rows) != len(required) or not all(
            _usable(row, at=as_of) for row in rows
        ):
            return [result("incomplete", "resolver_view_ns_soa_uncovered")]
        details, used = [], list(rows)
        for row in rows:
            terminal, _, _ = _chain(row.response, row.question)
            for record in _answers(row.response, terminal, row.question.record_type):
                details.append(record.record_type + ":" + " ".join(record.values))
                if record.record_type == "NS" and record.values[0] != ".":
                    for kind in ("A", "AAAA"):
                        address = observations.get(
                            DNSQuestion(name=record.values[0], record_type=kind)
                        )
                        if address is not None and _usable(address, at=as_of):
                            if address not in used:
                                used.append(address)
                            address_terminal, _, _ = _chain(
                                address.response, address.question
                            )
                            details.extend(
                                kind + ":" + record.values[0]
                                for record in _answers(
                                    address.response, address_terminal, kind
                                )
                            )
        return [
            result(
                (
                    "observed"
                    if all(row.response.dns_outcome == "answer" for row in rows)
                    else "missing"
                ),
                "resolver_view_only_parent_child_unverified",
                selected=used,
                questions=tuple(row.question for row in used),
                details=tuple(details),
            )
        ]
    if selection.check == "cname":
        name, visited, used, active = selection.subject, set(), [], None
        for _ in range(MAX_DNS_DEPTH + 1):
            if name in visited:
                return [
                    result(
                        "observed",
                        "cname_loop_observed",
                        questions=tuple(row.question for row in used),
                        selected=used,
                    )
                ]
            visited.add(name)
            q = DNSQuestion(name=name, record_type="CNAME")
            explicit = observations.get(q)
            if explicit is not None and _usable(explicit, at=as_of):
                # A fresh admitted query can replace stale embedded alias data.
                active = explicit
                if active not in used:
                    used.append(active)
            if active is None:
                return [
                    result(
                        "incomplete",
                        "cname_target_uncovered_hypothesis_only",
                        questions=(q,),
                        selected=used,
                    )
                ]
            records = _answers(active.response, name, "CNAME")
            if not records:
                return [
                    result(
                        "observed" if active.question == q else "incomplete",
                        (
                            "cname_chain_observed"
                            if active.question == q
                            else "cname_target_uncovered_hypothesis_only"
                        ),
                        questions=tuple(row.question for row in used),
                        selected=used,
                    )
                ]
            expires_at = _record_expiry(active, records)
            if expires_at is None or expires_at <= as_of:
                return [
                    result(
                        "incomplete",
                        "cname_supporting_record_uncovered",
                        questions=tuple(row.question for row in used),
                        selected=used,
                    )
                ]
            coverage_deadlines[active.question] = min(
                coverage_deadlines[active.question], expires_at
            )
            if len(records) != 1:
                return [
                    result(
                        "malformed",
                        "cname_conflicting_targets",
                        questions=tuple(row.question for row in used),
                        selected=used,
                    )
                ]
            if records[0].values[0] == ".":
                return [
                    result(
                        "unsupported",
                        "cname_root_target_unsupported",
                        questions=tuple(row.question for row in used),
                        selected=used,
                    )
                ]
            name = records[0].values[0]
        return [
            result(
                "incomplete",
                "cname_depth_uncovered",
                questions=tuple(row.question for row in used),
                selected=used,
            )
        ]
    if selection.check == "caa":
        q, seen, used = required[0], set(), []
        for _ in range(plan.assessment.max_followup_depth + 1):
            if q.name in seen:
                return [
                    result(
                        "malformed",
                        "caa_alias_loop",
                        questions=tuple(r.question for r in used),
                        selected=used,
                    )
                ]
            seen.add(q.name)
            row = observations.get(q)
            alias_only = (
                row is not None
                and row.response is not None
                and row.response.dns_outcome == "incomplete_cname"
                and row.response.remaining_ttl is not None
                and row.response.remaining_ttl
                > (as_of - row.finished_at).total_seconds()
                and row.response.validation
                not in {"contradictory", "resolver_reported_failure"}
            )
            if row is None or not (_usable(row, at=as_of) or alias_only):
                return [
                    result(
                        "incomplete",
                        "caa_alias_or_ancestor_uncovered",
                        questions=tuple(r.question for r in used) or (q,),
                        selected=used,
                    )
                ]
            used.append(row)
            terminal, _, _ = _chain(row.response, q)
            records = _answers(row.response, terminal, "CAA")
            if records:
                critical = any(
                    int(r.values[0]) & 128
                    and r.values[1] not in {"issue", "issuewild", "iodef"}
                    for r in records
                )
                return [
                    result(
                        "unsupported" if critical else "observed",
                        (
                            "caa_unknown_critical_tag"
                            if critical
                            else "caa_policy_observed"
                        ),
                        questions=tuple(r.question for r in used),
                        selected=used,
                        details=tuple(" ".join(r.values) for r in records),
                    )
                ]
            if alias_only:
                if terminal == ".":
                    return [
                        result(
                            "unsupported",
                            "caa_root_alias_unsupported",
                            questions=tuple(r.question for r in used),
                            selected=used,
                        )
                    ]
                q = DNSQuestion(name=terminal, record_type="CAA")
                continue
            ancestor = terminal.partition(".")[2]
            if not ancestor:
                return [
                    result(
                        "missing",
                        "caa_no_policy_in_explicit_ancestor_coverage",
                        questions=tuple(r.question for r in used),
                        selected=used,
                    )
                ]
            q = DNSQuestion(name=ancestor, record_type="CAA")
        return [
            result(
                "incomplete",
                "caa_depth_uncovered",
                questions=tuple(r.question for r in used),
                selected=used,
            )
        ]
    return [result("unsupported", "check_unsupported")]


def summarize_dns_assessment(
    plan: DNSQueryPlan, execution: DNSExecution | dict, evidence_ref: str
) -> DNSAssessmentSummary:
    execution = validate_dns_execution(plan, execution)
    # A failed retry cannot erase an earlier completed observation in this job.
    observations = {}
    for row in execution.observations:
        if (
            row.question not in observations
            or _usable(row, at=execution.checkpoint_at)
            or not _usable(observations[row.question], at=execution.checkpoint_at)
        ):
            observations[row.question] = row
    coverage_deadlines = {
        question: row.finished_at + timedelta(seconds=row.response.remaining_ttl)
        for question, row in observations.items()
        if _usable(row, at=execution.checkpoint_at)
    }
    checks = tuple(
        result
        for selection in plan.assessment.checks
        for result in _check_results(
            plan, observations, selection, execution.checkpoint_at, coverage_deadlines
        )
    )
    uncovered = {
        q
        for q in (*plan.assessment.questions, *plan.assessment.allowed_followups)
        if q not in observations
        or not _usable(observations[q], at=execution.checkpoint_at)
    }
    for check in checks:
        if check.status in {"incomplete", "unsupported"}:
            uncovered.update(check.questions)
    authority = tuple(
        sorted(
            set(plan.assessment.questions + plan.assessment.allowed_followups),
            key=lambda q: (q.name, q.record_type),
        )
    )
    if execution.stop_reason == "in_progress":
        uncovered.update(authority)
    covered = tuple(q for q in authority if q not in uncovered)
    # No timestamps, responses, artifacts or outcomes enter comparison identity.
    # Generic report comparison remains the owner of remediation decisions.
    return DNSAssessmentSummary(
        status=(
            "complete"
            if not uncovered and execution.stop_reason == "complete"
            else (
                "partial"
                if execution.observations or execution.stop_reason == "in_progress"
                else "failed"
            )
        ),
        evidence_ref=evidence_ref,
        resolver=plan.resolver,
        settings=plan.assessment,
        execution=execution,
        checks=checks,
        covered_questions=covered,
        uncovered_questions=tuple(q for q in authority if q in uncovered),
        comparison_key=plan.plan_key,
        query_count=len(execution.observations),
        coverage_expires_at=min(
            (coverage_deadlines[q] for q in covered),
            default=None,
        ),
    )


def serialize_dns_artifacts(
    plan: DNSQueryPlan,
    execution: DNSExecution | dict,
    evidence_ref: str,
    *,
    reserve_terminal: bool = False,
) -> dict[str, bytes]:
    """Canonical artifacts share one output budget, with checkpoint headroom.

    The runner uses ``reserve_terminal=True`` before checkpointing/reserving any
    network attempt. Finalization uses the same artifacts without that reserve.
    A response that cannot fit is not retained over the earlier checkpoint.
    """
    summary = summarize_dns_assessment(plan, execution, evidence_ref)
    bodies = {
        DNS_OUTPUT_FILES["execution"]: summary.execution.model_dump_json(
            by_alias=True
        ).encode(),
        DNS_OUTPUT_FILES["summary"]: summary.model_dump_json(by_alias=True).encode(),
    }
    reserve = DNS_TERMINAL_RESERVE_BYTES if reserve_terminal else 0
    if sum(map(len, bodies.values())) + reserve > plan.assessment.max_output_bytes:
        raise ValueError("DNS artifacts exceed the original combined output budget")
    return bodies
