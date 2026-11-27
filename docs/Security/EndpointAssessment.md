# Endpoint assessment

MoonMind assesses a Linux endpoint's configuration with
[OpenSCAP](https://www.open-scap.org/) evaluating
[ComplianceAsCode](https://github.com/ComplianceAsCode/content) (SCAP Security
Guide) content. It never reaches the host: the typed `endpointAssessment`
Container Job evaluates a captured copy offline, or imports results the
operator produced on the device. Reaching live hosts is separate work.

## The job

`endpointAssessment` (`moonmind/schemas/repository_scanner_models.py`) runs in
the [repository-security image](SecurityTools.md#packaging-and-recommended-tools)
with `networkMode: none`, no Docker socket, no added privilege and no
credential. Admission, persistence and the worker rebuild the canonical
workload from the admission, so a request cannot add a network, mount,
environment entry or command. The contract has no remediation, fix-generation
or command input, and the runner's only OpenSCAP invocation is
`oscap xccdf eval --profile <profile> [--tailoring-file] --results-arf`.

| `target` | Input artifact | Also required |
| --- | --- | --- |
| `rootfs` | A root filesystem tar, such as `docker export` output | `datastream`, `profile` |
| `image` | A single-image Docker-save or OCI layout archive; layers and whiteouts are applied in order | `datastream`, `profile` |
| `results` | ARF or XCCDF results from `oscap` run on the endpoint | none; `profile`, when given, is checked |

Every target names `asset`, the endpoint's stable name in the assessment
scope, which findings attach to. `tailoringArtifactRef` adds an XCCDF
tailoring file to an evaluation. `ruleIds` lists rules the selected standards
need; any the profile leaves unselected or that end without pass or fail leave
the run partial.

The examined subject is identified by digest
(`rootfs@sha256:…`, `image-archive@sha256:…` or `openscap-results@sha256:…`).
An offline copy or imported result is not MoonMind's own observation of the
running device, and every summary says so.

## What an offline copy can decide

The runner unpacks the archive as the unprivileged scanner user into private
scratch and points `OSCAP_PROBE_ROOT` at it. Unpacking refuses path traversal,
links leaving the copy and writes through escaping symlinks, and skips device
files. Without privilege it cannot keep file ownership, and a copy has no
processes, kernel, services or mounts. OpenSCAP still answers those checks
offline, wrongly: a process or kernel-parameter object that "does not exist"
in the copy fails, and an absence check passes.

So before evaluating, the runner walks each rule's OVAL definitions in the
shipped datastream, through extended definitions, tests, objects, filters,
states and variables. A rule that reads live state (process, sysctl, systemd,
partition, interface and similar probes, or paths under `/proc`, `/sys`,
`/run`, `/dev`) is `runtime`; one that compares ownership, modes or labels is
`metadata`. Their pass or fail becomes not assessed with that reason. On the
Debian 12 content, service, partition and kernel-parameter rules are
`runtime`, file permission and owner rules are `metadata`, and package, SSH
and APT configuration rules are evaluated. Results imported from the device
are not narrowed.

## Results

Only an XCCDF `TestResult` rebuilt from allow-listed fields is retained: the
copied datastream, OVAL results, system characteristics (which can quote file
contents), target addresses and facts are dropped. An evaluated copy's
`target` names the scanner, so only imports keep it, as a declared value. The
worker rebuilds the result and the `endpoint-assessment/v1` summary from the
job's output; the job cannot assert its own summary, and `security.assess`
recomputes it from the retained result before use.

The summary records the benchmark and its version, profile, content file,
digest and release, tailoring, platform applicability, subject identity and
every rule's native result:

- `pass` and `fail` are evidence; `fail` is a finding on the endpoint asset.
- `notapplicable`, `notchecked`, `notselected`, `informational`, `error`,
  `unknown` and `fixed` are coverage states with reasons, never passes.
- `error` and `unknown` results, required rules not evaluated, and `fixed`
  results (a run that remediated) make the run `partial`. Rules outside the
  profile are `notselected` and outside its population.

`security.assess` reads the summary through the retained-report path as
`deterministic_check` evidence (`source: endpoint`, `tool: openscap`). Pass and
fail rules are complete coverage of their rule IDs; error, unknown and
unevaluated required rules are partial coverage of theirs; other states
cover nothing. A pack objective naming XCCDF rule IDs is therefore satisfied
only by complete, current, in-scope pass results for those rules. Benchmark,
version, profile, content digest and release, tailoring and origin are
intelligence, so a changed profile or content release is reported as an
intelligence change rather than a changed endpoint. Absence compares only
under the same profile, tailoring and origin. Imported results are observed
as of their own end time.

## Content and platforms

The image installs OpenSCAP from Debian's signed archive and the newest stable
ComplianceAsCode release, verified against its published SHA-512, with the
license, inventory and per-datastream digests recorded; the runner refuses a
datastream whose digest differs. It ships the dpkg-based distributions the
Debian-built scanner evaluates offline: `ssg-debian12-ds.xml`,
`ssg-debian13-ds.xml`, `ssg-ubuntu2204-ds.xml` and `ssg-ubuntu2404-ds.xml`.
Results from other Linux content can be imported. Windows, macOS, mobile
devices and regulated gaming devices are unsupported coverage.

ComplianceAsCode profiles are used under their own identity and BSD-3-Clause
license. A profile aligned with a benchmark such as CIS or DISA STIG is
reported as aligned with it, never as that benchmark or a certification, and
no benchmark text is retained.

## Planning an endpoint

`endpoint` is a security-plan subject. Its planning context lists the shipped
datastreams, the general profile and the required inputs, and requirements a
standard does not tag `endpoint` must be not applicable. General best
practices for endpoints (`endpoint-best-practices`) names rules of the
ComplianceAsCode `standard` profile
(`xccdf_org.ssgproject.content_profile_standard`, shipped for Debian 12, Debian
13 and Ubuntu 22.04); its account-file and kernel requirements need results
produced on the device. PCI DSS and ISO/IEC 27001 rows about system
configuration, malware, patching, authentication and logging are tagged
`endpoint`. A plan run launches each `endpoint-assessment` job through the
`security-endpoint` preset, unscoped like repository and image jobs, and the
conclusion reports per-rule evidence.

## Qualification

The repository-security image workflow builds the image and runs the admitted
runner with real OpenSCAP and the Debian 12 ANSSI minimal profile on two
fixture root filesystems (`services/repository-security/fixtures/endpoint`).
The weakened fixture fails `apt_conf_disallow_unauthenticated` and
`apt_sources_list_official`, which the hardened one passes; file owner rules
are reported as device-only; and importing the retained result yields the same
rule results. Qualification against real fleet devices is Unverified until an
operator compares results produced on a device, or an exported copy of one,
with this fixture qualification.
