/**
 * Compact saved-result projection for MoonLadderStudios/MoonMind#4020.
 *
 * Uses only the server's actual selected run/attempt/result and committed
 * artifact references. No second result store, no frontend-invented status,
 * no compute restart. Download reuses the existing authorized artifact
 * download endpoint; Continue reuses the single continuation owner
 * (`continueInNewWorkflow`, fresh admission through
 * `POST /executions/{workflowId}/continue`); Publish Saved Work reuses the
 * publication-only `POST /executions/{workflowId}/retry-publication` path with
 * the saved-work body (`savedWorkRef` + destination), so no model is rerun.
 * Its availability is the execution's server action projection
 * (`canPublishSavedWork` and its disabled reason), and an accepted publication
 * is followed through the existing execution detail read of the returned
 * operation.
 *
 * A committed saved-work manifest is presented as one saved unit with the
 * snapshot, delta, history, and file-manifest parts it names. Its format
 * claims, exclusions, limitations, and retention come from the server's
 * compact `saved_work_summary` listing metadata, never from parsing raw
 * manifest bytes in the browser.
 *
 * Preview access never authorizes raw restore/publication: an ArtifactRef is
 * an identifier, not a URL or credential. Raw bytes require
 * `raw_access_allowed === true`; otherwise only metadata-first preview via
 * `default_read_ref` (or the artifact metadata document) is exposed.
 */

export interface SavedResultLinkLike {
  linkType?: string | undefined;
  link_type?: string | undefined;
  label?: string | null | undefined;
  [key: string]: unknown;
}

export interface SavedResultArtifactLike {
  artifactId: string;
  contentType?: string | null | undefined;
  content_type?: string | null | undefined;
  sizeBytes?: number | null | undefined;
  size_bytes?: number | null | undefined;
  status?: string | null | undefined;
  sha256?: string | null | undefined;
  digest?: string | null | undefined;
  contentDigest?: string | null | undefined;
  content_digest?: string | null | undefined;
  downloadUrl?: string | null | undefined;
  download_url?: string | null | undefined;
  defaultReadRef?: { artifactId?: string } | null | undefined;
  default_read_ref?: { artifactId?: string; artifact_id?: string } | null | undefined;
  rawAccessAllowed?: boolean | null | undefined;
  raw_access_allowed?: boolean | null | undefined;
  metadata?: Record<string, unknown> | null | undefined;
  links?: SavedResultLinkLike[] | null | undefined;
  [key: string]: unknown;
}

export interface SavedResultExecutionLike {
  workflowId?: string | null | undefined;
  workflow_id?: string | null | undefined;
  runId?: string | null | undefined;
  run_id?: string | null | undefined;
  temporalRunId?: string | null | undefined;
  temporal_run_id?: string | null | undefined;
  state?: string | null | undefined;
  rawState?: string | null | undefined;
  status?: string | null | undefined;
  closeStatus?: string | null | undefined;
  close_status?: string | null | undefined;
  temporalStatus?: string | null | undefined;
  temporal_status?: string | null | undefined;
  actions?: unknown;
  finishSummary?: unknown;
  finish_summary?: unknown;
  outputBranch?: unknown;
  output_branch?: unknown;
  [key: string]: unknown;
}

export type SavedResultKind = 'report' | 'repository' | 'non-git';
export type SavedResultState = 'ready' | 'pending' | 'unavailable' | 'stale';

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function digestValue(value: unknown): string {
  // The `absent` sentinel is never a real content identity, wherever it is
  // stored.
  const normalized = text(value);
  return normalized.toLowerCase() === 'absent' ? '' : normalized;
}

function artifactDigest(artifact: SavedResultArtifactLike): string {
  const direct = [
    (artifact as Record<string, unknown>).sha256,
    (artifact as Record<string, unknown>).digest,
    (artifact as Record<string, unknown>).contentDigest,
    (artifact as Record<string, unknown>).content_digest,
    artifact.sha256,
    artifact.digest,
    artifact.contentDigest,
    artifact.content_digest,
  ];
  for (const candidate of direct) {
    const normalized = digestValue(candidate);
    if (normalized) {
      return normalized;
    }
  }
  const metadata = (artifact.metadata ?? {}) as Record<string, unknown>;
  for (const key of ['sha256', 'digest', 'contentDigest', 'content_digest']) {
    const normalized = digestValue(metadata[key]);
    if (normalized) {
      return normalized;
    }
  }
  return '';
}

