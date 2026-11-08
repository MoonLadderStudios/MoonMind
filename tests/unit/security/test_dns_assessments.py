"""Hermetic DNS contracts, wire parsing, coverage and selected policy checks."""

import json
from datetime import UTC, datetime, timedelta

import dns.edns
import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest
from pydantic import ValidationError

from moonmind.security.dns_assessments import (
    DNSAssessment,
    DNSCheckSelection,
    DNSExecution,
    DNSObservation,
    DNSQuestion,
    DNSResolverConfig,
    build_dns_query_plan,
    build_dns_query_wire,
    canonical_dns_name,
    parse_dns_response,
    permitted_followup,
    summarize_dns_assessment,
    validate_dns_execution,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def question(name="example.com", record_type="A"):
    return DNSQuestion(name=name, record_type=record_type)


def assessment(*, questions=None, **kwargs):
    return DNSAssessment(
        questions=tuple(questions or [question()]),
        **kwargs,
    )


def plan(a=None):
    return build_dns_query_plan(
        a or assessment(),
        DNSResolverConfig(endpoint="https://resolver.example/dns-query"),
    )


def response(q=None, answers=(), authority=(), *, rcode=0, flags=0, ede=()):
    q = q or question()
    request = dns.message.from_wire(build_dns_query_wire(q))
    result = dns.message.make_response(request)
    result.origin = dns.name.root
    result.flags |= flags
    result.set_rcode(rcode)
    result.answer.extend(dns.rrset.from_text(*rr) for rr in answers)
    result.authority.extend(dns.rrset.from_text(*rr) for rr in authority)
    if ede:
        result.use_edns(options=[dns.edns.EDEOption(code, text) for code, text in ede])
        result.set_rcode(rcode)
    return result


def parsed(q=None, answers=(), authority=(), *, age=0, **kwargs):
    q = q or question()
    return parse_dns_response(
        q, response(q, answers, authority, **kwargs).to_wire(), http_age_seconds=age
    )


def observation(q=None, result=None, *, attempt=1, depth=0, parent_attempt=None):
    return DNSObservation(
        question=q or question(),
        attempt=attempt,
        depth=depth,
        parent_attempt=parent_attempt,
        started_at=NOW + timedelta(seconds=attempt),
        finished_at=NOW + timedelta(seconds=attempt, milliseconds=10),
        transport_outcome="response",
        response=result
        or parsed(answers=[("example.com", 120, "IN", "A", "192.0.2.1")]),
    )


def execution(p, *observations):
    return DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=NOW,
        checkpoint_at=NOW + timedelta(seconds=20),
        finished_at=NOW + timedelta(seconds=20),
        observations=observations,
        stop_reason="complete",
    )


@pytest.mark.parametrize(
    "value",
    [
        "*.example.com",
        "a..com",
        "a.com..",
        "a\\046b.com",
        "a%2eb.com",
        "a\n.com",
        "é.com",
        "xn--.com",
        "-a.com",
        "a-.com",
        ".",
    ],
)
def test_ambiguous_names_rejected(value):
    with pytest.raises(ValueError):
        canonical_dns_name(value)


def test_canonical_ascii_underscore_and_immutable_authority():
    assert canonical_dns_name("_DMARC.Example.COM.") == "_dmarc.example.com"
    assert canonical_dns_name("xn--bcher-kva.example") == "xn--bcher-kva.example"
    q = question()
    a = assessment(questions=[q], allowed_followups=(question("alias.example", "A"),))
    p = plan(a)
    with pytest.raises(ValidationError):
        q.name = "elsewhere.example"
    assert permitted_followup(p, question("alias.example"), depth=1)
    assert not permitted_followup(p, question("child.example.com"), depth=1)
    assert not permitted_followup(p, question("alias.example", "TXT"), depth=1)
    assert not permitted_followup(p, question("alias.example"), depth=9)
    assert DNSAssessment.model_validate_json(a.model_dump_json()) == a
    assert p == build_dns_query_plan(a, p.resolver)


@pytest.mark.parametrize(
    "url",
    [
        "http://resolver.example/dns-query",
        "https://u:p@resolver.example/",
        "https://resolver.example/?x=1",
        "https://resolver.example/#x",
        "https://resolver.example/\\bad",
        "https://resolver.example/%2e%2e/path",
    ],
)
def test_resolver_endpoint_has_no_caller_authority_or_ambiguous_parts(url):
    with pytest.raises(ValueError):
        DNSResolverConfig(endpoint=url)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 257},
        {"max_response_bytes": 65537},
        {"max_output_bytes": 4194305},
        {"max_followup_depth": 9},
        {"queries_per_second": 21},
        {"max_attempts": True},
        {"timeout_seconds": "60"},
    ],
)
def test_original_budget_ceilings_are_strict(kwargs):
    with pytest.raises(ValueError):
        assessment(**kwargs)


def test_name_and_serialized_argv_bounds():
    with pytest.raises(ValueError):
        assessment(questions=[question(f"n{i}.example") for i in range(65)])
    long_name = ".".join(["a" * 63] * 3 + ["a" * 61])
    with pytest.raises(ValueError):
        assessment(questions=[question(f"n{i}." + long_name) for i in range(2)])


def test_dkim_requires_supplied_admitted_selector_questions():
    with pytest.raises(ValueError):
        DNSCheckSelection(check="dkim", subject="example.com")
    selection = DNSCheckSelection(
        check="dkim", subject="example.com", selectors=("selector1",)
    )
    with pytest.raises(ValueError):
        assessment(checks=(selection,))
    a = assessment(
        questions=[question("selector1._domainkey.example.com", "TXT")],
        checks=(selection,),
    )
    assert a.checks[0].selectors == ("selector1",)


def test_request_is_bounded_wire_not_network():
    msg = dns.message.from_wire(build_dns_query_wire(question()))
    assert msg.id == 0 and len(msg.question) == 1
    assert msg.flags & dns.flags.RD and not msg.flags & dns.flags.CD
    assert msg.ednsflags & dns.flags.DO


def test_matching_response_preserves_structured_source_and_ttl():
    result = parsed(answers=[("example.com", 120, "IN", "A", "192.0.2.1")], age=5)
    assert result.dns_outcome == "answer"
    assert result.cache_state == "cached" and result.freshness == "fresh"
    assert result.remaining_ttl == 115
    assert result.records[0].section == "answer"
    assert result.records[0].values == ("192.0.2.1",)
    assert result.validation == "unverified"
    assert "wire" not in result.model_dump_json().lower()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: setattr(m, "id", 7),
        lambda m: setattr(m, "flags", m.flags & ~dns.flags.QR),
        lambda m: m.set_opcode(1),
        lambda m: m.question.clear(),
        lambda m: m.question.append(m.question[0]),
        lambda m: setattr(m.question[0], "name", dns.name.from_text("other.example")),
        lambda m: setattr(m.question[0], "rdtype", dns.rdatatype.AAAA),
        lambda m: setattr(m.question[0], "rdclass", dns.rdataclass.CH),
    ],
)
def test_invalid_header_and_question_are_not_observations(mutate):
    message = response()
    mutate(message)
    assert (
        parse_dns_response(
            question(), message.to_wire(), http_age_seconds=0
        ).dns_outcome
        == "malformed"
    )


