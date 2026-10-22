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
}

// Like saved-result continuation identities, this digest works on HTTP LAN
// origins without SubtleCrypto. Only identity metadata, never the draft body,
// goes into sessionStorage. The random key is the server's admission identity.
function digest(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x01000193 ^ value.length;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x5bd1e995);
  }
  return [first, second].map((lane) => (lane >>> 0).toString(16).padStart(8, '0')).join('');
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
  constructor(private readonly scope: string) {}

  private storageKey(fingerprint: string): string {
    return `moonmind.create-intent.${this.scope}.${digest(fingerprint)}`;
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
        window.sessionStorage.setItem(this.storageKey(fingerprint), JSON.stringify({
          key: intent.key,
          uncertain: intent.uncertain,
          ...(intent.accepted ? { accepted: createSubmissionResult(intent.accepted) } : {}),
          ...(intent.inputArtifactRef ? { inputArtifactRef: intent.inputArtifactRef } : {}),
          ...(intent.scheduledFor ? { scheduledFor: intent.scheduledFor } : {}),
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
