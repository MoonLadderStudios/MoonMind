# Passive Repository Scan

**Document Class:** Working document (owning tool/report note)
**Viewpoint:** Security tooling
**Status:** Implemented
**Owners:** MoonMind Engineering
**Last Updated:** 2026-09-22

> [!NOTE]
> This is the small owning documentation for one bounded product extension:
> a single passive, read-only secret-exposure scan over an authorized
> snapshot, run through the existing Container Job and artifact path. It is
> not a security platform, scanner runtime, or replacement for core
> reliability work.

## Scanner selection

One maintained scanner for one declared input class with a useful
secret-exposure result:

- **Scanner:** the in-repo outbound-scan regex contract
  (`moonmind.security.outbound_scan.scan_outbound_text`, forced
  `high_security_mode=True` per file), as wired by
  `moonmind/security/passive_repo_scan.py`.
- **Official interface:** `scan_outbound_text(text, location, high_security_mode=True)`;
  findings are category/location/redacted-preview triples.
- **Licensing:** in-repo MoonMind code; no new dependency.
- **Image provenance:** not applicable -- the scan runs in-process; no scanner
  image is pulled, installed into MoonMind's main image, or executed from a
  collection.
- **Configuration:** `PassiveScanConfig` bounds (`max_files`,
  `max_bytes_per_file`, `max_total_bytes`), recorded in result metadata.
- **Output behavior:** native machine-readable JSON plus a small Markdown
  summary; raw matches never leave the scan boundary.

No ToolPack database/schema family, no scanner collection, no universal
findings model, no feed service, and no assessment-target network access.

## Execution

- **Supported journey:** `moonmind container passive-scan` (backed by
  `passive_scan_submission()` / `run_passive_scan_job()` in
  `moonmind/container_job_cli.py`) submits the scan workload through the
  existing `container_job_submission` path -- workspace and correlation
  identity are stamped from the admitted managed session, so the authorized
  operator never hand-authors a JSON workload. The command waits durably and
  prints the terminal job state plus the collected logs and artifacts
  references for the retained native report and summary.
- **Input:** an immutable authorized snapshot directory (declared input class:
  UTF-8 text files). The default snapshot is the mounted workspace root
  (`.` with `workdir: /workspace`): the container-job path mounts the
  authorized repository directly at `/workspace`. A snapshot path that is
  itself a symlink is rejected before resolution, so container files outside
  the authorized workspace are never inspected. Symlinks escaping the
  snapshot, binary content, undecodable files, special files, and bound
  overruns are recorded as skipped and force an `incomplete` verdict --
  never clean. Repository-control metadata (`.git`) is excluded from
  working-tree coverage without forcing `incomplete`.
- **Job:** `build_scan_job_workload()` declares the production workload:
  existing `moonmind-python-tests` image source, `networkMode: none`,
  bounded CPU/memory/PIDs/timeout, and two declared outputs
  (`artifacts/passive-scan-report.json`,
  `artifacts/passive-scan-summary.md`). The workspace mount stays writable
  so the declared outputs are collectable; the snapshot itself is read-only
  by construction (the scan performs no writes to snapshot paths -- only the
  two declared outputs are written). Workspace and correlation identity are
  stamped by the existing `container_job_submission` path; the entrypoint is
  `python -m moonmind.security.passive_repo_scan`. File enumeration is
  incremental and stops as soon as the `max_files` bound is exceeded.
- **Feed:** none. The offline regex mode needs no external feed; result
  metadata records `feed: {name: none}` plus the tool ref, input digest, and
  configuration identity (never a compatibility fingerprint).

## Results

- **Verdicts:** `finding_present` | `clean_with_coverage` | `incomplete`.
  Zero findings cover only the listed scanned files; they never mean the whole
  repository is secure. Malformed, truncated, timed-out, cancelled, stale, or
  skipped data always resolves to `incomplete` (`parse_scan_report_json`
  fails closed). A `clean_with_coverage` verdict additionally requires the
  complete evidence schema (tool identity, 64-hex input digest computed over
  canonical untruncated paths, non-empty coverage with matching counts,
  recorded configuration and feed, and a non-empty summary); a bare
  `{"verdict": "clean_with_coverage"}` payload is `incomplete`. Retained
  configuration, feed, and cancellation fields round-trip instead of being
  replaced by defaults.
- **Detection:** the maintained outbound-scan contract (bare credential keys)
  plus a bounded repository supplement for prefixed/suffixed credential
  assignments (`DATABASE_PASSWORD`, `GITHUB_TOKEN`,
  `AWS_SECRET_ACCESS_KEY`). Supplement findings carry the key name plus a
  redaction marker, never raw values.
- **Sensitivity:** source and findings are untrusted and potentially
  secret-bearing. Locations and previews are redacted via
  `redact_sensitive_text`, path-validated, and bounded. Reports carry no raw
  secret matches and invent no verification.
- **Retention:** `retain_scan_report()` stores the native JSON and the summary
  through the existing artifact store, so the result stays readable after job
  cleanup. Source fixes or publication are separate authorized work, never a
  scan side effect.

