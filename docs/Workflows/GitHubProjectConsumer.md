# GitHub Project Implement

Select **GitHub Project Implement** and the workflow repository to consume one
real GitHub issue per run. Leave **GitHub project URL** blank to discover an open
project linked to that repository. If several are linked, a unique project whose
name matches the repository wins. With no open linked project, discovery looks
for a unique same-name project under the repository's organization or user owner.
Names match without regard to case or surrounding whitespace. Missing or
ambiguous matches require an explicit URL. Discovery reads complete bounded lists
of up to 500 projects; incomplete reads never choose an arbitrary match.

An explicit organization or personal project URL overrides discovery, including
an optional `/views/N` scope. Automatically discovered projects use project-wide
scope. Once an issue is reserved, its resolved project/item identity stays pinned
across retries. Runtime, repository, verification,
and publication use the existing workflow controls. There is no preview UI.

## Selection

Leave **Pull columns in priority order** blank to use **Ready**, matched without
regard to case. If Ready is absent, exactly one **Selected for Development** or
**Ready for Development** option qualifies. Missing or ambiguous defaults require
an explicit selection; Backlog is never an automatic fallback. To consume several
columns, enter their exact names on separate lines, highest priority first.

The column field defaults to **Status** and can be changed to another
single-select field. Within each column, oldest issues run first. Optional
priority ordering uses a numeric field (lowest first) or the option order of a
single-select field; missing priorities follow populated ones, then age breaks
ties. Project membership admits all authors by default; turn off **Include issues
created by other users** to restrict work to the authenticated GitHub account.

Only project items whose native content type is `Issue` qualify. `DraftIssue`
items, PRs, archived or unreadable cards, closed issues, and issues from other
repositories are excluded. Drafts are never converted automatically. Existing
dependency, claim, retry, and recovery checks govern every candidate. Blocked
items are skipped before lower-priority eligible work is considered. The selected
item's identity and column are reread before reservation.

A view URL applies its saved filter. Supported qualifiers are `is`, `state`,
`repo`, `status`, and `label`, including negation and comma-separated values.
Other filter syntax stops selection before mutation. Selecting the project URL
without `/views/N` explicitly uses project-wide scope. The declared preset sort
controls consumption order; a view's visual sort does not override it.

Discovery requests open issues in the selected repository and source columns
where the API supports that filter, then validates native types and scope locally.
At most 500 matching project items and 100 project fields are inspected. Incomplete
pagination, provider errors, or exhausted scan limits report an error rather than
an empty queue or a lower-priority selection. Selection evidence records columns,
ordering, and exclusion counts. Issue details are fetched only as needed.

## Board phases

The preset resolves phase destinations to existing option IDs:

| Lifecycle | Default column names |
| --- | --- |
| In progress | In Progress |
| Verified PR handoff | In Review, Code Review, or Review |
| Verified completion | Done, Completed, or Complete |
| Safe continuation | Recovery Needed, when present |
| Hold or intervention | Needs Attention, when present |

Advanced text fields override these names. Ambiguous matches require explicit
mapping. Progress, review, and completion must be mapped; several phases may share
a column on a simpler board. Exceptional phases without a destination remain
visible through issue labels and synchronization diagnostics. A safe, no-work
release returns to the original source column under the existing retry policy.

A PR-only run ends in review. Opening a PR never marks an issue done. Done requires
the existing trusted completion decision, or a later completed issue closure whose
recorded verified PR/head is confirmed merged into the intended destination. A
not-planned closure never becomes Done. Project updates cannot grant merge authority.

Claim receipts retain the selected item, field/option IDs, completion target, and
pending synchronization across restarts. Retries keep that issue and reuse its
work. Lost update acknowledgements are reread before another mutation. The existing
periodic issue maintenance retries pending updates and observes later completion;
board failures remain separate from implementation outcomes. A newer local issue
owner retires the predecessor's projection. Unexpected external column moves
suspend further writes, with diagnostics. Returning the item to its last managed
column or current lifecycle destination allows synchronization to resume.
GitHub field updates provide no atomic
ownership check, so a move concurrent with an already submitted request can race.

## Access and scheduling

The GitHub connection needs issue access and project read/write access. In
**Settings → Source control**, enable **Allow project board updates** for that
connection. This admits the `projects` operation in MoonMind; it does not change
the credential's GitHub permissions. Classic tokens require `project` scope;
organization GitHub Apps require Projects write permission. Read-only or missing
project access stops selection before an issue is claimed. Retained connection
grants are not broadened automatically.

Use the existing recurring workflow scheduler to consume continuously. Skip
overlapping occurrences for serial consumption; the next run reevaluates the
configured column order. Independent schedules and deployments still use the
shared issue claim protocol; a board move is not an exclusive lock.