function artifactSize(artifact: SavedResultArtifactLike): number | null {
  const raw =
    artifact.sizeBytes ??
    artifact.size_bytes ??
    (artifact as Record<string, unknown>).sizeBytes ??
    (artifact as Record<string, unknown>).size_bytes;
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
}

function artifactStatus(artifact: SavedResultArtifactLike): string {
  return text(artifact.status).toUpperCase();
}

function linkTypes(artifact: SavedResultArtifactLike): string[] {
  const links = Array.isArray(artifact.links) ? artifact.links : [];
  return links
    .map((link) => text(link.linkType ?? link.link_type).toLowerCase())
    .filter(Boolean);
}

const REPOSITORY_LINK_PREFIXES = [
  'repository.',
  'git.',
  'patch.',
  'diff.',
  'checkpoint.',
  'capture.',
];

// The execution artifact list also carries inputs, runtime logs, debug traces,
// and other evidence. Only artifacts linked as a result output are saved work.
function isSavedOutputArtifact(artifact: SavedResultArtifactLike): boolean {
  return linkTypes(artifact).some(
    (type) =>
      type === 'result' ||
      type.startsWith('report.') ||
      (type.startsWith('output.') && type !== 'output.logs') ||
      REPOSITORY_LINK_PREFIXES.some((prefix) => type.startsWith(prefix)),
  );
}

function contentType(artifact: SavedResultArtifactLike): string {
  return text(artifact.contentType ?? artifact.content_type).toLowerCase();
}

const SAVED_WORK_MANIFEST_CONTENT_TYPE =
  'application/vnd.moonmind.saved-work-manifest+json';

function artifactKind(artifact: SavedResultArtifactLike): string {
  return text(((artifact.metadata ?? {}) as Record<string, unknown>).artifact_kind);
}

// The content type is set by the server's capture path; it, not a link
// label or title, identifies the committed saved-work unit.
function isSavedWorkManifest(artifact: SavedResultArtifactLike): boolean {
  return contentType(artifact).startsWith(SAVED_WORK_MANIFEST_CONTENT_TYPE);
}

export function classifySavedResultArtifact(artifact: SavedResultArtifactLike): {
  kind: SavedResultKind;
  complete: boolean;
  reason: string;
} {
  const types = linkTypes(artifact);
  const content = contentType(artifact);
  let kind: SavedResultKind = 'non-git';
  if (types.some((type) => type.startsWith('report.'))) {
    kind = 'report';
  } else if (
    types.some((type) =>
      REPOSITORY_LINK_PREFIXES.some((prefix) => type.startsWith(prefix)),
    ) ||
    artifactKind(artifact).startsWith('checkpoint_') ||
    content.includes('x-diff') ||
    content.includes('x-patch')
  ) {
    kind = 'repository';
  }

  // A provider exit code, a local directory path, an absent digest, or a
  // permissive generic metadata default is never proof of a complete save.
  // Only a server COMPLETE status bound to real content identity counts.
  const status = artifactStatus(artifact);
  const digest = artifactDigest(artifact);
  const size = artifactSize(artifact);
  if (status !== 'COMPLETE') {
    return { kind, complete: false, reason: `status-${status || 'unknown'}` };
  }
  if (!digest) {
    return { kind, complete: false, reason: 'absent-digest' };
  }
  if (size === null) {
    return { kind, complete: false, reason: 'absent-size' };
  }
  return { kind, complete: true, reason: 'complete' };
}

function isExpiredArtifact(
  artifact: SavedResultArtifactLike,
  now: number,
): boolean {
  // Artifacts have no EXPIRED status: expiry is the server's `expires_at`
  // until the lifecycle sweep marks the artifact DELETED.
  if (artifactStatus(artifact) === 'DELETED') {
    return true;
  }
  const expiresAt = Date.parse(
    text(
      (artifact as Record<string, unknown>).expiresAt ??
        (artifact as Record<string, unknown>).expires_at,
    ),
  );
  return Number.isFinite(expiresAt) && expiresAt <= now;
}

export function canDownloadSavedResultRaw(
  artifact: SavedResultArtifactLike,
): boolean {
  // Strict allowlist: only an explicit server grant authorizes raw bytes.
  // Preview access, a default_read_ref, or a download URL hint never does.
  return (
    artifact.rawAccessAllowed === true || artifact.raw_access_allowed === true
  );
}

