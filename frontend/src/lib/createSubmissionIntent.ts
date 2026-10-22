import { newRequestId } from './requestId';

export interface CreateSubmissionResult {
  workflowId?: string;
  runId?: string;
  temporalRunId?: string;
  namespace?: string;
  redirectPath?: string;
  definitionId?: string;
}

/** The API returns execution details too; never persist its draft or inputs. */
export function createSubmissionResult(value: CreateSubmissionResult): CreateSubmissionResult {
  const result: CreateSubmissionResult = {};
  for (const field of ['workflowId', 'runId', 'temporalRunId', 'namespace', 'redirectPath', 'definitionId'] as const) {
    if (typeof value[field] === 'string') result[field] = value[field];
  }
  return result;
}

export interface CreateSubmissionIntent {
  key: string;
  uncertain: boolean;
  accepted?: CreateSubmissionResult;
  inputArtifactRef?: string;
  scheduledFor?: string;
  artifactLinks?: Record<string, 'uncertain' | 'complete'>;
}

// Like saved-result continuation identities, this digest works on HTTP LAN
// origins without SubtleCrypto. Only identity metadata, never the draft body,
// goes into sessionStorage. The random key is the server's admission identity.
function digest(value: string | Uint8Array): string {
  let first = 0x811c9dc5;
  let second = 0x01000193 ^ value.length;
  for (let index = 0; index < value.length; index += 1) {
    const code = typeof value === 'string' ? value.charCodeAt(index) : value[index]!;
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x5bd1e995);
  }
  return [first, second].map((lane) => (lane >>> 0).toString(16).padStart(8, '0')).join('');
}

export interface CreateAttachmentRef {
  artifactId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
}

function attachmentMetadata(value: unknown): CreateAttachmentRef | null {
  if (!value || typeof value !== 'object') return null;
  const ref = value as CreateAttachmentRef;
  return typeof ref.artifactId === 'string' && typeof ref.filename === 'string' &&
    typeof ref.contentType === 'string' && typeof ref.sizeBytes === 'number' && Number.isFinite(ref.sizeBytes)
    ? { artifactId: ref.artifactId, filename: ref.filename, contentType: ref.contentType, sizeBytes: ref.sizeBytes } : null;
}

function artifactLinkProgress(value: unknown): Record<string, 'uncertain' | 'complete'> {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(Object.entries(value).filter(([, status]) => status === 'uncertain' || status === 'complete'));
}

/** Hash bytes, not name/mtime: reselecting different content is a new upload. */
export async function createAttachmentFingerprint(file: File, context: string): Promise<string> {
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(new Error('Failed to read attachment for retry identity.'));
    reader.readAsArrayBuffer(file);
  });
  return JSON.stringify([context, file.name, file.type, file.size, digest(new Uint8Array(bytes))]);
}

/** One history entry is one draft, not every future Create with the same text. */
export function createSubmissionScope(): string {
  const state = window.history.state;
  if (typeof state?.moonmindCreateIntent === 'string') return state.moonmindCreateIntent;
  const scope = newRequestId();
  try {
    window.history.replaceState({ ...state, moonmindCreateIntent: scope }, '');
  } catch { /* In-memory retry protection still works when history is unavailable. */ }
  return scope;
}

export class CreateSubmissionIntents {
  private readonly intents = new Map<string, CreateSubmissionIntent>();
  private readonly uploads = new Map<string, CreateAttachmentRef>();
  private readonly pendingUploads = new Map<string, Promise<CreateAttachmentRef>>();
  constructor(private readonly scope: string) {}

  private storageKey(fingerprint: string): string {
    return `moonmind.create-intent.${this.scope}.${digest(fingerprint)}`;
  }

  attachment(fingerprint: string): CreateAttachmentRef | null {
    const cached = this.uploads.get(fingerprint);
    if (cached) return cached;
    try {
      const stored = attachmentMetadata(JSON.parse(window.sessionStorage.getItem(`${this.storageKey(fingerprint)}.upload`) || 'null'));
      if (stored) this.uploads.set(fingerprint, stored);
      return stored;
    } catch { return null; }
  }