@pytest.mark.parametrize(
    "wire",
    [
        b"",
        b"\x00" * 11,
        b"\x00\x00\x81\x80\x00\x01\x00\x00\x00\x00\x00\x00\xc0\x0c\x00\x01\x00\x01",
    ],
)
def test_invalid_wire_and_compression_loop_are_closed(wire):
    assert (
        parse_dns_response(question(), wire, http_age_seconds=0).dns_outcome
        == "malformed"
    )


def test_no_trailing_bytes_oversized_or_partial_answer_success():
    wire = response(answers=[("example.com", 120, "IN", "A", "192.0.2.1")]).to_wire()
    for bad in (wire + b"x", wire[:-1]):
        assert (
            parse_dns_response(question(), bad, http_age_seconds=0).dns_outcome
            == "malformed"
        )
    assert (
        parse_dns_response(question(), wire, max_response_bytes=12).dns_outcome
        == "oversized"
    )
    result = parsed(
        answers=[("example.com", 120, "IN", "A", "192.0.2.1")], flags=dns.flags.TC
    )
    assert result.dns_outcome == "truncated" and result.freshness == "unknown"


@pytest.mark.parametrize(
    "rcode,outcome",
    [
        (dns.rcode.SERVFAIL, "servfail"),
        (dns.rcode.REFUSED, "refused"),
        (dns.rcode.NXDOMAIN, "nxdomain"),
        (dns.rcode.BADVERS, "dns_error"),
    ],
)
def test_dns_rcodes_are_distinct_including_edns_extended(rcode, outcome):
    result = parsed(rcode=rcode)
    assert result.rcode == rcode and result.dns_outcome == outcome


def test_negative_freshness_requires_enclosing_soa_and_accounts_for_age():
    soa = (
        "example.com",
        600,
        "IN",
        "SOA",
        "ns.example.com hostmaster.example.com 1 2 3 4 90",
    )
    result = parsed(authority=[soa], rcode=dns.rcode.NXDOMAIN, age=40)
    assert result.dns_outcome == "nxdomain" and result.remaining_ttl == 50
    assert result.cache_state == "cached" and result.freshness == "fresh"
    assert parsed(authority=[soa], age=90).freshness == "stale"
    assert parsed(authority=[soa], age=None).freshness == "unknown"
    assert parsed(rcode=dns.rcode.NXDOMAIN).freshness == "unknown"
    assert (
        parsed(
            authority=[
                (
                    "unrelated.example",
                    60,
                    "IN",
                    "SOA",
                    "ns.unrelated.example h.unrelated.example 1 2 3 4 60",
                )
            ]
        ).dns_outcome
        == "incomplete"
    )
    assert parsed(authority=[soa]).dns_outcome == "nodata"
    assert (
        parsed(
            authority=[("example.com", 60, "IN", "NS", "ns.example.com")]
        ).dns_outcome
        == "referral"
    )


def test_cname_is_not_nodata_or_takeover_and_target_ttl_limits_freshness():
    alias = ("example.com", 50, "IN", "CNAME", "alias.example")
    result = parsed(answers=[alias])
    assert result.dns_outcome == "incomplete_cname"
    result = parsed(
        answers=[alias, ("alias.example", 120, "IN", "A", "192.0.2.1")], age=51
    )
    assert result.dns_outcome == "answer" and result.freshness == "stale"
    assert (
        parsed(
            answers=[alias, ("alias.example", 60, "IN", "CNAME", "example.com")]
        ).dns_outcome
        == "cname_loop"
    )


def test_ede_stale_validation_contradictions_and_denied_are_separate():
    answer = [("example.com", 120, "IN", "A", "192.0.2.1")]
    assert parsed(answers=answer, flags=dns.flags.AD).validation == "resolver_asserted"
    assert (
        parsed(answers=answer, flags=dns.flags.AD, ede=[(6, "bogus")]).validation
        == "contradictory"
    )
    assert (
        parsed(answers=answer, flags=dns.flags.AD, rcode=dns.rcode.SERVFAIL).validation
        == "contradictory"
    )
    assert parsed(answers=answer, ede=[(3, "stale")]).freshness == "stale"
    assert parsed(ede=[(15, "blocked")]).dns_outcome == "denied"
    assert parsed(ede=[(31, "rate limited")]).dns_outcome == "rate_limited"


def test_sanitization_before_normal_retention_never_encodes_raw_peer_data():
    secret = "ghp_" + "a" * 36
    q = question("example.com", "TXT")
    result = parsed(
        q,
        [("example.com", 60, "IN", "TXT", f'"v=spf1 token={secret} -all"')],
        ede=[(0, f"token={secret}")],
    )
    body = result.model_dump_json()
    assert secret not in body and "base64" not in body and "wire" not in body
    assert result.records[0].interpretation == "redacted"
    p = plan(
        assessment(
            questions=[q],
            checks=(DNSCheckSelection(check="spf", subject="example.com"),),
        )
    )
    summary = summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    )
    assert summary.checks[0].status == "incomplete"
    assert not summary.covered_questions


def test_execution_reconstruction_rejects_extra_fields_endpoint_scope_and_followup_forgery():
    p = plan()
    e = execution(p, observation())
    assert validate_dns_execution(p, e.model_dump(mode="json", by_alias=True)) == e
    for edit in (
        lambda v: v.update(raw="hidden"),
        lambda v: v["resolver"].update(endpoint="https://other.example/"),
        lambda v: v["observations"][0]["question"].update(name="other.example"),
        lambda v: v["observations"][0].update(depth=1, parentAttempt=9),
        lambda v: v["observations"][0]["response"].update(dnsOutcome="nodata"),
    ):
        payload = e.model_dump(mode="json", by_alias=True)
        edit(payload)
        with pytest.raises(ValueError):
            validate_dns_execution(p, payload)


def test_missing_stale_invalid_failed_questions_remain_uncovered_and_key_is_stable():
    p = plan()
    fresh = summarize_dns_assessment(p, execution(p, observation()), "art_one")
    assert fresh.covered_questions == (question(),)
    stale = parsed(answers=[("example.com", 1, "IN", "A", "192.0.2.1")], age=2)
    partial = summarize_dns_assessment(
        p, execution(p, observation(result=stale)), "art_two"
    )
    missing = summarize_dns_assessment(p, execution(p), "art_three")
    assert partial.uncovered_questions == missing.uncovered_questions == (question(),)
    assert partial.comparison_key == missing.comparison_key == fresh.comparison_key
    assert fresh.evidence_ref == "art_one"


