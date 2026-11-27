# Network configuration assessment

MoonMind assesses a network offline from what the operator can export: device
and firewall configurations, cloud network exports, and topology or
segmentation descriptions. It needs no new network, credential or target
authority. It does not log in to devices, collect over SNMP or NETCONF,
discover hosts or scan beyond the existing
[bounded service inventory](RestrictedEgress.md#bounded-service-inventory);
those need their own admission design.

## Exports and provenance

Upload each export as an artifact and add it to the saved environment's
artifact references. A [security plan](SecurityPlans.md#planning-from-create)
with subject type `network` names each export under Network exports in Create:
its `artifactRef`, `fileName`, `device`, `exportedAt` (with a time zone),
`supplier` and `kind` (`device_config`, `cloud_network` or `topology`).

An export is what the operator supplied at a point in time; MoonMind did not
observe the running device. Every result carries the export time, device and
supplier, and the planner copies them from Create, never from repository text,
so a plan whose analyzer job states different provenance is rejected. Evidence
is observed as of the export time and stays fresh for `maxExportAgeDays`
(default 90); after that the evidence freshness rules treat it as stale, and a
requirement evaluated at a single point needs an export from that day.

## Sensitive evidence

Configurations hold password hashes, community strings, pre-shared keys and
private addressing. The shared redaction owner recognizes these constructs for
Cisco IOS, IOS XE, NX-OS, ASA, Arista EOS, Junos, FortiOS, PAN-OS XML and cloud
network JSON, so logs, reports, plans and model-delivered evidence mask them
everywhere. An export is sanitized line by line: each secret value becomes
`MOONMIND-REDACTED`, a plain token the analyzer still parses, and every line
keeps its number, so locators into the sanitized copy match the original.
Unredacted bytes stay in the operator's artifact. The analyzer job mounts it
read-only and nothing parses or publishes it. Published evidence is restricted
and the summary records sensitivity `confidential`; private addressing is kept
because the rules need it. Constructs the patterns do not recognize stay in the
sanitized copy, so treat it as confidential.

The environment's evidence-processing policy still decides agent review: with
`disabled` or `local_only` model delivery the review does not run and has no
fallback, while the deterministic checks do.

## The analyzer job

The `network-config` job (`networkConfigAssessment`) runs
[Batfish](https://github.com/batfish/batfish) (Apache-2.0), from the
`network-config` image source (Compose default `batfish/allinone:latest`,
override `MOONMIND_NETWORK_CONFIG_IMAGE`), with `networkMode: none`, no
environment, workspace, egress or credential. The worker sanitizes the captured
exports into the job's own output directory with
`moonmind/security/network_config_runner.py`, which starts the image's Batfish
service on loopback and asks fixed questions. Collection sanitizes the captured
exports again instead of trusting the writable directory, redacts the answers,
and generates the `network-config/v1` summary itself; `security.assess`
recomputes that summary from the retained sanitized exports and answers before
using it.

Batfish was adopted under the [conditional-tool](SecurityTools.md#conditional-additions)
discipline. The measured gap: no installed tool parses device configurations
(Trivy, Opengrep and Gitleaks cover source, images and infrastructure as code).
The weakened and corrected Cisco IOS fixtures in
`tests/fixtures/security/network_config` differ on every check below, and the
recorded answers there come from the real image. The
[upstream image workflow](../../.github/workflows/upstream-assessment-images.yml)
reruns the image over those fixtures.

| Check | What the analyzer decides |
| --- | --- |
| `acl-line-unreachable` | A filter line that earlier lines already fully match |
| `cleartext-protocol-permitted` | An inbound interface filter that permits Telnet, FTP or TFTP |
| `ntp-not-configured` | A device with no NTP server |
| `remote-logging-not-configured` | A device with no remote logging host |
| `undefined-reference` | A reference to a structure the configuration never defines |

Each file records its vendor, operating system, version, parse status and
coverage. Unsupported vendors, unparsed lines, conversion issues (parsed
content Batfish did not model), formats or versions MoonMind has not qualified
(only Cisco IOS 12 to 17 so far), undecodable or oversized files, and failed
questions are named coverage gaps that leave results partial, never clean. An
oversized export is not captured, and the other exports are still analyzed.
Cloud network exports and topology descriptions are kept, sanitized, for
review; no qualified analyzer reads them yet. Optional `flows` (an IP address,
port and protocol) ask the exported inbound filters whether they permit traffic
to a destination; they send none.

## Agent review

The `security-review` Skill's [network procedure](../../.agents/skills/security-review/references/network.md)
covers what tools cannot decide: segmentation intent, any-to-any and shadowed
rules, management-plane exposure, logging and time sources, weak protocols and
ciphers, and signs of default credentials. Each result is a `security-review/v1`
interpretation whose locator is `<file>:<line>` in the sanitized export and
whose evidence cites that export; `security.assess` refuses a review whose
export interpretations lack them. Interpretations never become findings and
never satisfy a requirement that needs a check or an organisational record.

## Live evidence

A retained service inventory of the same network is compared with the
exported rules for the plan's flows. A port the inbound filters deny but the
inventory found open is a contradiction: the report shows it, and the export's
checks become partial coverage so the affected requirements are inconclusive.
Agreement is reported as corroboration. TLS, DNS and inventory jobs stay
separately admitted under existing authority.

## Standards

Packs tag requirements with `subjectTypes`, and a network plan selects those
tagged `network`; every other requirement is not applicable to it. PCI DSS
Requirement 1, ISO/IEC 27001 Annex A 8.20 to 8.22 and GLI secured-network and
remote-access controls are tagged. General best practices for networks
(`network-best-practices`) is a declared checklist of MoonMind's original
statements, not benchmark text. NET-1 to NET-5 are the analyzer checks above,
NET-6 to NET-10 need agent review, and NET-11 needs the operator's approved
configuration standard.

A network plan run launches its `network-config` job as a deterministic
`container.run_job` step, so it runs even when model delivery is disabled.
The conclusion reads the summary the job links to the run. Requirements of a
standard without profile inputs then conclude under the reviewed plan's
applicability; readiness standards keep their profile rules.

## Qualification

Analyzer coverage is qualified on fixtures only. Qualification on real device
exports is Unverified: no real exports are available to MoonMind's tests. The
operator can close it by uploading real exports, running a network plan, and
checking each file's identification, parse coverage and gaps against the device.
