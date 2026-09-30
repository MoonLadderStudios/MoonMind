/**
 * Compact saved-result projection for MoonLadderStudios/MoonMind#4020.
 *
 * Uses only the server's actual selected run/attempt/result and committed
 * artifact references. No second result store, no frontend-invented status,
 * no compute restart. Download reuses the existing authorized artifact
 * download endpoint; Continue reuses the existing continuation client
 * (`POST /executions/{workflowId}/continue`, fresh admission); Publish Saved
 * Work reuses the existing publication-only
 * `POST /executions/{workflowId}/retry-publication` path (no model rerun).
 *
 * Preview access never authorizes raw restore/publication: an ArtifactRef is
 * an identifier, not a URL or credential. Raw bytes require
 * `raw_access_allowed === true`; otherwise only a distinct server preview
 * artifact is exposed.
 */

import type { CapturedEvidence } from '../features/workflow-native-chat/WorkflowTerminalChatActions';
import { apiErrorMessage } from '../features/workflow-native-chat/WorkflowTerminalChatActions';

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
  previewArtifactRef?: { artifactId?: string } | null;
  preview_artifact_ref?: { artifactId?: string; artifact_id?: string } | null;
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

function refArtifactId(ref: unknown): string {
  if (!ref || typeof ref !== 'object') {
    return '';
  }
  return text(
    (ref as Record<string, unknown>).artifactId ??
      (ref as Record<string, unknown>).artifact_id,
  );
}

function previewArtifactId(artifact: SavedResultArtifactLike): string | null {
  // Only a distinct server preview artifact is a safe preview. When raw access
  // is denied and no preview exists, the server's default_read_ref is the raw
  // artifact itself, whose download it refuses.
  const candidates = [
    refArtifactId(artifact.previewArtifactRef ?? artifact.preview_artifact_ref),
    refArtifactId(artifact.defaultReadRef ?? artifact.default_read_ref),
  ];
  return (
    candidates.find((id) => id && id !== artifact.artifactId) ?? null
  );
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
): string | null {
  // The server's redacted/bounded preview artifact, or nothing. This never
  // authorizes raw restore/publication and never performs a credentialed
  // fetch by itself (plain anchor navigation only).
  const previewId = previewArtifactId(artifact);
  return previewId
    ? joinApiBasePath(
        apiBase,
        `/artifacts/${encodeURIComponent(previewId)}/download`,
      )
    : null;
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

function stableHash(value: string): string {
  // FNV-1a (two 32-bit lanes): a stable content fingerprint, not a secret.
  let a = 0x811c9dc5;
  let b = 0x01000193;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    a = Math.imul(a ^ code, 0x01000193) >>> 0;
    b = Math.imul(b ^ code, 0x811c9dc5) >>> 0;
  }
  return `${a.toString(16).padStart(8, '0')}${b.toString(16).padStart(8, '0')}`;
}

export function savedResultContinueIdempotencyKey(input: {
  workflowId: string;
  runId: string;
  title: string;
  instructions: string;
  selectedSourceArtifactRefs: string[];
}): string {
  // Bound to the selected run and the exact authored request: a lost
  // acknowledgment, reload, or double click resubmits the same key and the
  // server returns the same continuation; a changed request is a new one.
  // Single-user instance/resource key: no human-user partitions.
  const content = JSON.stringify([
    input.title.trim(),
    input.instructions.trim(),
    [...input.selectedSourceArtifactRefs].sort(),
  ]);
  return `saved-result:continue:${input.workflowId}:${input.runId}:${stableHash(content)}`;
}

export interface PublicationRecoveryResult {
  sourceWorkflowId: string;
  sourceRunId: string;
  workflowId: string;
  runId: string;
  publicationIdempotencyKey: string;
}

export async function requestPublicationRecovery(
  apiBase: string,
  workflowId: string,
  options: { expectedSourceRunId?: string } = {},
): Promise<PublicationRecoveryResult> {
  // The existing publication-only path. The server derives one deterministic
  // operation per source contract, so a repeated request returns it again.
  const init: RequestInit = {
    method: 'POST',
    credentials: 'include',
    headers: { Accept: 'application/json' },
  };
  if (options.expectedSourceRunId) {
    init.headers = { Accept: 'application/json', 'Content-Type': 'application/json' };
    init.body = JSON.stringify({ expectedSourceRunId: options.expectedSourceRunId });
  }
  const response = await fetch(
    joinApiBasePath(
      apiBase,
      `/executions/${encodeURIComponent(workflowId)}/retry-publication`,
    ),
    init,
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      apiErrorMessage(payload) ||
        `Publication recovery: ${response.statusText || response.status}`,
    );
  }
  return (await response.json()) as PublicationRecoveryResult;
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

function recordedStatus(
  execution: SavedResultExecutionLike,
  key: 'evidencePublication' | 'workspacePreservation',
): string | null {
  // Server-recorded save evidence from the control-stop finalization; absent
  // evidence stays absent rather than an assumed success.
  return text(record(auxiliaryOutcomes(execution)[key]).status).toLowerCase() || null;
}