def check_summary(kind, name, txt=None, **selection):
    q = question(name, "TXT")
    a = assessment(
        questions=[q],
        checks=(DNSCheckSelection(check=kind, subject="example.com", **selection),),
    )
    p = plan(a)
    result = parsed(
        q,
        [(name, 120, "IN", "TXT", json.dumps(txt))] if txt is not None else [],
        authority=[
            (
                "example.com",
                120,
                "IN",
                "SOA",
                "ns.example.com hostmaster.example.com 1 2 3 4 120",
            )
        ],
    )
    return summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    ).checks[0]


@pytest.mark.parametrize(
    "text,state",
    [
        ("v=spf1 ip4:192.0.2.0/24 -all", "observed"),
        ("v=spf1 include:other.example -all", "incomplete"),
        ("v=spf1 mx -all", "incomplete"),
        ("v=spf1 a:%{d} -all", "incomplete"),
        ("v=spf1 ip4:broken -all", "malformed"),
        ("v=spf1 unknown -all", "malformed"),
        (None, "missing"),
    ],
)
def test_spf_explicit_syntax_without_expansion(text, state):
    assert check_summary("spf", "example.com", text).status == state


@pytest.mark.parametrize(
    "text,state",
    [
        ("v=DKIM1; p=", "revoked"),
        ("v=DKIM1; p=not-base64!", "malformed"),
        ("v=DKIM1; k=ed25519; p=" + "A" * 43 + "=", "observed"),
        (None, "missing"),
    ],
)
def test_dkim_only_explicit_selector_and_basic_key_state(text, state):
    assert (
        check_summary(
            "dkim", "s1._domainkey.example.com", text, selectors=("s1",)
        ).status
        == state
    )


@pytest.mark.parametrize(
    "text,state",
    [
        ("v=DMARC1; p=reject", "observed"),
        ("v=DMARC1; p=none; pct=101", "malformed"),
        ("v=DMARC1; p=reject; p=none", "malformed"),
        (None, "incomplete"),
    ],
)
def test_dmarc_exact_policy_no_organizational_fallback_invention(text, state):
    assert check_summary("dmarc", "_dmarc.example.com", text).status == state


def test_caa_without_explicit_ancestor_is_incomplete_and_dnssec_does_not_claim_chain():
    q = question("example.com", "CAA")
    a = assessment(
        questions=[q],
        checks=(
            DNSCheckSelection(check="caa", subject="example.com"),
            DNSCheckSelection(check="dnssec", subject="example.com"),
        ),
    )
    p = plan(a)
    result = parsed(
        q,
        authority=[
            (
                "example.com",
                120,
                "IN",
                "SOA",
                "ns.example.com h.example.com 1 2 3 4 120",
            )
        ],
        flags=dns.flags.AD,
    )
    summary = summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    )
    assert summary.checks[0].status == "incomplete"
    assert summary.checks[1].code == "resolver_asserted_not_independently_validated"
    assert not summary.covered_questions


def test_valid_followup_keeps_parent_provenance_and_injected_names_create_no_authority():
    from moonmind.security.dns_assessments import followup_questions

    initial, admitted = question(), question("alias.example")
    p = plan(assessment(allowed_followups=(admitted,)))
    first = observation(
        result=parsed(
            answers=[
                ("example.com", 120, "IN", "CNAME", "alias.example"),
                ("unrelated.example", 120, "IN", "CNAME", "trap.example"),
            ]
        )
    )
    assert followup_questions(p, first) == (admitted,)
    second = observation(
        admitted,
        parsed(admitted, [("alias.example", 120, "IN", "A", "192.0.2.2")]),
        attempt=2,
        depth=1,
        parent_attempt=1,
    )
    assert (
        validate_dns_execution(p, execution(p, first, second))
        .observations[1]
        .parent_attempt
        == 1
    )
    with pytest.raises(ValueError):
        validate_dns_execution(
            p, execution(p, first, second.model_copy(update={"depth": 2}))
        )
    assert followup_questions(plan(), first) == ()


def test_caa_can_follow_explicit_alias_and_ancestor_but_never_invents_authority():
    from moonmind.security.dns_assessments import followup_questions

    q, alias, ancestor = (
        question("example.com", "CAA"),
        question("alias.example", "CAA"),
        question("example", "CAA"),
    )
    p = plan(
        assessment(
            questions=[q],
            allowed_followups=(alias, ancestor),
            checks=(DNSCheckSelection(check="caa", subject=q.name),),
        )
    )
    first = observation(q, parsed(q, [(q.name, 120, "IN", "CNAME", alias.name)]))
    assert followup_questions(p, first) == (alias,)
    second = observation(
        alias,
        parsed(
            alias,
            authority=[
                ("example", 120, "IN", "SOA", "ns.example h.example 1 2 3 4 120")
            ],
        ),
        attempt=2,
        depth=1,
        parent_attempt=1,
    )
    assert followup_questions(p, second) == (ancestor,)
    third = observation(
        ancestor,
        parsed(ancestor, [("example", 120, "IN", "CAA", '0 issue "ca.example"')]),
        attempt=3,
        depth=2,
        parent_attempt=2,
    )
    result = summarize_dns_assessment(p, execution(p, first, second, third), "art_dns")
    assert result.checks[0].status == "observed"
    assert result.checks[0].questions == (q, alias, ancestor)
    assert result.checks[0].observation_attempts == (1, 2, 3)


def test_cname_loop_in_one_response_is_a_finding_without_expansion():
    q = question("example.com", "CNAME")
    p = plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check="cname", subject=q.name),)
        )
    )
    result = parsed(
        q,
        [
            (q.name, 120, "IN", "CNAME", "alias.example"),
            ("alias.example", 120, "IN", "CNAME", q.name),
        ],
    )
    summary = summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    )
    assert summary.checks[0].code == "cname_loop_observed"


def test_multiple_cname_targets_are_not_collapsed_by_wire_parser():
    q = question()
    message = response(
        q,
        [
            (q.name, 120, "IN", "CNAME", "one.example"),
            (q.name, 120, "IN", "CNAME", "two.example"),
        ],
    )
    parsed_response = parse_dns_response(q, message.to_wire(), http_age_seconds=0)
    assert len(parsed_response.records) == 2
    assert parsed_response.dns_outcome == "incomplete_cname"


