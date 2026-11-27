# Reviewing exported network configurations

Use this procedure for a network plan or a direct review of exported device,
firewall or cloud network configurations. It covers what the offline analyzer
cannot decide. Each judgement is an agent interpretation, never a finding.

## Inputs and limits

Review the retained `network-config/v1` summary, its sanitized exports (each
file's `sanitizedRef`), the analyzer's answers and any topology or segmentation
description the operator supplied. Do not read the raw export artifacts.
Secret values appear as `MOONMIND-REDACTED`; never try to recover, guess or
repeat a secret. Private addressing is confidential evidence: cite it only
where a judgement needs it.

If the saved environment's evidence-processing policy sets model delivery to
`disabled` or `local_only`, do not review. The requirements that only review
decides are reported as not assessed with that reason, and there is no
fallback provider.

An export is the operator's statement of a device at its export time.
MoonMind did not observe the running device. Say so in the review's
limitations, and treat a stale or partially parsed export as partial evidence.
Use admitted service inventory, TLS or DNS results only when they are already
retained; never launch a probe or widen a target to check a rule.

## Procedures

- `network-segmentation-intent`: compare the filters between zones with the
  stated segmentation intent and topology. Name each path the intent forbids
  but the rules permit, and each intended path the rules block.
- `network-permissive-rules`: judge any-to-any and other broad rules, and the
  lines the analyzer reports as unreachable, against a purpose a reviewer can
  confirm.
- `network-management-exposure`: find who can reach management services and
  lines (SSH, Telnet, HTTP management, SNMP, console and virtual terminals) and
  from which networks; note missing access classes.
- `network-logging-time`: judge logging destinations, levels and source
  interfaces, and time sources with their authentication.
- `network-weak-protocols`: find weak protocol versions and ciphers, such as
  SSH version 1, SNMP v1 or v2c, Telnet, plain HTTP management and weak hashes.
- `network-default-credentials`: look for signs of default, shared or
  reversible credentials from account names and storage types (for example a
  type 0 or 7 marker before `MOONMIND-REDACTED`), never from secret values.

## Output

Save a `security-review/v1` interpretation for each judgement:

- `subject.asset`: the export's artifact reference, which is a scope asset;
- `subject.locator`: `<fileName>:<line>` or `<fileName>:<start>-<end>`, lines
  of the sanitized export, which keep the original numbering;
- `subject.resource`: the device and the filter, line or service judged;
- `evidenceRefs`: the export's sanitized reference, plus the analyzer answers
  when a judgement uses them.

`security.assess` refuses a review whose export interpretations lack a file and
line locator inside the export or the sanitized reference. Interpretations stay
distinct from the analyzer's deterministic findings: they never satisfy a
requirement that needs a check (such as unreachable filter lines or time
sources) or an organisational record (such as an approved configuration
standard).
