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

- **Input:** an immutable authorized snapshot directory (declared input class:
  UTF-8 text files). Symlinks escaping the snapshot, binary content,
  undecodable files, and bound overruns are recorded as skipped and force an
  `incomplete` verdict -- never clean.
- **Job:** `build_scan_job_workload()` declares the production workload:
  existing `moonmind-python-tests` image source, `networkMode: none`,
  read-only workspace, bounded CPU/memory/PIDs/timeout, and two declared
  outputs (`artifacts/passive-scan-report.json`,
  `artifacts/passive-scan-summary.md`). Workspace and correlation identity are
  stamped by the existing `container_job_submission` path; the entrypoint is
  `python -m moonmind.security.passive_repo_scan`.
- **Feed:** none. The offline regex mode needs no external feed; result
  metadata records `feed: {name: none}` plus the tool ref, input digest, and
  configuration identity (never a compatibility fingerprint).

## Results

- **Verdicts:** `finding_present` | `clean_with_coverage` | `incomplete`.
  Zero findings cover only the listed scanned files; they never mean the whole
  repository is secure. Malformed, truncated, timed-out, cancelled, stale, or
  skipped data always resolves to `incomplete` (`parse_scan_report_json`
  fails closed).
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