def test_explicit_dmarc_fallback_keeps_exact_subject_first_and_provenance():
    subject = "z.example.com"
    q, fallback = (
        question("_dmarc." + subject, "TXT"),
        question("_dmarc.example.com", "TXT"),
    )
    selection = DNSCheckSelection(
        check="dmarc", subject=subject, organizational_domain="example.com"
    )
    p = plan(
        assessment(questions=[q], allowed_followups=(fallback,), checks=(selection,))
    )
    first = observation(
        q,
        parsed(
            q,
            authority=[
                (
                    "example.com",
                    120,
                    "IN",
                    "SOA",
                    "ns.example.com h.example.com 1 2 3 4 120",
                )
            ],
        ),
    )
    second = observation(
        fallback,
        parsed(
            fallback,
            [(fallback.name, 120, "IN", "TXT", '"v=DMARC1; p=reject; sp=quarantine"')],
        ),
        attempt=2,
        depth=1,
        parent_attempt=1,
    )
    check = summarize_dns_assessment(p, execution(p, first, second), "art_dns").checks[
        0
    ]
    assert check.code == "dmarc_supplied_fallback_policy"
    assert check.questions == (q, fallback)
    assert check.observation_attempts == (1, 2)


@pytest.mark.parametrize(
    "text",
    [
        "v=spf1 mx/129 -all",
        "v=spf1 a:bad..example -all",
        "v=spf1 include:bad..example -all",
        "v=spf1 redirect=bad..example",
        "v=spf1 a:%{broken -all",
        "v=spf1 ip4:192.0.2.0/33 -all",
    ],
)
def test_spf_invalid_static_or_macro_syntax_is_not_just_unexpanded(text):
    assert check_summary("spf", "example.com", text).status == "malformed"


def test_answer_expired_during_job_is_not_comparison_coverage():
    p = plan()
    row = observation(
        result=parsed(answers=[("example.com", 5, "IN", "A", "192.0.2.1")])
    )
    summary = summarize_dns_assessment(p, execution(p, row), "art_dns")
    assert summary.uncovered_questions == (question(),)


def test_unknown_redacted_answer_owner_prevents_false_terminal_negative():
    q = question()
    message = response(
        q,
        authority=[
            (
                "example.com",
                120,
                "IN",
                "SOA",
                "ns.example.com h.example.com 1 2 3 4 120",
            )
        ],
    )
    message.answer.append(
        dns.rrset.from_text("bad\\046label.example.", 120, "IN", "A", "192.0.2.1")
    )
    result = parse_dns_response(q, message.to_wire(), http_age_seconds=0)
    p = plan()
    assert not summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    ).covered_questions


def test_failed_retry_preserves_other_completed_observations():
    other = question("other.example")
    p = plan(assessment(questions=[question(), other]))
    first = observation()
    failed = tuple(
        DNSObservation(
            question=other,
            attempt=i,
            started_at=NOW + timedelta(seconds=i),
            finished_at=NOW + timedelta(seconds=i, milliseconds=10),
            transport_outcome="timeout",
        )
        for i in (2, 3)
    )
    result = summarize_dns_assessment(p, execution(p, first, *failed), "art_dns")
    assert result.covered_questions == (question(),)
    assert result.uncovered_questions == (other,)
    assert len(result.execution.observations) == 3


def test_completed_question_cannot_be_replayed_into_contradictory_coverage():
    p = plan()
    conflicting = observation(
        result=parsed(answers=[("example.com", 120, "IN", "A", "192.0.2.99")]),
        attempt=2,
    )
    with pytest.raises(ValueError, match="completed"):
        validate_dns_execution(p, execution(p, observation(), conflicting))


def test_transient_retry_can_recover_without_resetting_attempt_count():
    p = plan()
    first = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=1, milliseconds=10),
        transport_outcome="timeout",
    )
    second = observation(attempt=2)
    summary = summarize_dns_assessment(p, execution(p, first, second), "art_dns")
    assert summary.covered_questions == (question(),) and summary.query_count == 2


def test_checkpoint_serialization_reserves_bounded_terminal_envelope():
    from moonmind.security.dns_assessments import (
        DNS_TERMINAL_RESERVE_BYTES,
        serialize_dns_artifacts,
    )

    p = plan()
    checkpoint = execution(p, observation())
    bodies = serialize_dns_artifacts(p, checkpoint, "art_dns", reserve_terminal=True)
    assert (
        sum(map(len, bodies.values()))
        <= p.assessment.max_output_bytes - DNS_TERMINAL_RESERVE_BYTES
    )
    final = checkpoint.model_copy(update={"stop_reason": "duration_limit"})
    final_bodies = serialize_dns_artifacts(p, final, "art_dns")
    assert (
        sum(map(len, final_bodies.values())) - sum(map(len, bodies.values()))
        < DNS_TERMINAL_RESERVE_BYTES
    )
    small = plan(assessment(max_output_bytes=4096))
    with pytest.raises(ValueError, match="output budget"):
        serialize_dns_artifacts(
            small, execution(small), "art_dns", reserve_terminal=True
        )


@pytest.mark.parametrize(
    "alter",
    [
        lambda e: e["observations"][0].update(attempt=2),
        lambda e: e["observations"][0].update(finishedAt="2026-01-01T00:00:30Z"),
        lambda e: e.update(finishedAt="2026-01-01T00:20:00Z"),
    ],
)
def test_collector_rejects_attempt_and_original_duration_overruns(alter):
    p = plan()
    body = execution(p, observation()).model_dump(mode="json", by_alias=True)
    alter(body)
    with pytest.raises(ValueError):
        validate_dns_execution(p, body)


def test_collector_rejects_original_rate_and_count_overruns():
    p = plan(assessment(max_attempts=1))
    with pytest.raises(ValueError):
        validate_dns_execution(p, execution(p, observation(), observation(attempt=2)))
    p = plan()
    second = observation(attempt=2).model_copy(
        update={
            "started_at": NOW + timedelta(seconds=1, milliseconds=1),
            "finished_at": NOW + timedelta(seconds=1, milliseconds=2),
        }
    )
    with pytest.raises(ValueError):
        validate_dns_execution(p, execution(p, observation(), second))


def test_delegation_address_observations_carry_query_provenance():
    ns, soa, address = (
        question("example.com", "NS"),
        question("example.com", "SOA"),
        question("ns.example.com", "A"),
    )
    p = plan(
        assessment(
            questions=[ns, soa],
            allowed_followups=(address,),
            checks=(DNSCheckSelection(check="delegation", subject="example.com"),),
        )
    )
    first = observation(ns, parsed(ns, [(ns.name, 120, "IN", "NS", address.name)]))
    second = observation(
        soa,
        parsed(
            soa,
            [(soa.name, 120, "IN", "SOA", "ns.example.com h.example.com 1 2 3 4 120")],
        ),
        attempt=2,
    )
    third = observation(
        address,
        parsed(address, [(address.name, 120, "IN", "A", "192.0.2.1")]),
        attempt=3,
        depth=1,
        parent_attempt=1,
    )
    check = summarize_dns_assessment(
        p, execution(p, first, second, third), "art_dns"
    ).checks[0]
    assert address in check.questions and 3 in check.observation_attempts
    assert check.code == "resolver_view_only_parent_child_unverified"


