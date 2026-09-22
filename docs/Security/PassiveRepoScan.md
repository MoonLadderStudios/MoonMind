# Passive Repository Scan

**Document Class:** Working document (owning tool/report note)
**Viewpoint:** Security tooling
**Implementation tracking:** MoonLadderStudios/MoonMind#3970
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
