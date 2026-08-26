import {
  type ContextRetrievalAuthoring,
  parseContextRetrievalParameters,
} from './contextRetrievalAuthoring';
import {
  buildRemediationRuntimeRequestFields,
  DEFAULT_REMEDIATION_ACTION_POLICY,
  DEFAULT_REMEDIATION_AUTHORITY,
  DEFAULT_REMEDIATION_MODE,
} from './workflowActions';

const DRAFT_STORAGE_PREFIX = 'moonmind.remediation-create-draft.';
const DRAFT_PRESENCE_PREFIX = 'moonmind.remediation-create-draft-presence.';
const DEFAULT_REMEDIATION_REPOSITORY = 'MoonLadderStudios/MoonMind';
const MAX_REMEDIATION_STEP_SELECTORS = 25;
export const REMEDIATION_CREATE_DRAFT_TTL_MS = 2 * 60 * 60 * 1000;

export type RemediationCreateDraftReadResult =
  | { status: 'valid'; draft: RemediationCreateDraft }
  | {
      status: 'missing' | 'cross_tab' | 'malformed' | 'expired';
      draft: null;
    };

export type RemediationCreateDraft = {
  source: 'remediation';
  schemaVersion: 1;
  createdAt: string;
  target: {
    workflowId: string;
    runId: string;
    title?: string;
    state?: string;
    stepSelectors?: Array<Record<string, unknown>>;
    agentRunIds?: string[];
  };
  repository: string;
  /** Source branch used to create the remediation workflow workspace. */
  branch?: string;
  /** Explicit alias shown in the remediation authoring summary. */
  startingBranch?: string;
  /** Isolated Checkpoint Branch work branch, distinct from the source branch. */
  workBranch?: string;
  publishMode?: string;
  executionProfileRef?: string;
  launchPolicyRef?: string;
  contextRetrieval?: ContextRetrievalAuthoring;
  runtime?: {
    mode?: string;
    model?: string;
    effort?: string;
    modelTier?: number;
    tierFallback?: 'clamp' | 'strict';
    profileId?: string;
  };
  agentProfile?: {
    profileId: string;
    version?: number;
    providerProfileRef: string;
  };
  instructions?: string;
  remediation: {
    target: {
      workflowId: string;
      runId: string;
      stepSelectors?: Array<Record<string, unknown>>;
      agentRunIds?: string[];
    };
    mode: 'snapshot' | 'live_follow' | 'snapshot_then_follow';
    authorityMode: 'observe_only' | 'approval_gated' | 'admin_auto';
    actionPolicyRef?: string;
    evidencePolicy?: Record<string, unknown>;
    approvalPolicy?: Record<string, unknown>;
    lockPolicy?: Record<string, unknown>;
    verificationPolicy?: Record<string, unknown>;
    checkpointBranchPolicy?: Record<string, unknown>;
    trigger: { type: 'manual' };
  };
};

type RemediationDraftExecution = {
  workflowId?: string | null | undefined;
  runId?: string | null | undefined;
  temporalRunId?: string | null | undefined;
  title?: string | null | undefined;
  repository?: string | null | undefined;
  state?: string | null | undefined;
  rawState?: string | null | undefined;
  status?: string | null | undefined;
  resume?: {
    checkpointRef?: string | null | undefined;
    sourceRunId?: string | null | undefined;
  } | null | undefined;
  inputParameters?: Record<string, unknown> | null | undefined;
  steps?: Array<Record<string, unknown>> | null | undefined;
  stepLedger?: Array<Record<string, unknown>> | null | undefined;
  latestCheckpointRef?: string | null | undefined;
  checkpointRef?: string | null | undefined;
  checkpoints?: Array<Record<string, unknown>> | null | undefined;
  targetRuntime?: string | null | undefined;
  profileId?: string | null | undefined;
  agentProfile?: {
    profileId?: string | null | undefined;
    version?: number | null | undefined;
    providerProfileRef?: string | null | undefined;
  } | null | undefined;
  model?: string | null | undefined;
  resolvedModel?: string | null | undefined;
  requestedModel?: string | null | undefined;
  effort?: string | null | undefined;
};