function previewArtifactId(artifact: SavedResultArtifactLike): string | null {
  const ref =
    artifact.defaultReadRef ?? artifact.default_read_ref ?? null;
  if (ref && typeof ref === 'object') {
    const id = text(
      (ref as Record<string, unknown>).artifactId ??
        (ref as Record<string, unknown>).artifact_id,
    );
    return id || null;
  }
  return null;
}

function joinApiBasePath(apiBase: string, path: string): string {
  const base = apiBase.replace(/\/+$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export function buildSavedResultDownloadHref(
  apiBase: string,
  artifact: SavedResultArtifactLike,
): string {
  return joinApiBasePath(
    apiBase,
    `/artifacts/${encodeURIComponent(artifact.artifactId)}/download`,
  );
}

export function buildSavedResultPreviewHref(
  apiBase: string,
  artifact: SavedResultArtifactLike,
): string {
  // Metadata-first preview: the server's redacted/bounded preview artifact
  // when default_read_ref is set, otherwise the artifact metadata document.
  // This never authorizes raw restore/publication and never performs a
  // credentialed fetch by itself (plain anchor navigation only).
  const previewId = previewArtifactId(artifact);
  if (previewId) {
    return joinApiBasePath(
      apiBase,
      `/artifacts/${encodeURIComponent(previewId)}/download`,
    );
  }
  return joinApiBasePath(
    apiBase,
    `/artifacts/${encodeURIComponent(artifact.artifactId)}`,
  );
}

function savedResultSelectionKey(
  workflowId: string,
  runId: string,
): string {
  return `${workflowId}|${runId}`;
}

// FNV-1a over two independent lanes: a stable, synchronous identity for an
// authored intent. Browsers on plain-HTTP LAN origins have no SubtleCrypto.
function stableDigest(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x01000193 ^ value.length;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x5bd1e995);
  }
  return [first, second]
    .map((lane) => (lane >>> 0).toString(16).padStart(8, '0'))
    .join('');
}

export function savedResultContinuationKey(
  workflowId: string,
  runId: string,
  intent: {
    instructions: string;
    title?: string | null;
    selectedSourceArtifactRefs: string[];
  },
): string {
  // The key is derived from the exact selected source run and the authored
  // intent, so a double click, a retry after a lost acknowledgment, or a
  // resubmission after reload maps to the same server reservation, while a
  // changed intent or ref set is a new request rather than a digest conflict.
  // Single-user instance/resource key: no human-user partitions.
  const digest = stableDigest(
    JSON.stringify({
      instructions: intent.instructions,
      title: intent.title || null,
      refs: [...intent.selectedSourceArtifactRefs].sort(),
    }),
  );
  return `saved-result:continue:${workflowId}:${runId}:${digest}`;
}

export type SavedWorkObjective = 'pr' | 'draft_pr' | 'branch';
const SAVED_WORK_OBJECTIVES: readonly SavedWorkObjective[] = ['pr', 'draft_pr', 'branch'];
export type SavedWorkStrategy =
  | 'baseline_delta'
  | 'additive_import'
  | 'empty_initialization';

export interface SavedWorkDestinationDraft {
  repository: string;
  objective: SavedWorkObjective;
  baseBranch: string;
  headBranch: string;
  strategy: SavedWorkStrategy;
  pullRequestTitle: string;
}

export interface SavedWorkPublicationRequest {
  savedWorkRef: string;
  sourceRunId?: string;
  destination: {
    repository: string;
    objective: SavedWorkObjective;
    baseBranch?: string;
    headBranch: string;
    strategy: SavedWorkStrategy;
  };
  pullRequestTitle?: string;
}

export interface SavedWorkPublicationResult {
  sourceWorkflowId: string;
  sourceRunId: string;
  workflowId: string;
  runId: string;
  publicationIdempotencyKey: string;
  rolloutGeneration?: string;
}

function branchSlug(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
}

export function defaultSavedWorkDestination(
  execution: SavedResultExecutionLike,
  workflowId: string,
  allowedModes: readonly SavedWorkObjective[] = SAVED_WORK_OBJECTIVES,
): SavedWorkDestinationDraft {
  // Derive the destination the execution already determines; the operator
  // edits it through the form rather than declaring it from scratch.
  const fields = execution as Record<string, unknown>;
  const publishMode = text(fields.publishMode ?? fields.publish_mode).toLowerCase();
  const objective: SavedWorkObjective = publishMode === 'branch' ? 'branch' : 'pr';
  return {
    repository: text(fields.repository),
    objective: allowedModes.includes(objective) ? objective : allowedModes[0] ?? objective,
    baseBranch: text(fields.startingBranch ?? fields.starting_branch),
    headBranch: `saved-work/${branchSlug(workflowId) || 'result'}`,
    strategy: 'baseline_delta',
    pullRequestTitle: '',
  };
}