## Limitations

Deterministic regex shapes only; no dependency/inventory analysis, no
network-active assessment, no live-target qualification, no production scans
or external uploads under this issue. Missing dependencies produce a clear
`incomplete`/unsupported result rather than widened authority.

## Recurring assessments and retained comparisons

`security.assess` is a native tool for existing manual and recurring workflows.
It compares retained passive reports, or reassesses an inventory against an
explicit advisory feed without a source-code change. Temporal still owns
cadence, overlap, catch-up and cancellation. The tool does not launch an agent
or require model triage.

Create a schedule with `POST /api/recurring-workflows`, using the normal
`MoonMind.UserWorkflow` target and a step with `type: "tool"`:

```json
{
  "id": "assess",
  "type": "tool",
  "tool": {
    "name": "security.assess",
    "inputs": {
      "scope": {
        "assets": ["app"],
        "exclusions": [],
        "repositoryExceptions": []
      },
      "tools": ["inventory-feed"],
      "budget": {
        "baseSeconds": 60,
        "maxSeconds": 120,
        "progressStallSeconds": 60
      },
      "credentialRefs": [],
      "networkRefs": [],
      "inventoryRef": "art_inventory",
      "feedRef": "art_feed",
      "baselineRef": "art_previous_comparison"
    }
  }
}
```

The example refs stand for completed, authorized artifacts. Declare inputs in
the workflow's `inputAttachments` as `{"artifactId": "art_..."}` so the existing
occurrence admission links them to that execution. Each invocation checks
current artifact read authority before reusing scanner work. A saved schedule
confers no credential or target-network authority. These offline adapters
require empty credential/network reference lists; active adapters must use
their existing admission owners. Agent-backed steps and PR finishing, including
`review_only`, retain execution-plan admission; an assessment adds no image or
source-revision pin.

Use the existing schedule policy (`overlap.mode`, `catchup.mode`) and manual
`POST /api/recurring-workflows/{id}/run` with an `Idempotency-Key`. Supply a newer
`feedRef` through normal workflow/schedule authoring when intelligence changes,
and the last `comparisonRef` as the next `baselineRef`. There is no feed poller
or second scheduler. Identical admitted inputs reuse a confirmed scanner save;
a later occurrence can also reuse the supplied baseline's scanner observation.
Scanner results are saved before report publication, so publication retries
do not repeat completed work. Cancellation propagates without a successor.

### Evidence contracts

The versioned JSON contracts live in `moonmind/security/assessments.py`:

- `security-inventory/v1`: scope, observation/expiry timestamps, configuration
  reference, coverage status and components (`asset`, unversioned `purl`,
  `version`). Producer-asserted completeness covers the declared scope,
  including assets with no components.
- `security-feed/v1`: source, revision, observation/expiry timestamps and native
  rules (`id`, `purl`, explicit `affectedVersions`). The bounded adapter matches
  exact versions; it does not guess version ranges or fetch intelligence.
- `security-observation/v1`: native source/tool/rule/asset/instance identity,
  evidence refs, coverage and configuration/feed provenance. The
  `retained-report` tool consumes these observations through `reportRefs`; it
  also accepts this document's passive scanner JSON, using snapshot-relative
  file paths as scope assets. Native passive evidence expires for comparison
  after one day; its original artifact retention is unchanged.
- `security-comparison/v1`: the observation, retained findings, scoped triage,
  counts and baseline/scan artifact references. Identity never depends on a
  presentation title or severity.

Comparisons distinguish `new`, `still_observed`, `absent_comparable` and
`not_reassessed`. Absence requires fresh, completed coverage of the original
asset/rule under comparable scope, exclusions and configuration. Partial scans,
failed authentication, unsupported assets, expired evidence and changed scope
retain prior findings and evidence. Large evidence unions remain reachable
through retained input reports. An unavailable or malformed baseline, including
missing stored bytes, is explicitly unavailable; findings then use
`observed_without_baseline` and cannot claim to be new or resolved. Malformed
current reports yield incomplete coverage while preserving earlier evidence.

An optional `decisionsRef` contains a JSON array of triage decisions. Each
decision names `findingId`, `disposition`, `reason`, `evidenceRefs`, `author`,
`source`, `scope`, `reviewAt` and `reviewConditions`, with optional `expiresAt`.
Dispositions are `accepted_risk`, `false_positive`, `confirmed_vulnerability`
and `fix_retest`; only the latter requires `retestOutcome` (`passed`, `failed`
or `inconclusive`). Due or expired decisions re-enter review. Scope/provenance
changes require review too; that status persists until a renewed decision for
the finding replaces it. Replaying the same decision cannot clear review.
Earlier decisions remain in baseline artifacts. Decisions annotate observations
and cannot assert a scanner fix. Repository exceptions remain explicit scope
configuration.

The existing report links expose a readable delta/coverage/review summary,
structured comparison and saved scanner evidence. `scanOutcome` and
`publicationOutcome` remain separate: a publication failure returns the
durable refs and diagnostics without changing the scanner outcome. Live
cloud/model qualification is separate from these controlled artifact journeys.