function normalizedRefId(ref: string): string {
  // Same normalization as the server's artifact-ref reader.
  return ref.trim().replace(/^artifact:\/\//, '').replace(/^input\//, '');
}

export type SavedResultCaptureState =
  | 'recorded'
  | 'unavailable'
  | 'pending'
  | 'stale';

export interface SavedResultCapture {
  state: SavedResultCaptureState;
  itemCount: number;
  captureManifestRef: string | null;
  reason: string | null;
}

function projectCapture(
  runId: string,
  evidence: CapturedEvidence | null | undefined,
  evidenceError: boolean,
): { capture: SavedResultCapture; authorizedRefs: string[] } {
  const none = { itemCount: 0, captureManifestRef: null };
  if (evidenceError) {
    return {
      capture: { ...none, state: 'unavailable', reason: 'captured_evidence_unavailable' },
      authorizedRefs: [],
    };
  }
  if (!evidence) {
    return { capture: { ...none, state: 'pending', reason: null }, authorizedRefs: [] };
  }
  // Captured evidence is authorized for the server's current run. It is only
  // applied to the exact displayed run; anything else is stale.
  if (text(evidence.runId) !== runId) {
    return {
      capture: { ...none, state: 'stale', reason: 'captured_evidence_other_run' },
      authorizedRefs: [],
    };
  }
  const items = Array.isArray(evidence.items) ? evidence.items : [];
  if (!evidence.available || items.length === 0) {
    return {
      capture: {
        ...none,
        state: 'unavailable',
        reason: text(evidence.unavailableReason) || 'no_captured_evidence',
      },
      authorizedRefs: [],
    };
  }
  const manifest = items.find((item) => item.kind === 'capture_manifest');
  return {
    capture: {
      state: 'recorded',
      itemCount: items.length,
      captureManifestRef: manifest ? text(manifest.artifactRef) || null : null,
      reason: null,
    },
    authorizedRefs: items.map((item) => text(item.artifactRef)).filter(Boolean),
  };
}

export function projectSavedResults(input: {
  workflowId: string;
  runId: string;
  execution: SavedResultExecutionLike;
  artifacts: SavedResultArtifactLike[];
  artifactsStale: boolean;
  artifactsError: Error | null;
  /** The `/captured-evidence` response; its refs are what `/continue` authorizes. */
  capturedEvidence?: CapturedEvidence | null;
  capturedEvidenceError?: boolean;
  now?: number;
}): {
  selectedKey: string;
  state: SavedResultState;
  entries: SavedResultEntry[];
  continuationRefs: string[];
  terminalSource: boolean;
  computeOutcome: string;
  saveOutcome: string;
  committedCount: number;
  evidencePublicationOutcome: string | null;
  workspacePreservationOutcome: string | null;
  capture: SavedResultCapture;
  publicationOutcome: string;
  cleanupOutcome: string;
} {
  const selectedKey = savedResultSelectionKey(input.workflowId, input.runId);
  const terminalSource = isTerminalExecution(input.execution);
  const evidencePublicationOutcome = recordedStatus(
    input.execution,
    'evidencePublication',
  );
  const { capture, authorizedRefs } = projectCapture(
    input.runId,
    input.capturedEvidence,
    Boolean(input.capturedEvidenceError),
  );
  const base = {
    selectedKey,
    continuationRefs: [] as string[],
    terminalSource,
    computeOutcome: computeOutcome(input.execution),
    committedCount: 0,
    evidencePublicationOutcome,
    workspacePreservationOutcome: recordedStatus(
      input.execution,
      'workspacePreservation',
    ),
    capture,
    publicationOutcome: publicationOutcome(input.execution),
    cleanupOutcome: cleanupOutcome(input.execution),
  };
  // A server-recorded save failure is never masked by listed artifacts.
  const recordedFailure = evidencePublicationOutcome === 'failed';

  if (input.artifactsError) {
    return {
      ...base,
      state: 'unavailable',
      entries: [],
      saveOutcome: recordedFailure ? 'failed' : 'unavailable',
    };
  }
  if (input.artifactsStale) {
    return {
      ...base,
      state: 'stale',
      entries: [],
      saveOutcome: recordedFailure ? 'failed' : 'stale',
    };
  }
  const now = input.now ?? Date.now();
  const entries = (input.artifacts ?? [])
    .filter(isSavedOutputArtifact)
    .map((artifact) => toEntry(artifact, now));
  if (entries.length === 0) {
    const state = terminalSource ? 'unavailable' : 'pending';
    return {
      ...base,
      state,
      entries: [],
      saveOutcome: recordedFailure ? 'failed' : state,
    };
  }
  const committedCount = entries.filter((entry) => entry.complete).length;
  // Carry only committed outputs the server authorizes for this run. A
  // restricted output is never carried: continuation copies raw bytes, and
  // preview access never authorizes raw restore.
  const authorizedById = new Map(
    authorizedRefs.map((ref) => [normalizedRefId(ref), ref] as const),
  );
  const continuationRefs = entries
    .filter((entry) => entry.complete && !entry.restricted && !entry.expired)
    .map((entry) => authorizedById.get(entry.artifactId))
    .filter((ref): ref is string => Boolean(ref));
  return {
    ...base,
    state: 'ready',
    entries,
    committedCount,
    continuationRefs,
    saveOutcome: recordedFailure
      ? 'failed'
      : committedCount > 0
        ? 'committed'
        : 'incomplete',
  };
}