export function buildSavedWorkPublicationRequest(
  savedWorkRef: string,
  draft: SavedWorkDestinationDraft,
): SavedWorkPublicationRequest {
  // Only the saved result and its destination are authored here. The server
  // freezes the manifest digest, destination policy, and commit identity, and
  // derives the operation identity from them.
  const destination: SavedWorkPublicationRequest['destination'] = {
    repository: draft.repository.trim(),
    objective: draft.objective,
    headBranch: draft.headBranch.trim(),
    strategy: draft.strategy,
  };
  // Initializing an empty destination has no base to apply onto.
  const baseBranch = draft.baseBranch.trim();
  if (baseBranch && draft.strategy !== 'empty_initialization') {
    destination.baseBranch = baseBranch;
  }
  const request: SavedWorkPublicationRequest = { savedWorkRef, destination };
  const title = draft.pullRequestTitle.trim();
  if (title && draft.objective !== 'branch') {
    request.pullRequestTitle = title;
  }
  return request;
}

export function savedWorkPublicationIdentity(
  request: SavedWorkPublicationRequest,
): string {
  return JSON.stringify(request);
}

export function savedResultErrorMessage(payload: unknown): string {
  // FastAPI rejections carry either a string `detail` or an object `detail`
  // with a `message`.
  if (typeof payload === 'string') {
    return payload;
  }
  if (!payload || typeof payload !== 'object') {
    return '';
  }
  const record = payload as Record<string, unknown>;
  const detail = record.detail;
  if (typeof detail === 'string') {
    return detail;
  }
  if (detail && typeof detail === 'object') {
    const message = text((detail as Record<string, unknown>).message);
    if (message) {
      return message;
    }
  }
  return text(record.message);
}

/** A server error response; timeouts and server failures may follow admission. */
export class SavedWorkPublicationError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = 'SavedWorkPublicationError';
    this.status = status;
    this.code = code;
  }
}

export async function publishSavedWork(
  apiBase: string,
  workflowId: string,
  request: SavedWorkPublicationRequest,
): Promise<SavedWorkPublicationResult> {
  // A transport failure propagates as-is: the request may have been accepted,
  // and resubmitting the same request reuses the same server operation.
  const response = await fetch(
    joinApiBasePath(
      apiBase,
      `/executions/${encodeURIComponent(workflowId)}/retry-publication`,
    ),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(request),
    },
  );
  const payload = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const detail = record(record(payload).detail);
    throw new SavedWorkPublicationError(
      savedResultErrorMessage(payload) ||
        `Publish saved work failed (${response.status})`,
      response.status,
      text(detail.code) || null,
    );
  }
  if (!text(record(payload).workflowId)) {
    // Accepted but unreadable: report it as possibly started, not as failed.
    throw new Error('The publication response could not be read.');
  }
  return payload as SavedWorkPublicationResult;
}

export interface SavedWorkPublicationAvailability {
  available: boolean;
  /** The server's disabled reason; null when available or not reported. */
  reason: string | null;
  allowedModes: SavedWorkObjective[];
  /** Set when a rollout canary admits only these destination repositories. */
  canaryRepositories: string[];
}

export function savedWorkPublicationAvailability(
  execution: SavedResultExecutionLike,
): SavedWorkPublicationAvailability {
  // Availability is the execution's server action projection, which applies
  // the same submission gate and rollout admission as the publication route.
  // An unreported capability is unavailable, never assumed.
  const actions = record(execution.actions);
  if (actions.canPublishSavedWork !== true) {
    return {
      available: false,
      reason: text(record(actions.disabledReasons).canPublishSavedWork) || null,
      allowedModes: [],
      canaryRepositories: [],
    };
  }
  const limits = record(record(actions.actionEvidence).publishSavedWork);
  const allowedModes = Array.isArray(limits.allowedModes)
    ? SAVED_WORK_OBJECTIVES.filter((mode) => (limits.allowedModes as unknown[]).includes(mode))
    : [...SAVED_WORK_OBJECTIVES];
  const canaryRepositories = Array.isArray(limits.canaryRepositories)
    ? limits.canaryRepositories.map(text).filter(Boolean)
    : [];
  return { available: true, reason: null, allowedModes, canaryRepositories };
}