function cleanText(value: unknown): string {
  return String(value ?? '').trim();
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function remediationMode(value: unknown): RemediationCreateDraft['remediation']['mode'] {
  const normalized = cleanText(value);
  if (
    normalized === 'snapshot' ||
    normalized === 'live_follow' ||
    normalized === 'snapshot_then_follow'
  ) {
    return normalized;
  }
  return DEFAULT_REMEDIATION_MODE;
}

function remediationAuthorityMode(
  value: unknown,
): RemediationCreateDraft['remediation']['authorityMode'] {
  const normalized = cleanText(value);
  if (
    normalized === 'observe_only' ||
    normalized === 'approval_gated' ||
    normalized === 'admin_auto'
  ) {
    return normalized;
  }
  return DEFAULT_REMEDIATION_AUTHORITY;
}

function storageKey(draftId: string): string {
  return `${DRAFT_STORAGE_PREFIX}${draftId}`;
}

function presenceKey(draftId: string): string {
  return `${DRAFT_PRESENCE_PREFIX}${draftId}`;
}

function randomDraftId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function checkpointSelectors(execution: RemediationDraftExecution): Array<Record<string, unknown>> {
  const selectors: Array<Record<string, unknown>> = [];
  const add = (candidate: Record<string, unknown>, source: string) => {
    const checkpointRef = cleanText(
      candidate.checkpointRef ||
      candidate.checkpoint_ref ||
      candidate.stateCheckpointRef ||
      candidate.stepCheckpointRef,
    );
    if (!checkpointRef) return;
    selectors.push({
      source,
      checkpointRef,
      ...(cleanText(candidate.logicalStepId || candidate.stepId)
        ? { logicalStepId: cleanText(candidate.logicalStepId || candidate.stepId) }
        : {}),
      ...(cleanText(candidate.checkpointDigest)
        ? { checkpointDigest: cleanText(candidate.checkpointDigest) }
        : {}),
    });
  };

  if (execution.resume?.checkpointRef) {
    selectors.push({
      source: 'resume',
      checkpointRef: execution.resume.checkpointRef,
    });
  }
  for (const item of execution.checkpoints || []) add(item, 'checkpoint');
  for (const item of execution.stepLedger || []) add(item, 'step_ledger');
  for (const item of execution.steps || []) add(item, 'step');
  const directCheckpoint = cleanText(execution.latestCheckpointRef || execution.checkpointRef);
  if (directCheckpoint) selectors.push({ source: 'execution', checkpointRef: directCheckpoint });

  const seen = new Set<string>();
  return selectors
    .filter((selector) => {
      const key = cleanText(selector.checkpointRef);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, MAX_REMEDIATION_STEP_SELECTORS);
}

export function buildRemediationCreateDraft(
  execution: RemediationDraftExecution,
  options: {
    mode?: RemediationCreateDraft['remediation']['mode'] | string;
    authorityMode?: RemediationCreateDraft['remediation']['authorityMode'] | string;
    actionPolicyRef?: string;
    runId?: string;
    instructions?: string;
  } = {},
): RemediationCreateDraft {
  const workflowId = cleanText(execution.workflowId);
  const runId = cleanText(options.runId || execution.temporalRunId || execution.runId);
  if (!workflowId || !runId) {
    throw new Error('Remediation draft requires target workflow and run identity.');
  }
  const runtimeFields = buildRemediationRuntimeRequestFields(execution);
  const runtime = (
    runtimeFields.runtime &&
    typeof runtimeFields.runtime === 'object' &&
    !Array.isArray(runtimeFields.runtime)
      ? runtimeFields.runtime as Record<string, unknown>
      : {}
  );
  const stepSelectors = checkpointSelectors(execution);
  const title = cleanText(execution.title);
  const state = cleanText(execution.state || execution.rawState || execution.status);
  const remediationTarget = {
    workflowId,
    runId,
    ...(stepSelectors.length > 0 ? { stepSelectors } : {}),
  };
  const storedAgentProfile = (
    execution.inputParameters?.agentProfile &&
    typeof execution.inputParameters.agentProfile === 'object' &&
    !Array.isArray(execution.inputParameters.agentProfile)
      ? execution.inputParameters.agentProfile as Record<string, unknown>
      : {}
  );
  const storedSnapshot = (
    execution.inputParameters?.agentProfileSnapshot &&
    typeof execution.inputParameters.agentProfileSnapshot === 'object' &&
    !Array.isArray(execution.inputParameters.agentProfileSnapshot)
      ? execution.inputParameters.agentProfileSnapshot as Record<string, unknown>
      : {}
  );
  const selectedAgentProfileId = cleanText(
    execution.agentProfile?.profileId || storedAgentProfile.profileId || storedSnapshot.profileId,
  );
  const selectedProviderProfileRef = cleanText(
    execution.agentProfile?.providerProfileRef || storedSnapshot.providerProfileRef || execution.profileId,
  );
  const selectedAgentProfileVersion = Number(
    execution.agentProfile?.version || storedAgentProfile.version || storedSnapshot.version || 0,
  );
  const inputParameters = asRecord(execution.inputParameters);
  const workflowParameters = asRecord(
    inputParameters.workflow || inputParameters.task,
  );
  const repositoryParameters = asRecord(inputParameters.repository);
  const repositoryBranch = asRecord(repositoryParameters.branch);
  const gitParameters = asRecord(workflowParameters.git || inputParameters.git);
  const omnigentParameters = asRecord(inputParameters.omnigent);
  const remediationParameters = asRecord(workflowParameters.remediation);
  const checkpointBranchPolicy = asRecord(
    remediationParameters.checkpointBranchPolicy,
  );
  const startingBranch = cleanText(
    gitParameters.startingBranch ||
      workflowParameters.startingBranch ||
      repositoryBranch.name ||
      inputParameters.startingBranch ||
      gitParameters.branch ||
      workflowParameters.branch ||
      inputParameters.branch,
  );
  const workBranch = cleanText(
    checkpointBranchPolicy.gitWorkBranch ||
      gitParameters.workBranch ||
      inputParameters.gitWorkBranch,
  );
  const executionProfileRef = cleanText(
    omnigentParameters.executionTargetRef || runtime.executionProfileRef,
  );
  const launchPolicyRef = cleanText(
    omnigentParameters.launchPolicyRef || storedSnapshot.launchPolicyRef,
  );
  const contextRetrieval = parseContextRetrievalParameters(inputParameters);
  return {
    source: 'remediation',
    schemaVersion: 1,
    createdAt: new Date().toISOString(),
    target: {
      workflowId,
      runId,
      ...(title ? { title } : {}),
      ...(state ? { state } : {}),
      ...(stepSelectors.length > 0 ? { stepSelectors } : {}),
    },
    repository: cleanText(execution.repository) || DEFAULT_REMEDIATION_REPOSITORY,
    ...(startingBranch ? { branch: startingBranch, startingBranch } : {}),
    ...(workBranch ? { workBranch } : {}),
    publishMode: 'pr',
    ...(executionProfileRef ? { executionProfileRef } : {}),
    ...(launchPolicyRef ? { launchPolicyRef } : {}),
    ...(contextRetrieval ? { contextRetrieval } : {}),
    runtime: {
      ...(cleanText(runtime.mode) ? { mode: cleanText(runtime.mode) } : {}),
      ...(cleanText(runtime.model) ? { model: cleanText(runtime.model) } : {}),
      ...(cleanText(runtime.effort) ? { effort: cleanText(runtime.effort) } : {}),
      ...(cleanText(runtime.profileId) ? { profileId: cleanText(runtime.profileId) } : {}),
    },
    ...(selectedAgentProfileId && selectedProviderProfileRef
      ? {
          agentProfile: {
            profileId: selectedAgentProfileId,
            ...(selectedAgentProfileVersion > 0
              ? { version: selectedAgentProfileVersion }
              : {}),
            providerProfileRef: selectedProviderProfileRef,
          },
        }
      : {}),
    instructions:
      options.instructions ||
      `Investigate and remediate target execution ${workflowId} using bounded evidence.`,
    remediation: {
      target: remediationTarget,
      mode: remediationMode(options.mode),
      authorityMode: remediationAuthorityMode(options.authorityMode),
      actionPolicyRef: cleanText(options.actionPolicyRef || DEFAULT_REMEDIATION_ACTION_POLICY),
      evidencePolicy: {
        includeStepLedger: true,
        includeDiagnostics: true,
        includeRecovery: true,
        includeIncident: true,
        includeCheckpointBranches: true,
        includeAdapterRefs: true,
        tailLines: 2000,
      },
      approvalPolicy: {
        requiredForHighRisk: true,
      },
      lockPolicy: {
        targetMutationLock: true,
      },
      verificationPolicy: {
        verifyAppliedActions: true,
      },
      checkpointBranchPolicy: {
        actionKind: 'checkpoint_branch.create_from_remediation_context',
        runtimeContextPolicy: 'fresh_agent_run',
        workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
        ...(workBranch ? { gitWorkBranch: workBranch } : {}),
      },
      trigger: { type: 'manual' },
    },
  };
}

export function storeRemediationCreateDraft(draft: RemediationCreateDraft): string {
  const draftId = randomDraftId();
  const storedDraft = {
    ...draft,
    schemaVersion: 1 as const,
    createdAt: draft.createdAt || new Date().toISOString(),
  };
  window.sessionStorage.setItem(storageKey(draftId), JSON.stringify(storedDraft));
  // Session storage intentionally keeps the draft body tab-scoped. This
  // non-sensitive local marker lets a copied URL report the cross-tab failure
  // precisely without exposing target identity or authored repair content.
  window.localStorage.setItem(
    presenceKey(draftId),
    JSON.stringify({ createdAt: storedDraft.createdAt }),
  );
  return draftId;
}

function isNonEmptyText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function optionalRecordIsValid(value: unknown): boolean {
  return value === undefined || isRecordValue(value);
}

function optionalTextIsValid(value: unknown): boolean {
  return value === undefined || typeof value === 'string';
}

function stepSelectorsAreValid(value: unknown): boolean {
  if (value === undefined) return true;
  if (!Array.isArray(value) || value.length > MAX_REMEDIATION_STEP_SELECTORS) return false;
  return value.every((item) => {
    if (!isRecordValue(item)) return false;
    const logicalStepId = cleanText(item.logicalStepId || item.stepId);
    const checkpointRef = cleanText(item.checkpointRef || item.checkpoint_ref);
    const agentRunId = cleanText(item.agentRunId || item.agent_run_id);
    return Boolean(logicalStepId || checkpointRef || agentRunId || cleanText(item.source));
  });
}

function agentRunIdsAreValid(value: unknown): boolean {
  return value === undefined || (
    Array.isArray(value) &&
    value.length <= 25 &&
    value.every(isNonEmptyText)
  );
}

function createdAtMillis(value: unknown): number | null {
  if (!isNonEmptyText(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function draftIsStructurallyValid(value: unknown): value is RemediationCreateDraft {
  const draft = asRecord(value);
  const target = asRecord(draft.target);
  const remediation = asRecord(draft.remediation);
  const remediationTarget = asRecord(remediation.target);
  const trigger = asRecord(remediation.trigger);
  const agentProfile = asRecord(draft.agentProfile);
  return (
    draft.source === 'remediation' &&
    draft.schemaVersion === 1 &&
    createdAtMillis(draft.createdAt) !== null &&
    isNonEmptyText(target.workflowId) &&
    isNonEmptyText(target.runId) &&
    isNonEmptyText(draft.repository) &&
    remediationTarget.workflowId === target.workflowId &&
    remediationTarget.runId === target.runId &&
    stepSelectorsAreValid(target.stepSelectors) &&
    stepSelectorsAreValid(remediationTarget.stepSelectors) &&
    agentRunIdsAreValid(target.agentRunIds) &&
    agentRunIdsAreValid(remediationTarget.agentRunIds) &&
    optionalTextIsValid(draft.branch) &&
    optionalTextIsValid(draft.startingBranch) &&
    optionalTextIsValid(draft.workBranch) &&
    optionalTextIsValid(draft.publishMode) &&
    optionalTextIsValid(draft.executionProfileRef) &&
    optionalTextIsValid(draft.launchPolicyRef) &&
    (
      remediation.actionPolicyRef === undefined ||
      remediation.actionPolicyRef === DEFAULT_REMEDIATION_ACTION_POLICY
    ) &&
    optionalRecordIsValid(draft.runtime) &&
    optionalRecordIsValid(draft.agentProfile) &&
    (
      draft.agentProfile === undefined ||
      (isNonEmptyText(agentProfile.profileId) &&
        isNonEmptyText(agentProfile.providerProfileRef))
    ) &&
    optionalRecordIsValid(draft.contextRetrieval) &&
    optionalRecordIsValid(remediation.evidencePolicy) &&
    optionalRecordIsValid(remediation.approvalPolicy) &&
    optionalRecordIsValid(remediation.lockPolicy) &&
    optionalRecordIsValid(remediation.verificationPolicy) &&
    optionalRecordIsValid(remediation.checkpointBranchPolicy) &&
    ['snapshot', 'live_follow', 'snapshot_then_follow'].includes(
      cleanText(remediation.mode),
    ) &&
    ['observe_only', 'approval_gated', 'admin_auto'].includes(
      cleanText(remediation.authorityMode),
    ) &&
    trigger.type === 'manual'
  );
}

function inspectPresenceMarker(
  draftId: string,
  now: number,
): 'cross_tab' | 'expired' | 'missing' {
  const raw = window.localStorage.getItem(presenceKey(draftId));
  if (!raw) return 'missing';
  try {
    const createdAt = createdAtMillis(asRecord(JSON.parse(raw)).createdAt);
    if (createdAt === null) {
      window.localStorage.removeItem(presenceKey(draftId));
      return 'missing';
    }
    if (now - createdAt > REMEDIATION_CREATE_DRAFT_TTL_MS) {
      window.localStorage.removeItem(presenceKey(draftId));
      return 'expired';
    }
    return 'cross_tab';
  } catch {
    window.localStorage.removeItem(presenceKey(draftId));
    return 'missing';
  }
}

export function inspectRemediationCreateDraft(
  draftId: string | null | undefined,
  now = Date.now(),
): RemediationCreateDraftReadResult {
  const normalized = cleanText(draftId);
  if (!normalized) return { status: 'missing', draft: null };
  const raw = window.sessionStorage.getItem(storageKey(normalized));
  if (!raw) {
    return { status: inspectPresenceMarker(normalized, now), draft: null };
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!draftIsStructurallyValid(parsed)) {
      return { status: 'malformed', draft: null };
    }
    const createdAt = createdAtMillis(parsed.createdAt);
    if (
      createdAt === null ||
      createdAt > now + 5 * 60 * 1000
    ) {
      return { status: 'malformed', draft: null };
    }
    if (now - createdAt > REMEDIATION_CREATE_DRAFT_TTL_MS) {
      clearRemediationCreateDraft(normalized);
      return { status: 'expired', draft: null };
    }
    return { status: 'valid', draft: parsed };
  } catch {
    return { status: 'malformed', draft: null };
  }
}

export function readRemediationCreateDraft(
  draftId: string | null | undefined,
): RemediationCreateDraft | null {
  const result = inspectRemediationCreateDraft(draftId);
  return result.status === 'valid' ? result.draft : null;
}

export function clearRemediationCreateDraft(draftId: string | null | undefined): void {
  const normalized = cleanText(draftId);
  if (normalized) {
    window.sessionStorage.removeItem(storageKey(normalized));
    window.localStorage.removeItem(presenceKey(normalized));
  }
}

export function remediationCreateDraftHref(draftId: string): string {
  return `/workflows/new?intent=remediate&draftId=${encodeURIComponent(draftId)}`;
}