def test_resolver_rejects_empty_port_and_overlong_underscore_label():
    with pytest.raises(ValueError):
        DNSResolverConfig(endpoint="https://resolver.example:/dns-query")
    with pytest.raises(ValueError):
        canonical_dns_name("_" + "a" * 63 + ".example")


def test_plan_codec_is_canonical_bounded_and_has_fixed_runner_output():
    from moonmind.security.dns_assessments import (
        DNS_OUTPUT_DIRECTORY,
        DNS_RUNNER_MODULE,
        build_dns_command,
        decode_dns_query_plan,
        encode_dns_query_plan,
    )

    p = plan(
        assessment(
            questions=[
                question(f"name{i}.very-long-admitted-subject-for-codec.example")
                for i in range(64)
            ]
        )
    )
    chunks = encode_dns_query_plan(p)
    assert all(0 < len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1 and len(build_dns_command(p)) <= 128
    assert decode_dns_query_plan(chunks) == p
    assert build_dns_command(p) == ["-I", "-m", DNS_RUNNER_MODULE, *chunks]
    assert DNS_OUTPUT_DIRECTORY == "/outputs/dns-assessment"
    for invalid in [
        (),
        ("x" * 4097,),
        (" " + "".join(chunks),),
        (*chunks, " "),
        tuple(
            "".join(chunks)[i : i + 4000] for i in range(0, len("".join(chunks)), 4000)
        ),
    ]:
        with pytest.raises(ValueError):
            decode_dns_query_plan(invalid)


def test_dkim_arbitrary_base64_is_never_retained_as_public_key_evidence():
    import base64

    q = question("s1._domainkey.example.com", "TXT")
    secret = "ghp_" + "z" * 36
    encoded = base64.b64encode(secret.encode()).decode()
    result = parsed(
        q, [(q.name, 120, "IN", "TXT", json.dumps("v=DKIM1; p=" + encoded))]
    )
    assert (
        secret not in result.model_dump_json()
        and encoded not in result.model_dump_json()
    )
    p = plan(
        assessment(
            questions=[q],
            checks=(
                DNSCheckSelection(
                    check="dkim", subject="example.com", selectors=("s1",)
                ),
            ),
        )
    )
    assert (
        summarize_dns_assessment(p, execution(p, observation(q, result)), "art_dns")
        .checks[0]
        .status
        == "malformed"
    )


def test_dkim_valid_key_is_retained_as_semantic_identity_not_raw_base64():
    import base64

    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    q = question("s1._domainkey.example.com", "TXT")
    key = (
        generate_private_key(public_exponent=65537, key_size=1024)
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    encoded = base64.b64encode(key).decode()
    # TXT wire chunks join to one policy; the Base64 is not retained afterward.
    policy = "v=DKIM1; p=" + encoded
    chunks = " ".join(
        json.dumps(policy[i : i + 200]) for i in range(0, len(policy), 200)
    )
    result = parsed(q, [(q.name, 120, "IN", "TXT", chunks)])
    assert encoded not in result.model_dump_json()
    p = plan(
        assessment(
            questions=[q],
            checks=(
                DNSCheckSelection(
                    check="dkim", subject="example.com", selectors=("s1",)
                ),
            ),
        )
    )
    check = summarize_dns_assessment(
        p, execution(p, observation(q, result)), "art_dns"
    ).checks[0]
    assert check.status == "observed" and any(
        d.startswith("sha256:") for d in check.details
    )


def test_artifact_serialization_recomputes_summary_and_enforces_combined_bound():
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    p = plan()
    bodies = serialize_dns_artifacts(p, execution(p, observation()), "art_dns")
    assert set(bodies) == {"execution.json", "summary.json"}
    assert json.loads(bodies["summary.json"])["comparisonKey"] == p.plan_key
    assert sum(map(len, bodies.values())) <= p.assessment.max_output_bytes
    p = plan(assessment(max_output_bytes=4096))
    with pytest.raises(ValueError):
        serialize_dns_artifacts(
            p, execution(p, *[observation(attempt=i) for i in range(1, 8)]), "art_dns"
        )


def test_root_target_and_null_mx_cannot_create_followup_or_break_summary():
    from moonmind.security.dns_assessments import followup_questions

    q = question("example.com", "MX")
    p = plan(assessment(questions=[q]))
    row = observation(q, parsed(q, [(q.name, 120, "IN", "MX", "0 .")]))
    assert followup_questions(p, row) == ()
    q = question("example.com", "CNAME")
    p = plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check="cname", subject=q.name),)
        )
    )
    row = observation(q, parsed(q, [(q.name, 120, "IN", "CNAME", ".")]))
    assert followup_questions(p, row) == ()
    assert summarize_dns_assessment(p, execution(p, row), "art_dns").checks[
        0
    ].status in {"incomplete", "unsupported", "malformed"}


def test_dkim_alias_target_keys_are_projected_without_encoded_retention():
    import base64

    q = question("s1._domainkey.example.com", "TXT")
    encoded = base64.b64encode(b"untrusted value in key field").decode()
    result = parsed(
        q,
        [
            (q.name, 120, "IN", "CNAME", "key.example"),
            ("key.example", 120, "IN", "TXT", json.dumps("v=DKIM1; p=" + encoded)),
        ],
    )
    assert encoded not in result.model_dump_json()
    p = plan(
        assessment(
            questions=[q],
            checks=(
                DNSCheckSelection(
                    check="dkim", subject="example.com", selectors=("s1",)
                ),
            ),
        )
    )
    assert (
        summarize_dns_assessment(p, execution(p, observation(q, result)), "art_dns")
        .checks[0]
        .status
        == "malformed"
    )


def test_native_times_reject_numeric_coercion():
    p = plan()
    body = execution(p, observation()).model_dump(mode="json", by_alias=True)
    body["startedAt"] = 1767225600
    with pytest.raises(ValueError):
        validate_dns_execution(p, body)


def test_maximal_plan_rejects_oversize_argv_instead_of_trimming():
    from itertools import combinations, islice

    selectors = tuple(f"s{i}." + ".".join(["a" * 60] * 3) for i in range(16))
    questions = [
        question(f"{selector}._domainkey.example.com", "TXT") for selector in selectors
    ]
    checks = tuple(
        DNSCheckSelection(check="dkim", subject="example.com", selectors=selected)
        for selected in islice(combinations(selectors, 14), 64)
    )
    with pytest.raises(ValueError, match="argv"):
        assessment(questions=questions, checks=checks)


