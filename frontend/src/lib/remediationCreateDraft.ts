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
const DEFAULT_REMEDIATION_REPOSITORY = 'MoonLadderStudios/MoonMind';

/**
 * Remediation create drafts are short-lived, single-hop handoffs from a target
 * Workflow Detail page into the normal Create page. They expire so a stale
 * session-storage entry (for example an abandoned tab reopened a day later)
 * cannot silently prepopulate the Create page against a target run that has
 * since changed. Twelve hours is generous for an operator who steps away
 * mid-authoring while still bounding the pinned-target staleness window.
 */
export const REMEDIATION_DRAFT_TTL_MS = 12 * 60 * 60 * 1000;

export type RemediationDraftLoadStatus =
  | 'available'
  | 'missing'
  | 'malformed'
  | 'expired';

export type RemediationDraftLoadResult = {
  status: RemediationDraftLoadStatus;
  draft: RemediationCreateDraft | null;
};

export type RemediationCreateDraft = {
  source: 'remediation';
  /**
   * Epoch milliseconds when the draft was built. Used to expire stale drafts
   * before they prepopulate the Create page. Optional so older/hand-authored
   * drafts remain readable, but the builder always sets it.
   */
  createdAt?: number;
  target: {
    workflowId: string;
    runId: string;
    title?: string;
    state?: string;
    stepSelectors?: Array<Record<string, unknown>>;
    agentRunIds?: string[];
  };
  repository: string;
  /** Editable work branch for the remediation run. */
  branch?: string;
  /** Editable starting/base branch the remediation branches from. */
  startingBranch?: string;
  publishMode?: string;
  /** Omnigent execution profile authored on the target run. */
  executionProfileRef?: string;
  /** Omnigent launch policy authored on the target run. */
  launchPolicyRef?: string;
  /**
   * Retrieval/context controls authored on the target run, already normalized
   * into the Create page's authoring shape so it can be applied directly.
   */
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
  return selectors.filter((selector) => {
    const key = cleanText(selector.checkpointRef);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
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
  const omnigentParams = asRecord(inputParameters.omnigent);
  const gitParams = asRecord(inputParameters.git);
  const taskParams = asRecord(inputParameters.task);
  const executionProfileRef = cleanText(omnigentParams.executionTargetRef);
  const launchPolicyRef = cleanText(omnigentParams.launchPolicyRef);
  const workBranch = cleanText(
    inputParameters.branch || gitParams.branch || taskParams.branch,
  );
  const startingBranch = cleanText(
    inputParameters.startingBranch ||
      inputParameters.baseBranch ||
      gitParams.baseBranch ||
      gitParams.startingBranch,
  );
  const contextRetrieval = parseContextRetrievalParameters(inputParameters);
  return {
    source: 'remediation',
    createdAt: Date.now(),
    target: {
      workflowId,
      runId,
      ...(title ? { title } : {}),
      ...(state ? { state } : {}),
      ...(stepSelectors.length > 0 ? { stepSelectors } : {}),
    },
    repository: cleanText(execution.repository) || DEFAULT_REMEDIATION_REPOSITORY,
    ...(workBranch ? { branch: workBranch } : {}),
    ...(startingBranch ? { startingBranch } : {}),
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
      },
      trigger: { type: 'manual' },
    },
  };
}

export function storeRemediationCreateDraft(draft: RemediationCreateDraft): string {
  const draftId = randomDraftId();
  window.sessionStorage.setItem(storageKey(draftId), JSON.stringify(draft));
  return draftId;
}

/**
 * Load a remediation draft with an explicit availability status so the Create
 * page can surface an actionable, safe error for each failure mode:
 * - `missing`: no draft under this id (cleared, or opened in another tab/session);
 * - `malformed`: present but not a valid remediation draft (tampered/corrupt);
 * - `expired`: present but older than {@link REMEDIATION_DRAFT_TTL_MS};
 * - `available`: a valid, fresh draft.
 * Expired drafts are removed on read so they cannot be reapplied later.
 */
export function loadRemediationCreateDraft(
  draftId: string | null | undefined,
): RemediationDraftLoadResult {
  const normalized = cleanText(draftId);
  if (!normalized) return { status: 'missing', draft: null };
  const raw = window.sessionStorage.getItem(storageKey(normalized));
  if (!raw) return { status: 'missing', draft: null };
  let parsed: RemediationCreateDraft;
  try {
    parsed = JSON.parse(raw) as RemediationCreateDraft;
  } catch {
    return { status: 'malformed', draft: null };
  }
  if (!parsed || parsed.source !== 'remediation') {
    return { status: 'malformed', draft: null };
  }
  const createdAt = Number(parsed.createdAt);
  if (Number.isFinite(createdAt) && createdAt > 0) {
    if (Date.now() - createdAt > REMEDIATION_DRAFT_TTL_MS) {
      window.sessionStorage.removeItem(storageKey(normalized));
      return { status: 'expired', draft: null };
    }
  }
  return { status: 'available', draft: parsed };
}

export function readRemediationCreateDraft(
  draftId: string | null | undefined,
): RemediationCreateDraft | null {
  const result = loadRemediationCreateDraft(draftId);
  return result.status === 'available' ? result.draft : null;
}

export function clearRemediationCreateDraft(draftId: string | null | undefined): void {
  const normalized = cleanText(draftId);
  if (normalized) {
    window.sessionStorage.removeItem(storageKey(normalized));
  }
}

export function remediationCreateDraftHref(draftId: string): string {
  return `/workflows/new?intent=remediate&draftId=${encodeURIComponent(draftId)}`;
}
