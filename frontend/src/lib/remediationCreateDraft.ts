import {
  DEFAULT_REMEDIATION_ACTION_POLICY,
  DEFAULT_REMEDIATION_AUTHORITY,
  DEFAULT_REMEDIATION_MODE,
} from './workflowActions';

const STORAGE_PREFIX = 'moonmind.remediationDraft.';
const DEFAULT_REPOSITORY = 'MoonLadderStudios/MoonMind';
const DRAFT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

type UnknownRecord = Record<string, unknown>;

export type RemediationTarget = {
  workflowId: string;
  runId: string;
  title?: string;
  state?: string;
  stepSelectors?: UnknownRecord[];
  agentRunIds?: string[];
};

export type RemediationCreateDraft = {
  draftId?: string;
  source: 'remediation';
  createdAt: string;
  target: RemediationTarget;
  repository: string;
  branch?: string;
  publishMode?: string;
  runtime?: {
    mode?: string;
    model?: string;
    effort?: string;
    profileId?: string;
  };
  instructions?: string;
  remediation: {
    target: RemediationTarget;
    mode: 'snapshot' | 'live_follow' | 'snapshot_then_follow';
    authorityMode: 'observe_only' | 'approval_gated' | 'admin_auto';
    actionPolicyRef?: string;
    evidencePolicy?: UnknownRecord;
    checkpointBranchPolicy?: UnknownRecord;
    trigger: { type: 'manual' };
  };
};

export type RemediationCreateDraftOptions = {
  now?: Date;
  repository?: string | null;
  branch?: string | null;
  publishMode?: string | null;
  instructions?: string | null;
  stepSelectors?: UnknownRecord[];
  agentRunIds?: string[];
  evidencePolicy?: UnknownRecord;
  checkpointBranchPolicy?: UnknownRecord;
};

function recordValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function optionalString(value: unknown): string | undefined {
  const normalized = stringValue(value);
  return normalized || undefined;
}

function storageKey(draftId: string): string {
  return `${STORAGE_PREFIX}${draftId}`;
}

function createDraftId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  return `draft-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function readRunId(executionDetail: UnknownRecord): string {
  return (
    stringValue(executionDetail.runId) ||
    stringValue(executionDetail.temporalRunId) ||
    stringValue(executionDetail.latestRunId)
  );
}

function readWorkflowId(executionDetail: UnknownRecord): string {
  return stringValue(executionDetail.workflowId) || stringValue(executionDetail.id);
}

function readRuntime(executionDetail: UnknownRecord): RemediationCreateDraft['runtime'] {
  const runtime = recordValue(executionDetail.runtime);
  const mode = stringValue(runtime.mode) || stringValue(executionDetail.targetRuntime);
  const model =
    stringValue(runtime.model) ||
    stringValue(executionDetail.model) ||
    stringValue(executionDetail.resolvedModel) ||
    stringValue(executionDetail.requestedModel);
  const effort = stringValue(runtime.effort) || stringValue(executionDetail.effort);
  const profileId =
    stringValue(runtime.profileId) ||
    stringValue(runtime.providerProfile) ||
    stringValue(executionDetail.profileId);
  const value = {
    ...(mode ? { mode } : {}),
    ...(model ? { model } : {}),
    ...(effort ? { effort } : {}),
    ...(profileId ? { profileId } : {}),
  };
  return Object.keys(value).length > 0 ? value : undefined;
}

function normalizeDraft(value: unknown): RemediationCreateDraft | null {
  const draft = recordValue(value);
  if (draft.source !== 'remediation') return null;
  const target = recordValue(draft.target);
  const remediation = recordValue(draft.remediation);
  const remediationTarget = recordValue(remediation.target);
  const workflowId = stringValue(target.workflowId);
  const runId = stringValue(target.runId);
  const remediationWorkflowId = stringValue(remediationTarget.workflowId);
  const remediationRunId = stringValue(remediationTarget.runId);
  const createdAt = stringValue(draft.createdAt);
  const createdAtTime = Date.parse(createdAt);
  if (
    !workflowId ||
    !runId ||
    workflowId !== remediationWorkflowId ||
    runId !== remediationRunId ||
    !Number.isFinite(createdAtTime) ||
    Date.now() - createdAtTime > DRAFT_MAX_AGE_MS
  ) {
    return null;
  }
  return draft as RemediationCreateDraft;
}

export function buildRemediationCreateDraft(
  executionDetail: unknown,
  options: RemediationCreateDraftOptions = {},
): RemediationCreateDraft {
  const detail = recordValue(executionDetail);
  const workflowId = readWorkflowId(detail);
  const runId = readRunId(detail);
  if (!workflowId || !runId) {
    throw new Error('Target workflow and run are required to create a remediation draft.');
  }

  const target: RemediationTarget = {
    workflowId,
    runId,
    ...(optionalString(detail.title) ? { title: optionalString(detail.title) } : {}),
    ...(optionalString(detail.state || detail.rawState || detail.status)
      ? { state: optionalString(detail.state || detail.rawState || detail.status) }
      : {}),
    ...(options.stepSelectors && options.stepSelectors.length > 0
      ? { stepSelectors: options.stepSelectors }
      : {}),
    ...(options.agentRunIds && options.agentRunIds.length > 0
      ? { agentRunIds: options.agentRunIds }
      : {}),
  };
  const createdAt = (options.now || new Date()).toISOString();
  const repository =
    stringValue(options.repository) || stringValue(detail.repository) || DEFAULT_REPOSITORY;
  const runtime = readRuntime(detail);

  return {
    source: 'remediation',
    createdAt,
    target,
    repository,
    ...(optionalString(options.branch) ? { branch: optionalString(options.branch) } : {}),
    ...(optionalString(options.publishMode)
      ? { publishMode: optionalString(options.publishMode) }
      : {}),
    ...(runtime ? { runtime } : {}),
    ...(optionalString(options.instructions) ? { instructions: optionalString(options.instructions) } : {}),
    remediation: {
      target,
      mode: DEFAULT_REMEDIATION_MODE,
      authorityMode: DEFAULT_REMEDIATION_AUTHORITY,
      actionPolicyRef: DEFAULT_REMEDIATION_ACTION_POLICY,
      evidencePolicy: options.evidencePolicy ?? {},
      checkpointBranchPolicy: options.checkpointBranchPolicy ?? {},
      trigger: { type: 'manual' },
    },
  };
}

export function storeRemediationCreateDraft(draft: RemediationCreateDraft): string {
  const draftId = draft.draftId || createDraftId();
  window.sessionStorage.setItem(storageKey(draftId), JSON.stringify({ ...draft, draftId }));
  return draftId;
}

export function loadRemediationCreateDraft(draftId: string): RemediationCreateDraft | null {
  const normalizedId = draftId.trim();
  if (!normalizedId) return null;
  try {
    const raw = window.sessionStorage.getItem(storageKey(normalizedId));
    if (!raw) return null;
    return normalizeDraft(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function clearRemediationCreateDraft(draftId: string): void {
  const normalizedId = draftId.trim();
  if (!normalizedId) return;
  window.sessionStorage.removeItem(storageKey(normalizedId));
}

export function consumeRemediationCreateDraft(draftId: string): RemediationCreateDraft | null {
  const draft = loadRemediationCreateDraft(draftId);
  clearRemediationCreateDraft(draftId);
  return draft;
}

export function remediationCreateDraftUrl(draftId: string): string {
  return `/workflows/new?intent=remediate&draftId=${encodeURIComponent(draftId)}`;
}