def test_inflight_checkpoint_counts_reservation_without_inventing_completion():
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    p = plan()
    reserved = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=1),
        transport_outcome="in_flight",
    )
    progress = DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=NOW,
        checkpoint_at=NOW + timedelta(seconds=1),
        stop_reason="in_progress",
        observations=(reserved,),
    )
    bodies = serialize_dns_artifacts(p, progress, "art_dns", reserve_terminal=True)
    retained = json.loads(bodies["execution.json"])
    assert (
        retained["finishedAt"] is None
        and retained["observations"][0]["finishedAt"] is None
    )
    rebuilt = validate_dns_execution(p, retained)
    summary = summarize_dns_assessment(p, rebuilt, "art_dns")
    assert summary.query_count == 1 and not summary.covered_questions
    assert summary.status == "partial" and summary.coverage_expires_at is None
    assert summary.uncovered_questions == (question(),)


def test_completed_observations_survive_host_loss_but_unfinished_job_is_not_comparable():
    other = question("other.example")
    p = plan(assessment(questions=[question(), other]))
    reserved = DNSObservation(
        question=other,
        attempt=2,
        started_at=NOW + timedelta(seconds=2),
        transport_outcome="in_flight",
    )
    progress = DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=NOW,
        checkpoint_at=NOW + timedelta(seconds=2),
        stop_reason="in_progress",
        observations=(observation(), reserved),
    )
    rebuilt = validate_dns_execution(p, progress.model_dump(mode="json", by_alias=True))
    assert rebuilt.observations[0].response.records[0].values == ("192.0.2.1",)
    summary = summarize_dns_assessment(p, rebuilt, "art_dns")
    assert summary.query_count == 2 and not summary.covered_questions
    interrupted = reserved.model_copy(update={"transport_outcome": "interrupted"})
    recovered = progress.model_copy(
        update={
            "checkpoint_at": NOW + timedelta(seconds=3),
            "observations": (observation(), interrupted),
        }
    )
    assert validate_dns_execution(p, recovered).observations[1].finished_at is None


def test_inflight_reservation_is_completed_in_place_not_an_extra_attempt():
    p = plan()
    reserved = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=1),
        transport_outcome="in_flight",
    )
    progress = DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=NOW,
        checkpoint_at=NOW + timedelta(seconds=1),
        stop_reason="in_progress",
        observations=(reserved,),
    )
    completed = progress.model_copy(
        update={
            "checkpoint_at": NOW + timedelta(seconds=2),
            "finished_at": NOW + timedelta(seconds=2),
            "stop_reason": "complete",
            "observations": (observation(),),
        }
    )
    summary = summarize_dns_assessment(p, completed, "art_dns")
    assert summary.query_count == 1 and summary.covered_questions == (question(),)


def test_collector_rejects_unfinished_attempts_claimed_as_complete_or_untested():
    p = plan()
    with pytest.raises(ValueError):
        DNSObservation(
            question=question(), attempt=1, started_at=NOW, transport_outcome="untested"
        )
    with pytest.raises(ValueError):
        DNSExecution(
            plan_key=p.plan_key,
            resolver=p.resolver,
            started_at=NOW,
            checkpoint_at=NOW + timedelta(seconds=1),
            stop_reason="complete",
        )
    reserved = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=1),
        transport_outcome="in_flight",
    )
    with pytest.raises(ValueError):
        validate_dns_execution(p, execution(p, reserved))


def test_received_response_omitted_for_output_budget_has_truthful_transport():
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    other = question("other.example")
    p = plan(assessment(questions=[question(), other]))
    omitted = DNSObservation(
        question=other,
        attempt=2,
        started_at=NOW + timedelta(seconds=2),
        finished_at=NOW + timedelta(seconds=2, milliseconds=10),
        transport_outcome="response",
        http_status=200,
        response_omitted="output_limit",
    )
    result = execution(p, observation(), omitted).model_copy(
        update={"stop_reason": "output_limit"}
    )
    bodies = serialize_dns_artifacts(p, result, "art_dns")
    summary = json.loads(bodies["summary.json"])
    assert summary["queryCount"] == 2
    assert summary["coveredQuestions"] == [{"name": "example.com", "recordType": "A"}]
    assert summary["uncoveredQuestions"] == [
        {"name": "other.example", "recordType": "A"}
    ]
    assert summary["execution"]["observations"][1]["responseOmitted"] == "output_limit"
    assert summary["execution"]["observations"][1]["transportOutcome"] == "response"
    with pytest.raises(ValueError):
        DNSObservation.model_validate(
            omitted.model_dump() | {"transport_outcome": "timeout"}
        )


def test_last_moment_start_cannot_borrow_another_full_query_timeout():
    p = plan(assessment(timeout_seconds=10, query_timeout_seconds=10))
    late = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=9),
        finished_at=NOW + timedelta(seconds=12),
        transport_outcome="timeout",
    )
    # Three seconds fits the query timeout, but exceeds the original job deadline
    # and its fixed one-second cancellation bookkeeping tolerance.
    with pytest.raises(ValueError, match="duration"):
        validate_dns_execution(p, execution(p, late))
    after_deadline = late.model_copy(
        update={
            "started_at": NOW + timedelta(seconds=10),
            "finished_at": NOW + timedelta(seconds=10, milliseconds=1),
        }
    )
    with pytest.raises(ValueError, match="deadline"):
        validate_dns_execution(p, execution(p, after_deadline))


def test_late_recovery_checkpoint_grants_no_query_time_or_fresh_coverage():
    p = plan()
    completed = observation()
    later = execution(p, completed).model_copy(
        update={
            "checkpoint_at": NOW + timedelta(days=1),
            "finished_at": NOW + timedelta(days=1),
            "stop_reason": "duration_limit",
        }
    )
    rebuilt = validate_dns_execution(p, later)
    assert rebuilt.observations[0].finished_at == completed.finished_at
    summary = summarize_dns_assessment(p, rebuilt, "art_dns")
    assert not summary.covered_questions and summary.coverage_expires_at is None
    reserved = DNSObservation(
        question=question(),
        attempt=1,
        started_at=NOW + timedelta(seconds=1),
        transport_outcome="interrupted",
    )
    recovered = later.model_copy(update={"observations": (reserved,)})
    assert validate_dns_execution(p, recovered).observations[0].finished_at is None


@pytest.mark.parametrize(
    "text, expected",
    [("v=spf1 -ALL", "observed"), ("v=spf1 a:%{d=} -all", "incomplete")],
)
def test_spf_case_and_macro_delimiters_do_not_invent_syntax_failures(text, expected):
    assert check_summary("spf", "example.com", text).status == expected


