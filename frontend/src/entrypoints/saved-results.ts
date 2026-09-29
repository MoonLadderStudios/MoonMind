/**
 * Compact saved-result projection for MoonLadderStudios/MoonMind#4020.
 *
 * Uses only the server's actual selected run/attempt/result and committed
 * artifact references. No second result store, no frontend-invented status,
 * no compute restart. Download reuses the existing authorized artifact
 * download endpoint; Continue reuses the existing publication-free
 * `POST /executions/{workflowId}/continue` fresh-admission path; Publish
 * Saved Work reuses the existing publication-only
 * `POST /executions/{workflowId}/retry-publication` path (no model rerun).
 *
 * Preview access never authorizes raw restore/publication: an ArtifactRef is
 * an identifier, not a URL or credential. Raw bytes require
 * `raw_access_allowed === true`; otherwise only metadata-first preview via
 * `default_read_ref` (or the artifact metadata document) is exposed.
 */

export interface SavedResultLinkLike {
  linkType?: string;
  link_type?: string;
  label?: string | null;
  [key: string]: unknown;
}

export interface SavedResultArtifactLike {
  artifactId: string;
  contentType?: string | null;
  content_type?: string | null;
  sizeBytes?: number | null;
  size_bytes?: number | null;
  status?: string | null;
  sha256?: string | null;
  digest?: string | null;
  contentDigest?: string | null;
  content_digest?: string | null;
  downloadUrl?: string | null;
  download_url?: string | null;
  defaultReadRef?: { artifactId?: string } | null;
  default_read_ref?: { artifactId?: string; artifact_id?: string } | null;
  rawAccessAllowed?: boolean | null;
  raw_access_allowed?: boolean | null;
  metadata?: Record<string, unknown> | null;
  links?: SavedResultLinkLike[] | null;
  [key: string]: unknown;
}

