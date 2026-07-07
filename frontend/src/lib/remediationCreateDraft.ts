import {
  buildRemediationRuntimeRequestFields,
  DEFAULT_REMEDIATION_ACTION_POLICY,
  DEFAULT_REMEDIATION_AUTHORITY,
  DEFAULT_REMEDIATION_MODE,
} from './workflowActions';

const DRAFT_STORAGE_PREFIX = 'moonmind.remediation-create-draft.';
const DEFAULT_REMEDIATION_REPOSITORY = 'MoonLadderStudios/MoonMind';

export type RemediationCreateDraft = {
  source: 'remediation';
  target: {
    workflowId: string;
    runId: string;
    title?: string;
    state?: string;
    stepSelectors?: Array<Record<string, unknown>>;
    agentRunIds?: string[];
  };
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
  steps?: Array<Record<string, unknown>> | null | undefined;
  stepLedger?: Array<Record<string, unknown>> | null | undefined;
  latestCheckpointRef?: string | null | undefined;
  checkpointRef?: string | null | undefined;
  checkpoints?: Array<Record<string, unknown>> | null | undefined;
  targetRuntime?: string | null | undefined;
  profileId?: string | null | undefined;
  model?: string | null | undefined;
  resolvedModel?: string | null | undefined;
  requestedModel?: string | null | undefined;
  effort?: string | null | undefined;
};

function cleanText(value: unknown): string {
  return String(value ?? '').trim();
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
  return {
    source: 'remediation',
    target: {
      workflowId,
      runId,
      ...(title ? { title } : {}),
      ...(state ? { state } : {}),
      ...(stepSelectors.length > 0 ? { stepSelectors } : {}),
    },
    repository: cleanText(execution.repository) || DEFAULT_REMEDIATION_REPOSITORY,
    publishMode: 'pr',
    runtime: {
      ...(cleanText(runtime.mode) ? { mode: cleanText(runtime.mode) } : {}),
      ...(cleanText(runtime.model) ? { model: cleanText(runtime.model) } : {}),
      ...(cleanText(runtime.effort) ? { effort: cleanText(runtime.effort) } : {}),
      ...(cleanText(runtime.profileId) ? { profileId: cleanText(runtime.profileId) } : {}),
    },
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

export function readRemediationCreateDraft(draftId: string | null | undefined): RemediationCreateDraft | null {
  const normalized = cleanText(draftId);
  if (!normalized) return null;
  const raw = window.sessionStorage.getItem(storageKey(normalized));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as RemediationCreateDraft;
    return parsed?.source === 'remediation' ? parsed : null;
  } catch {
    return null;
  }
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