def test_runner_uses_fixed_local_reference_until_collector_binds_artifact_id():
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    p = plan()
    bodies = serialize_dns_artifacts(p, execution(p, observation()), "execution.json")
    assert json.loads(bodies["summary.json"])["evidenceRef"] == "execution.json"
    for invalid in ("../execution.json", "https://outside.example/result", "/tmp/raw"):
        with pytest.raises(ValueError):
            serialize_dns_artifacts(p, execution(p, observation()), invalid)


def test_mail_policy_answers_do_not_authorize_automatic_mx_address_expansion():
    from moonmind.security.dns_assessments import followup_questions

    q = question("example.com", "TXT")
    address = question("mail.example.com")
    p = plan(assessment(questions=[q], allowed_followups=(address,)))
    row = observation(
        q,
        parsed(
            q,
            [
                (q.name, 120, "IN", "TXT", '"v=spf1 mx -all"'),
                (q.name, 120, "IN", "MX", "10 mail.example.com"),
            ],
        ),
    )
    assert followup_questions(p, row) == ()


@pytest.mark.parametrize("kind", ["spf", "caa"])
def test_large_semantic_details_preserve_bounded_artifacts_and_explicit_gap(kind):
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    q = question("example.com", "TXT" if kind == "spf" else "CAA")
    p = plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check=kind, subject=q.name),)
        )
    )
    if kind == "spf":
        original = "v=spf1 " + " ".join(["ip4:192.0.2.1"] * 65) + " -all"
        value = " ".join(
            json.dumps(original[i : i + 200]) for i in range(0, len(original), 200)
        )
    else:
        original = "a" * 4096
        value = "0 issue " + json.dumps(original)
    wire = response(q, [(q.name, 120, "IN", q.record_type, value)]).to_wire(
        max_size=65535
    )
    row = observation(q, parse_dns_response(q, wire, http_age_seconds=0))
    bodies = serialize_dns_artifacts(p, execution(p, row), "art_dns")
    summary = json.loads(bodies["summary.json"])
    check = summary["checks"][0]
    assert check["status"] == "incomplete"
    assert check["code"] == kind + "_semantic_details_limited"
    assert len(check["details"]) <= 64 and all(
        len(detail) <= 4096 for detail in check["details"]
    )
    assert any("omitted" in detail for detail in check["details"])
    assert summary["uncoveredQuestions"] == [
        {"name": q.name, "recordType": q.record_type}
    ]
    assert original in row.response.records[0].values


@pytest.mark.parametrize(
    "ttl,age,state", [(0, 0, "incomplete"), (10, 0, "incomplete"), (30, 10, "observed")]
)
def test_cname_conclusion_accounts_for_each_supporting_record_freshness(
    ttl, age, state
):
    q = question("example.com", "CNAME")
    p = plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check="cname", subject=q.name),)
        )
    )
    row = observation(
        q,
        parsed(
            q,
            [
                (q.name, 120, "IN", "CNAME", "alias.example"),
                ("alias.example", ttl, "IN", "CNAME", q.name),
            ],
            age=age,
        ),
    )
    summary = summarize_dns_assessment(p, execution(p, row), "art_dns")
    assert summary.checks[0].status == state
    if state == "incomplete":
        assert not summary.covered_questions and summary.coverage_expires_at is None
    else:
        assert summary.checks[0].code == "cname_loop_observed"
        assert summary.coverage_expires_at == row.finished_at + timedelta(
            seconds=ttl - age
        )


def test_fresh_explicit_cname_followup_replaces_expired_embedded_support():
    q, alias = question("example.com", "CNAME"), question("alias.example", "CNAME")
    p = plan(
        assessment(
            questions=[q],
            allowed_followups=(alias,),
            checks=(DNSCheckSelection(check="cname", subject=q.name),),
        )
    )
    first = observation(
        q,
        parsed(
            q,
            [
                (q.name, 120, "IN", "CNAME", alias.name),
                (alias.name, 0, "IN", "CNAME", q.name),
            ],
        ),
    )
    second = observation(
        alias,
        parsed(alias, [(alias.name, 90, "IN", "CNAME", q.name)]),
        attempt=2,
        depth=1,
        parent_attempt=1,
    )
    summary = summarize_dns_assessment(p, execution(p, first, second), "art_dns")
    assert summary.checks[0].code == "cname_loop_observed"
    assert summary.checks[0].observation_attempts == (1, 2)
    assert set(summary.covered_questions) == {q, alias}
    assert summary.coverage_expires_at == second.finished_at + timedelta(seconds=90)


@pytest.mark.parametrize("aliased", [False, True])
def test_delegation_attributes_only_exact_or_validated_alias_terminal_addresses(
    aliased,
):
    ns, soa, addr = (
        question("example.com", "NS"),
        question("example.com", "SOA"),
        question("ns.example.com"),
    )
    p = plan(
        assessment(
            questions=[ns, soa],
            allowed_followups=(addr,),
            checks=(DNSCheckSelection(check="delegation", subject="example.com"),),
        )
    )
    first = observation(ns, parsed(ns, [(ns.name, 120, "IN", "NS", addr.name)]))
    second = observation(
        soa,
        parsed(
            soa,
            [
                (
                    soa.name,
                    120,
                    "IN",
                    "SOA",
                    "ns.example.com h.example.com 1 2 3 4 120",
                ),
                (soa.name, 0, "IN", "NS", "trap.example"),
            ],
        ),
        attempt=2,
    )
    owner = "address.example" if aliased else addr.name
    answers = [
        (owner, 120, "IN", "A", "192.0.2.1"),
        ("unrelated.example", 120, "IN", "A", "203.0.113.77"),
    ]
    if aliased:
        answers.insert(0, (addr.name, 120, "IN", "CNAME", owner))
    third = observation(
        addr, parsed(addr, answers), attempt=3, depth=1, parent_attempt=1
    )
    check = summarize_dns_assessment(
        p, execution(p, first, second, third), "art_dns"
    ).checks[0]
    assert "A:192.0.2.1" in check.details
    assert "A:203.0.113.77" not in check.details
    assert "NS:trap.example" not in check.details
    assert check.observation_attempts == (1, 2, 3)