export interface SavedResultExecutionLike {
  workflowId?: string | null;
  workflow_id?: string | null;
  runId?: string | null;
  run_id?: string | null;
  temporalRunId?: string | null;
  temporal_run_id?: string | null;
  state?: string | null;
  rawState?: string | null;
  status?: string | null;
  closeStatus?: string | null;
  close_status?: string | null;
  finishSummary?: Record<string, unknown> | null;
  finish_summary?: Record<string, unknown> | null;
  outputBranch?: Record<string, unknown> | null;
  output_branch?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export type SavedResultKind = 'report' | 'repository' | 'non-git';
export type SavedResultState = 'ready' | 'pending' | 'unavailable' | 'stale';

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
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
    const normalized = text(candidate);
    if (normalized && normalized.toLowerCase() !== 'absent') {
      return normalized;
    }
  }
  const metadata = (artifact.metadata ?? {}) as Record<string, unknown>;
  for (const key of ['sha256', 'digest', 'contentDigest', 'content_digest']) {
    const normalized = text(metadata[key]);
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

function contentType(artifact: SavedResultArtifactLike): string {
  return text(artifact.contentType ?? artifact.content_type).toLowerCase();
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
      ['repository.', 'git.', 'patch.', 'diff.', 'checkpoint.', 'capture.'].some(
        (prefix) => type.startsWith(prefix),
      ),
    ) ||
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

function isExpiredArtifact(artifact: SavedResultArtifactLike): boolean {
  return artifactStatus(artifact) === 'EXPIRED';
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

export function savedResultSelectionKey(
  workflowId: string,
  runId: string,
): string {
  return `${workflowId}|${runId}`;
}

export function shouldApplySavedResultResponse(
  requestKey: string,
  currentKey: string,
): boolean {
  // Late responses must never retarget a historical selection or overwrite a
  // newer operation: only the exact current selection applies.
  return requestKey === currentKey;
}

export function savedResultIdempotencyKey(
  workflowId: string,
  runId: string,
  action: 'continue' | 'publish' | 'download',
  artifactId?: string,
): string {
  // Single-user instance/resource key: no human-user partitions.
  const subject = artifactId ? `:${artifactId}` : ':selection';
  return `saved-result:${action}:${workflowId}:${runId}${subject}`;
}

export function resolveUncertainSavedResultSubmission(input: {
  pendingOperationId: string;
  response: {
    status: number;
    code?: string | null;
    operationId?: string | null;
  } | null;
}): { reused: boolean; operationId: string } {
  const { pendingOperationId, response } = input;
  if (!response) {
    return { reused: false, operationId: pendingOperationId };
  }
  const conflictCodes = new Set([
    'idempotency_key_conflict',
    'continuation_idempotency_conflict',
    'publication_idempotency_key_conflict',
    'publication_recovery_already_started',
  ]);
  if (
    response.status === 409 &&
    ((response.code && conflictCodes.has(response.code)) ||
      text(response.operationId))
  ) {
    return {
      reused: true,
      operationId: text(response.operationId) || pendingOperationId,
    };
  }
  if (text(response.operationId) === pendingOperationId) {
    return { reused: true, operationId: pendingOperationId };
  }
  return {
    reused: false,
    operationId: text(response.operationId) || pendingOperationId,
  };
}

export function buildContinueInNewWorkflowBody(input: {
  idempotencyKey: string;
  selectedSourceArtifactRefs: string[];
  instructions?: string;
  title?: string | null;
  initialParameters?: Record<string, unknown>;
  boundedPurpose?: string | null;
}): Record<string, unknown> {
  // The browser authors only new intent plus already-authorized source refs.
  // Source run, session, host, profile, credential, and workspace ownership
  // stay server-pinned; they are never authored here.
  const body: Record<string, unknown> = {
    idempotencyKey: input.idempotencyKey,
    selectedSourceArtifactRefs: [...input.selectedSourceArtifactRefs],
  };
  if (input.title !== undefined && input.title !== null) {
    body.title = input.title;
  }
  if (input.instructions !== undefined) {
    body.instructions = input.instructions;
  }
  if (input.initialParameters !== undefined) {
    body.initialParameters = { ...input.initialParameters };
  }
  if (input.boundedPurpose !== undefined && input.boundedPurpose !== null) {
    body.boundedPurpose = input.boundedPurpose;
  }
  return body;
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

function toEntry(artifact: SavedResultArtifactLike): SavedResultEntry {
  const classified = classifySavedResultArtifact(artifact);
  return {
    artifactId: artifact.artifactId,
    title: entryTitle(artifact),
    kind: classified.kind,
    complete: classified.complete,
    completenessReason: classified.reason,
    restricted: !canDownloadSavedResultRaw(artifact),
    expired: isExpiredArtifact(artifact),
    retention: entryRetention(artifact),
    exclusions: entryExclusions(artifact),
  };
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

function publicationOutcome(execution: SavedResultExecutionLike): string {
  const summary =
    (execution.finishSummary ?? execution.finish_summary ?? {}) as Record<
      string,
      unknown
    >;
  const publish = (summary.publish ?? {}) as Record<string, unknown>;
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

function isTerminalExecution(execution: SavedResultExecutionLike): boolean {
  const outcome = computeOutcome(execution);
  return ['failed', 'canceled', 'completed'].includes(outcome);
}

export function projectSavedResults(input: {
  workflowId: string;
  runId: string;
  execution: SavedResultExecutionLike;
  artifacts: SavedResultArtifactLike[];
  artifactsStale: boolean;
  artifactsError: Error | null;
}): {
  selectedKey: string;
  state: SavedResultState;
  entries: SavedResultEntry[];
  computeOutcome: string;
  saveOutcome: string;
  publicationOutcome: string;
  cleanupPreserved: boolean;
} {
  const selectedKey = savedResultSelectionKey(input.workflowId, input.runId);
  const computed = computeOutcome(input.execution);
  const publication = publicationOutcome(input.execution);

  if (input.artifactsError) {
    return {
      selectedKey,
      state: 'unavailable',
      entries: [],
      computeOutcome: computed,
      saveOutcome: 'unavailable',
      publicationOutcome: publication,
      cleanupPreserved: true,
    };
  }
  if (input.artifactsStale) {
    return {
      selectedKey,
      state: 'stale',
      entries: [],
      computeOutcome: computed,
      saveOutcome: 'stale',
      publicationOutcome: publication,
      cleanupPreserved: true,
    };
  }
  const entries = (input.artifacts ?? []).map(toEntry);
  if (entries.length === 0) {
    return {
      selectedKey,
      state: isTerminalExecution(input.execution)
        ? 'unavailable'
        : 'pending',
      entries: [],
      computeOutcome: computed,
      saveOutcome: isTerminalExecution(input.execution)
        ? 'unavailable'
        : 'pending',
      publicationOutcome: publication,
      cleanupPreserved: true,
    };
  }
  const saveOutcome = entries.some((entry) => entry.complete)
    ? 'committed'
    : 'incomplete';
  return {
    selectedKey,
    state: 'ready',
    entries,
    computeOutcome: computed,
    saveOutcome,
    publicationOutcome: publication,
    cleanupPreserved: true,
  };
}