  prepareAttachment(fingerprint: string, upload: () => Promise<CreateAttachmentRef>): Promise<CreateAttachmentRef> {
    const cached = this.attachment(fingerprint);
    if (cached) return Promise.resolve(cached);
    const pending = this.pendingUploads.get(fingerprint);
    if (pending) return pending;
    // The picker can contain identical bytes with distinct modification times.
    // Share one prepared ref rather than letting concurrent completions change
    // the request's attachment identities between attempts.
    const prepared = upload().then((ref) => {
      // Back/Forward can reconstruct another store for this history entry
      // while this upload is pending. Never replace a ref it already used.
      const canonical = this.attachment(fingerprint);
      if (canonical) return canonical;
      this.saveAttachment(fingerprint, ref);
      return ref;
    }).finally(() => this.pendingUploads.delete(fingerprint));
    this.pendingUploads.set(fingerprint, prepared);
    return prepared;
  }

  saveAttachment(fingerprint: string, ref: CreateAttachmentRef): void {
    const metadata = attachmentMetadata(ref);
    if (!metadata) return;
    this.uploads.set(fingerprint, metadata);
    try { window.sessionStorage.setItem(`${this.storageKey(fingerprint)}.upload`, JSON.stringify(metadata)); }
    catch { /* Live retries still reuse the uploaded ref. */ }
  }

  forRequest(body: Record<string, unknown>): CreateSubmissionIntent {
    const fingerprint = JSON.stringify(body);
    const known = this.intents.get(fingerprint);
    if (known) return known;
    let intent: CreateSubmissionIntent | null = null;
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(this.storageKey(fingerprint)) || 'null');
      if (stored && typeof stored.key === 'string' && typeof stored.uncertain === 'boolean') {
        intent = {
          key: stored.key,
          uncertain: stored.uncertain,
          ...(stored.accepted && typeof stored.accepted === 'object' ? { accepted: createSubmissionResult(stored.accepted) } : {}),
          ...(typeof stored.inputArtifactRef === 'string' ? { inputArtifactRef: stored.inputArtifactRef } : {}),
          ...(typeof stored.scheduledFor === 'string' ? { scheduledFor: stored.scheduledFor } : {}),
          ...(stored.artifactLinks ? { artifactLinks: artifactLinkProgress(stored.artifactLinks) } : {}),
        };
      }
    } catch { /* Storage failure must not prevent authoring. */ }
    intent ??= { key: `create:${newRequestId()}`, uncertain: false };
    this.intents.set(fingerprint, intent);
    this.save(intent);
    return intent;
  }

  save(intent: CreateSubmissionIntent): void {
    for (const [fingerprint, candidate] of this.intents) {
      if (candidate !== intent) continue;
      try {
        const stored = JSON.parse(window.sessionStorage.getItem(this.storageKey(fingerprint)) || 'null');
        if (stored?.key === intent.key) {
          // Older handlers may finish after a reconstructed store has accepted
          // this request. Preserve confirmed progress rather than rewinding it.
          if (stored.accepted && typeof stored.accepted === 'object') intent.accepted = createSubmissionResult(stored.accepted);
          if (typeof stored.inputArtifactRef === 'string') intent.inputArtifactRef = stored.inputArtifactRef;
          if (typeof stored.scheduledFor === 'string') intent.scheduledFor = stored.scheduledFor;
          const links = artifactLinkProgress(stored.artifactLinks);
          for (const [key, state] of Object.entries(artifactLinkProgress(intent.artifactLinks))) {
            if (links[key] !== 'complete') links[key] = state;
          }
          if (Object.keys(links).length) intent.artifactLinks = links;
        }
        if (intent.accepted) intent.uncertain = false;
        window.sessionStorage.setItem(this.storageKey(fingerprint), JSON.stringify({
          key: intent.key,
          uncertain: intent.uncertain,
          ...(intent.accepted ? { accepted: createSubmissionResult(intent.accepted) } : {}),
          ...(intent.inputArtifactRef ? { inputArtifactRef: intent.inputArtifactRef } : {}),
          ...(intent.scheduledFor ? { scheduledFor: intent.scheduledFor } : {}),
          ...(intent.artifactLinks ? { artifactLinks: artifactLinkProgress(intent.artifactLinks) } : {}),
        }));
      } catch { /* Keep the live intent when tab storage is full or unavailable. */ }
      return;
    }
  }
}

export function createSubmissionDestination(created: CreateSubmissionResult): string {
  return String(created.redirectPath || '').trim() ||
    (created.definitionId ? `/schedules/${encodeURIComponent(created.definitionId)}` :
      created.workflowId ? `/workflows/${encodeURIComponent(created.workflowId)}?source=temporal` : '');
}