@pytest.mark.parametrize(
    "text,should_follow",
    [
        ("unrelated verification TXT", True),
        ("v=DMARC10; p=none", True),
        ("v=DMARC1; p=reject", False),
        ("v=DMARC1; p=invalid", False),
    ],
)
def test_dmarc_fallback_scheduling_matches_exact_policy_detection(text, should_follow):
    from moonmind.security.dns_assessments import followup_questions

    q, fallback = (
        question("_dmarc.a.example.com", "TXT"),
        question("_dmarc.example.com", "TXT"),
    )
    check = DNSCheckSelection(
        check="dmarc", subject="a.example.com", organizational_domain="example.com"
    )
    p = plan(assessment(questions=[q], allowed_followups=(fallback,), checks=(check,)))
    first = observation(q, parsed(q, [(q.name, 120, "IN", "TXT", json.dumps(text))]))
    assert followup_questions(p, first) == ((fallback,) if should_follow else ())
    limited = plan(
        assessment(
            questions=[q],
            allowed_followups=(fallback,),
            checks=(check,),
            max_followup_depth=0,
        )
    )
    assert followup_questions(limited, first) == ()
    without_fallback = plan(
        assessment(
            questions=[q],
            checks=(DNSCheckSelection(check="dmarc", subject="a.example.com"),),
        )
    )
    assert followup_questions(without_fallback, first) == ()
    if should_follow:
        second = observation(
            fallback,
            parsed(
                fallback, [(fallback.name, 120, "IN", "TXT", '"v=DMARC1; p=reject"')]
            ),
            attempt=2,
            depth=1,
            parent_attempt=1,
        )
        result = summarize_dns_assessment(
            p, execution(p, first, second), "art_dns"
        ).checks[0]
        assert result.code == "dmarc_supplied_fallback_policy"
        assert result.observation_attempts == (1, 2)


@pytest.mark.parametrize(
    "kind,name,valid,other,state",
    [
        ("spf", "example.com", "v=spf1 -all", "v=spf10 unrelated", "observed"),
        (
            "dmarc",
            "_dmarc.example.com",
            "v=DMARC1; p=reject",
            "v=DMARC10; p=none",
            "observed",
        ),
        ("dkim", "s1._domainkey.example.com", "v=DKIM1; p=", "v=DKIM10; p=", "revoked"),
        ("dkim", "s1._domainkey.example.com", "p=", "v=DKIM10; p=", "revoked"),
    ],
)
def test_mail_policy_selection_does_not_conflate_version_prefixes(
    kind, name, valid, other, state
):
    q = question(name, "TXT")
    selection = DNSCheckSelection(
        check=kind,
        subject="example.com",
        **({"selectors": ("s1",)} if kind == "dkim" else {}),
    )
    p = plan(assessment(questions=[q], checks=(selection,)))
    row = observation(
        q, parsed(q, [(q.name, 120, "IN", "TXT", json.dumps(valid), json.dumps(other))])
    )
    result = summarize_dns_assessment(p, execution(p, row), "art_dns").checks[0]
    assert result.status == state


def test_bounded_details_keep_unsupported_critical_caa_semantics():
    from moonmind.security.dns_assessments import serialize_dns_artifacts

    q = question("example.com", "CAA")
    p = plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check="caa", subject=q.name),)
        )
    )
    wire = response(
        q, [(q.name, 120, "IN", "CAA", '128 custom "' + "a" * 4096 + '"')]
    ).to_wire(max_size=65535)
    row = observation(q, parse_dns_response(q, wire, http_age_seconds=0))
    body = json.loads(
        serialize_dns_artifacts(p, execution(p, row), "art_dns")["summary.json"]
    )
    assert body["checks"][0]["status"] == "unsupported"
    assert "original_result:caa_unknown_critical_tag" in body["checks"][0]["details"]
    assert body["coveredQuestions"] == []


@pytest.mark.parametrize(
    "kind,valid,foreign,state",
    [
        ("dkim", "v=DKIM1; p=", " v=DKIM10; p=", "revoked"),
        ("dkim", " v=DKIM1; p=", "   v=DKIM10; p=", "revoked"),
        ("dkim", "p=", "; v=DKIM10; p=", "malformed"),
        ("dkim", "v=DKIM1; p=", " v=DKIM10; future syntax", "revoked"),
        ("dmarc", " v=DMARC1; p=reject", " v=DMARC10; p=none", "observed"),
    ],
)
def test_mail_version_selection_uses_the_same_first_tag_grammar(
    kind, valid, foreign, state
):
    name = "s1._domainkey.example.com" if kind == "dkim" else "_dmarc.example.com"
    q = question(name, "TXT")
    selection = DNSCheckSelection(
        check=kind,
        subject="example.com",
        **({"selectors": ("s1",)} if kind == "dkim" else {}),
    )
    p = plan(assessment(questions=[q], checks=(selection,)))
    row = observation(
        q, parsed(q, [(name, 120, "IN", "TXT", json.dumps(valid), json.dumps(foreign))])
    )
    assert (
        summarize_dns_assessment(p, execution(p, row), "art_dns").checks[0].status
        == state
    )


def test_dmarc_first_tag_whitespace_does_not_schedule_inapplicable_fallback():
    from moonmind.security.dns_assessments import followup_questions

    q, fallback = (
        question("_dmarc.a.example.com", "TXT"),
        question("_dmarc.example.com", "TXT"),
    )
    p = plan(
        assessment(
            questions=[q],
            allowed_followups=(fallback,),
            checks=(
                DNSCheckSelection(
                    check="dmarc",
                    subject="a.example.com",
                    organizational_domain="example.com",
                ),
            ),
        )
    )
    row = observation(
        q, parsed(q, [(q.name, 120, "IN", "TXT", '" v=DMARC1; p=reject"')])
    )
    assert followup_questions(p, row) == ()


@pytest.mark.parametrize("unknown", ["", "v = DKIM10; p = "])
def test_dkim_tag_whitespace_preserves_version_selection(unknown):
    q = question("s1._domainkey.example.com", "TXT")
    p = plan(
        assessment(
            questions=[q],
            checks=(
                DNSCheckSelection(
                    check="dkim", subject="example.com", selectors=("s1",)
                ),
            ),
        )
    )
    values = [json.dumps("v = DKIM1; p = ")]
    if unknown:
        values.append(json.dumps(unknown))
    row = observation(q, parsed(q, [(q.name, 120, "IN", "TXT", *values)]))
    check = summarize_dns_assessment(p, execution(p, row), "art_test").checks[0]
    assert check.status == "revoked"
    assert check.code == "dkim_key_revoked"


@pytest.mark.parametrize("text", ["v=DKIM1;;p=", ";v=DKIM1;p="])
def test_dkim_empty_interior_tag_is_malformed(text):
    q = question("s1._domainkey.example.com", "TXT")
    p = plan(
        assessment(
            questions=[q],
            checks=(
                DNSCheckSelection(
                    check="dkim", subject="example.com", selectors=("s1",)
                ),
            ),
        )
    )
    row = observation(q, parsed(q, [(q.name, 120, "IN", "TXT", json.dumps(text))]))
    check = summarize_dns_assessment(p, execution(p, row), "art_test").checks[0]
    assert check.status == "malformed"