const PUBLICATION_UNAVAILABLE_MESSAGES: Record<string, string> = {
  publication_recovery_disabled:
    "Publishing saved work is turned off by this deployment's publication rollout policy.",
  publication_recovery_shadow_only:
    'The publication rollout is in shadow mode, so saved work cannot be published yet.',
  publication_mode_not_allowed: 'The publication rollout policy allows no publication mode.',
  publication_recovery_not_in_canary: 'The publication rollout canary does not include this operator.',
  publication_recovery_policy_invalid:
    'The publication rollout setting is invalid, so saved work cannot be published.',
  temporal_submit_disabled: 'Workflow submission is disabled on this deployment.',
  historical_workflow_type: 'Retired workflow history is read-only.',
};

export function savedWorkPublicationUnavailableMessage(reason: string | null): string {
  if (!reason) {
    return 'The server has not reported whether saved work can be published. Refresh to check again.';
  }
  return (
    PUBLICATION_UNAVAILABLE_MESSAGES[reason] ?? `Publishing saved work is unavailable (${reason}).`
  );
}

export interface SavedWorkPublicationOperation {
  workflowId: string;
  /** Compute-style outcome of the publication run (for example `completed`). */
  outcome: string;
  terminal: boolean;
}

export async function fetchSavedWorkPublicationOperation(
  apiBase: string,
  workflowId: string,
): Promise<SavedWorkPublicationOperation> {
  // The returned publication operation is an ordinary execution, so it is
  // followed through the existing execution detail read. Nothing here starts,
  // retries, or cancels work.
  const response = await fetch(
    joinApiBasePath(apiBase, `/executions/${encodeURIComponent(workflowId)}?source=temporal`),
    { credentials: 'include', headers: { Accept: 'application/json' } },
  );
  const payload = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const detail = record(record(payload).detail);
    throw new SavedWorkPublicationError(
      savedResultErrorMessage(payload) || `Publication status read failed (${response.status})`,
      response.status,
      text(detail.code) || null,
    );
  }
  const execution = record(payload) as SavedResultExecutionLike;
  // The server's close status already maps timed-out and terminated runs.
  const closed = text(execution.temporalStatus ?? execution.temporal_status).toLowerCase();
  if (['completed', 'failed', 'canceled'].includes(closed)) {
    return { workflowId, outcome: closed, terminal: true };
  }
  return {
    workflowId,
    outcome: computeOutcome(execution),
    terminal: isTerminalExecution(execution),
  };
}

export interface SavedWorkPartEntry {
  artifactId: string;
  role: string;
  /** Present only when the part is in the run's artifact listing. */
  present: boolean;
  complete: boolean;
  completenessReason: string;
  restricted: boolean;
  expired: boolean;
}

export interface SavedWorkUnit {
  /** False for manifests listed without the server's compact summary. */
  summaryAvailable: boolean;
  formats: Array<{ format: string; status: string; required: boolean }>;
  exclusionReasons: Array<{ reason: string; count: number }>;
  limitations: string[];
  parts: SavedWorkPartEntry[];
}

export interface SavedResultEntry {
  artifactId: string;
  title: string;
  kind: SavedResultKind;
  complete: boolean;
  completenessReason: string;
  restricted: boolean;
  expired: boolean;
  retention: string | null;
  exclusions: number | null;
  /** Set when the entry is a committed saved-work manifest. */
  savedWork: SavedWorkUnit | null;
}

function entryTitle(artifact: SavedResultArtifactLike): string {
  const metadata = (artifact.metadata ?? {}) as Record<string, unknown>;
  for (const key of ['title', 'filename', 'name', 'label']) {
    const value = text(metadata[key]);
    if (value) {
      return value;
    }
  }
  const links = Array.isArray(artifact.links) ? artifact.links : [];
  for (const link of links) {
    const value = text(link.label);
    if (value) {
      return value;
    }
  }
  return artifact.artifactId;
}

function entryRetention(artifact: SavedResultArtifactLike): string | null {
  const metadata = (artifact.metadata ?? {}) as Record<string, unknown>;
  for (const key of [
    'retentionRef',
    'retention_ref',
    'retentionClass',
    'retention_class',
    'expiresAt',
    'expires_at',
    'retention',
  ]) {
    const value = text(
      metadata[key] ??
        (artifact as Record<string, unknown>)[key],
    );
    if (value) {
      return value;
    }
  }
  return null;
}

function entryExclusions(artifact: SavedResultArtifactLike): number | null {
  const metadata = (artifact.metadata ?? {}) as Record<string, unknown>;
  const raw =
    metadata.exclusions ??
    metadata.excludedPaths ??
    metadata.excluded_paths ??
    (artifact as Record<string, unknown>).exclusions;
  if (Array.isArray(raw)) {
    return raw.length;
  }
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return raw;
  }
  return null;
}

function toEntry(
  artifact: SavedResultArtifactLike,
  now: number,
): SavedResultEntry {
  const classified = classifySavedResultArtifact(artifact);
  return {
    artifactId: artifact.artifactId,
    title: entryTitle(artifact),
    kind: classified.kind,
    complete: classified.complete,
    completenessReason: classified.reason,
    restricted: !canDownloadSavedResultRaw(artifact),
    expired: isExpiredArtifact(artifact, now),
    retention: entryRetention(artifact),
    exclusions: entryExclusions(artifact),
    savedWork: null,
  };
}

const FORMAT_PART_ROLES: Record<string, string> = {
  full_snapshot: 'snapshot',
  exact_baseline_delta: 'delta',
  selected_history: 'history',
};

// Output claims that keep a required format usable; anything else
// (incomplete, failed, inapplicable, unknown) cannot satisfy it.
const USABLE_FORMAT_STATUSES = new Set(['self_contained', 'requires_dependencies']);

function savedWorkSummary(
  artifact: SavedResultArtifactLike,
): Record<string, unknown> | null {
  const summary = record(record(artifact.metadata).saved_work_summary);
  return Array.isArray(summary.outputs) ? summary : null;
}

function toPart(
  artifactId: string,
  role: string,
  byId: Map<string, SavedResultArtifactLike>,
  now: number,
): SavedWorkPartEntry {
  const artifact = byId.get(artifactId);
  if (!artifact) {
    return {
      artifactId,
      role,
      present: false,
      complete: false,
      completenessReason: 'not-listed',
      restricted: true,
      expired: false,
    };
  }
  const classified = classifySavedResultArtifact(artifact);
  return {
    artifactId,
    role,
    present: true,
    complete: classified.complete,
    completenessReason: classified.reason,
    restricted: !canDownloadSavedResultRaw(artifact),
    expired: isExpiredArtifact(artifact, now),
  };
}

function toSavedWorkEntry(
  manifest: SavedResultArtifactLike,
  byId: Map<string, SavedResultArtifactLike>,
  checkpointManifests: SavedResultArtifactLike[],
  now: number,
): SavedResultEntry {
  const base = toEntry(manifest, now);
  const summary = savedWorkSummary(manifest);
  if (!summary) {
    // A listed manifest without the compact summary still has its own
    // server status and content identity; its format claims are unknown.
    return {
      ...base,
      kind: 'repository',
      title: 'Saved work',
      savedWork: {
        summaryAvailable: false,
        formats: [],
        exclusionReasons: [],
        limitations: [],
        parts: [],
      },
    };
  }
  const required = new Set(
    (Array.isArray(summary.required_formats) ? summary.required_formats : []).map(
      (value) => text(value),
    ),
  );
  const claims = (summary.outputs as unknown[]).map((item) => record(item));
  const formats = claims.map((claim) => ({
    format: text(claim.format),
    status: text(claim.status),
    required: required.has(text(claim.format)),
  }));
  const parts: SavedWorkPartEntry[] = [];
  const partByFormat = new Map<string, SavedWorkPartEntry>();
  for (const claim of claims) {
    const artifactId = text(claim.artifact_id);
    if (!artifactId) {
      continue;
    }
    const format = text(claim.format);
    const part = toPart(artifactId, FORMAT_PART_ROLES[format] ?? format, byId, now);
    parts.push(part);
    partByFormat.set(format, part);
  }
  const snapshot = partByFormat.get('full_snapshot');
  for (const checkpoint of checkpointManifests) {
    const indexed = record(record(checkpoint.metadata).checkpoint_parts);
    if (!snapshot || text(indexed.archive_artifact_id) !== snapshot.artifactId) {
      continue;
    }
    parts.push(toPart(checkpoint.artifactId, 'file-manifest', byId, now));
    const indexPatch = text(indexed.index_patch_artifact_id);
    if (indexPatch) {
      parts.push(toPart(indexPatch, 'index-patch', byId, now));
    }
  }

  // The manifest's own server status and identity come first; its claims and
  // the listed parts of each required format can only downgrade that.
  let complete = base.complete;
  let completenessReason = base.completenessReason;
  if (complete) {
    for (const format of required) {
      const claim = formats.find((item) => item.format === format);
      if (!claim || !USABLE_FORMAT_STATUSES.has(claim.status)) {
        complete = false;
        completenessReason = `format-${format}-${claim?.status || 'missing'}`;
        break;
      }
      const part = partByFormat.get(format);
      if (!part) {
        complete = false;
        completenessReason = `part-${FORMAT_PART_ROLES[format] ?? format}-missing-artifact`;
        break;
      }
      if (!part.complete || part.expired) {
        complete = false;
        completenessReason = `part-${part.role}-${part.expired ? 'expired' : part.completenessReason}`;
        break;
      }
    }
  }
  const exclusionCount = summary.exclusion_count;
  return {
    ...base,
    kind: claims.some((claim) => text(claim.format) !== 'report_only' && text(claim.artifact_id))
      ? 'repository'
      : 'non-git',
    title: 'Saved work',
    complete,
    completenessReason,
    exclusions:
      typeof exclusionCount === 'number' && Number.isFinite(exclusionCount)
        ? exclusionCount
        : null,
    savedWork: {
      summaryAvailable: true,
      formats,
      exclusionReasons: (Array.isArray(summary.exclusion_reasons)
        ? summary.exclusion_reasons
        : []
      ).map((item) => ({
        reason: text(record(item).reason),
        count: Number(record(item).count) || 0,
      })),
      limitations: (Array.isArray(summary.limitations) ? summary.limitations : [])
        .map((item) => text(item))
        .filter(Boolean),
      parts,
    },
  };
}

function projectEntries(
  artifacts: SavedResultArtifactLike[],
  now: number,
): SavedResultEntry[] {
  const outputs = artifacts.filter(isSavedOutputArtifact);
  const byId = new Map(artifacts.map((artifact) => [artifact.artifactId, artifact]));
  const checkpointManifests = outputs.filter(
    (artifact) => artifactKind(artifact) === 'checkpoint_manifest',
  );
  const units = outputs
    .filter(isSavedWorkManifest)
    .map((manifest) => toSavedWorkEntry(manifest, byId, checkpointManifests, now));
  // Parts belong to their saved unit; they are not separate saved results.
  const grouped = new Set(
    units.flatMap((unit) => unit.savedWork?.parts.map((part) => part.artifactId) ?? []),
  );
  const unitIds = new Set(units.map((unit) => unit.artifactId));
  const singles = outputs
    .filter((artifact) => !unitIds.has(artifact.artifactId) && !grouped.has(artifact.artifactId))
    .map((artifact) => toEntry(artifact, now));
  return [...units, ...singles];
}

export function canPublishSavedWork(entry: SavedResultEntry): boolean {
  const unit = entry.savedWork;
  if (!unit || !entry.complete || entry.expired || entry.restricted) {
    return false;
  }
  // Older manifests lack a compact summary. The server admission checks the
  // authoritative manifest and its raw dependency closure for every request.
  if (!unit.summaryAvailable) {
    return true;
  }
  if (!unit.formats.some((claim) => claim.format === 'full_snapshot' && claim.status === 'self_contained')) {
    return false;
  }
  // Materialization reads the snapshot and every recorded delta, including
  // optional deltas. Preview access to any of them cannot authorize that read.
  const publicationParts = unit.parts.filter((part) => part.role === 'snapshot' || part.role === 'delta');
  return publicationParts.some((part) => part.role === 'snapshot') && publicationParts.every(
    (part) => part.present && part.complete && !part.expired && !part.restricted,
  );
}

function computeOutcome(execution: SavedResultExecutionLike): string {
  const state = text(
    execution.rawState ?? execution.state ?? execution.status,
  ).toLowerCase();
  if (['failed', 'error', 'terminated'].includes(state)) {
    return 'failed';
  }
  if (['canceled', 'cancelled'].includes(state)) {
    return 'canceled';
  }
  if (state === 'no_commit') {
    return 'no_commit';
  }
  if (['completed', 'succeeded', 'success', 'closed'].includes(state)) {
    return 'completed';
  }
  if (['running', 'executing', 'pending', 'waiting'].includes(state)) {
    return state;
  }
  const close = text(
    execution.closeStatus ?? execution.close_status,
  ).toLowerCase();
  if (close.includes('fail')) {
    return 'failed';
  }
  if (close.includes('cancel')) {
    return 'canceled';
  }
  if (close.includes('complet') || close.includes('success')) {
    return 'completed';
  }
  return state || 'unknown';
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function finishSummaryOf(
  execution: SavedResultExecutionLike,
): Record<string, unknown> {
  return record(execution.finishSummary ?? execution.finish_summary);
}

function auxiliaryOutcomes(
  execution: SavedResultExecutionLike,
): Record<string, unknown> {
  return record(record(finishSummaryOf(execution).controlStop).auxiliaryOutcomes);
}

function publicationOutcome(execution: SavedResultExecutionLike): string {
  // The control-stop Git publication outcome is authoritative: the retry
  // endpoint and the action capability read the same value.
  const recovery = text(
    record(auxiliaryOutcomes(execution).gitPublication).status,
  ).toLowerCase();
  if (recovery) {
    return recovery;
  }
  const publish = record(finishSummaryOf(execution).publish);
  const status = text(publish.status).toLowerCase();
  if (status) {
    return status;
  }
  const branch =
    (execution.outputBranch ?? execution.output_branch ?? {}) as Record<
      string,
      unknown
    >;
  const branchStatus = text(branch.status).toLowerCase();
  if (branchStatus) {
    return branchStatus;
  }
  return 'none';
}

function cleanupOutcome(execution: SavedResultExecutionLike): string {
  // Report recorded cleanup evidence; without it the outcome is unknown,
  // never an assumed success.
  const auxiliary = auxiliaryOutcomes(execution);
  const statuses = ['hostCleanup', 'providerProfileRelease']
    .map((key) => text(record(auxiliary[key]).status).toLowerCase())
    .filter(Boolean);
  if (statuses.includes('failed')) {
    return 'failed';
  }
  if (auxiliary.janitorRequired === true) {
    return 'janitor_required';
  }
  if (statuses.includes('pending')) {
    return 'pending';
  }
  if (statuses.length > 0 && statuses.every((status) => status === 'completed')) {
    return 'completed';
  }
  return statuses[0] ?? 'unknown';
}

function isTerminalExecution(execution: SavedResultExecutionLike): boolean {
  const outcome = computeOutcome(execution);
  return ['failed', 'canceled', 'completed', 'no_commit'].includes(outcome);
}

export function projectSavedResults(input: {
  workflowId: string;
  runId: string;
  execution: SavedResultExecutionLike;
  artifacts: SavedResultArtifactLike[];
  artifactsStale: boolean;
  artifactsError: Error | null;
  /** Source evidence refs the `/continue` endpoint authorizes. */
  authorizedContinuationRefs?: string[] | null;
  now?: number;
}): {
  selectedKey: string;
  state: SavedResultState;
  entries: SavedResultEntry[];
  continuationRefs: string[];
  terminalSource: boolean;
  computeOutcome: string;
  saveOutcome: string;
  publicationOutcome: string;
  cleanupOutcome: string;
} {
  const selectedKey = savedResultSelectionKey(input.workflowId, input.runId);
  const terminalSource = isTerminalExecution(input.execution);
  const base = {
    selectedKey,
    continuationRefs: [] as string[],
    terminalSource,
    computeOutcome: computeOutcome(input.execution),
    publicationOutcome: publicationOutcome(input.execution),
    cleanupOutcome: cleanupOutcome(input.execution),
  };

  if (input.artifactsError) {
    return {
      ...base,
      state: 'unavailable',
      entries: [],
      saveOutcome: 'unavailable',
    };
  }
  if (input.artifactsStale) {
    return {
      ...base,
      state: 'stale',
      entries: [],
      saveOutcome: 'stale',
    };
  }
  const now = input.now ?? Date.now();
  const entries = projectEntries(input.artifacts ?? [], now);
  if (entries.length === 0) {
    const state = terminalSource ? 'unavailable' : 'pending';
    return { ...base, state, entries: [], saveOutcome: state };
  }
  const saveOutcome = entries.some((entry) => entry.complete)
    ? 'committed'
    : 'incomplete';
  const authorized = new Set(input.authorizedContinuationRefs ?? []);
  return {
    ...base,
    state: 'ready',
    entries,
    continuationRefs: entries
      .filter((entry) => entry.complete && authorized.has(entry.artifactId))
      .map((entry) => entry.artifactId),
    saveOutcome,
  };
}
