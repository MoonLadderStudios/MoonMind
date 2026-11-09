import { readModelSelection } from "./modelSelection";
import { buildRemediationRuntimeRequestFields } from './workflowActions';

const DRAFT_STORAGE_PREFIX = 'moonmind.remediation-create-draft.';
const DRAFT_PRESENCE_PREFIX = 'moonmind.remediation-create-draft-presence.';
const MAX_REMEDIATION_STEP_SELECTORS = 25;
/** Only action policy a draft stored before #5157 can carry. */
const LEGACY_REMEDIATION_ACTION_POLICY = 'admin_healer_default';
export const REMEDIATION_CREATE_DRAFT_TTL_MS = 2 * 60 * 60 * 1000;
/** Skill that reads the remediation context MoonMind places in the workspace. */
export const REMEDIATION_SKILL_ID = 'remediate-workflow';

export type RemediationCreateDraftReadResult =
  | { status: 'valid'; draft: RemediationCreateDraft }
  | {
      status: 'missing' | 'cross_tab' | 'malformed' | 'expired';
      draft: null;
    };

export type SecurityFindingSelection = { comparisonArtifactRef: string; findingId: string };

export type RemediationCreateDraft = {
  source: 'remediation';
  schemaVersion: 1;
  createdAt: string;
  target: {
    workflowId: string;
    runId: string;
    securityFinding?: SecurityFindingSelection;
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
  publishMode?: string;
  executionProfileRef?: string;
  launchPolicyRef?: string;
  runtime?: {
    mode?: string;
    model?: string | null;
    effort?: string | null;
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
  /** Last authored submission, retained so a lost acknowledgment can be replayed. */
  authoring?: {
    request: Record<string, unknown>;
    publishMode: string;
    schedule: {
      mode: 'immediate' | 'once' | 'deferred_minutes' | 'recurring';
      scheduledFor: string;
      deferredMinutes: string;
      cron: string;
      timezone: string;
      name: string;
    };
  };
  /** Skill for the remediation step; the operator may change it before submit. */
  skill?: string;
  remediation: {
    target: {
      workflowId: string;
      runId: string;
      securityFinding?: SecurityFindingSelection;
      stepSelectors?: Array<Record<string, unknown>>;
      agentRunIds?: string[];
    };
    /**
     * New drafts author an observe-only snapshot: the Omnigent run receives
     * delivered evidence but no live-follow or typed-action tools. Other values
     * and the optional policies only appear in submissions retained from before
     * MoonLadderStudios/MoonMind#5157, replayed unchanged for idempotency.
     */
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

/**
 * What normal Remediate authors: the Omnigent run diagnoses from evidence
 * delivered into its workspace and works in its own repository. It receives no
 * live-follow or typed-action tools, so no follow mode or action policy is
 * offered (MoonLadderStudios/MoonMind#5157).
 */
const OBSERVE_ONLY_DIAGNOSIS = {
  mode: 'snapshot',
  authorityMode: 'observe_only',
} as const;

export function buildRemediationCreateDraft(
  execution: RemediationDraftExecution,
  options: {
    runId?: string;
    instructions?: string;
    securityFinding?: SecurityFindingSelection;
  } = {},
): RemediationCreateDraft {
  const workflowId = cleanText(execution.workflowId);
  const runId = cleanText(options.runId || execution.temporalRunId || execution.runId);
  if (!workflowId || !runId) {
    throw new Error('Remediation draft requires target workflow and run identity.');
  }
  if (options.securityFinding && !securityFindingIsValid(options.securityFinding)) {
    throw new Error('Security remediation requires comparison artifact and finding identity.');
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
    ...(options.securityFinding ? { securityFinding: { ...options.securityFinding } } : {}),
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
  // A scratch target has no repository; remediation keeps that source instead
  // of substituting one, and stays save-only so nothing targets a destination.
  const repository = cleanText(
    execution.repository || asRecord(repositoryParameters.repository).name,
  );
  const gitParameters = asRecord(workflowParameters.git || inputParameters.git);
  const omnigentParameters = asRecord(inputParameters.omnigent);
  const startingBranch = cleanText(
    gitParameters.startingBranch ||
      workflowParameters.startingBranch ||
      repositoryBranch.name ||
      inputParameters.startingBranch ||
      gitParameters.branch ||
      workflowParameters.branch ||
      inputParameters.branch,
  );
  const executionProfileRef = cleanText(
    omnigentParameters.executionTargetRef || runtime.executionProfileRef,
  );
  const launchPolicyRef = cleanText(
    omnigentParameters.launchPolicyRef || storedSnapshot.launchPolicyRef,
  );
  // Built-in vector retrieval retired (#4105): remediation drafts carry no
  // retrieval authoring. Historical payloads remain readable in detail views;
  // new remediation work uses explicit evidence refs, never rag/followUp.
  return {
    source: 'remediation',
    schemaVersion: 1,
    createdAt: new Date().toISOString(),
    target: {
      workflowId,
      runId,
      ...(options.securityFinding ? { securityFinding: { ...options.securityFinding } } : {}),
      ...(title ? { title } : {}),
      ...(state ? { state } : {}),
      ...(stepSelectors.length > 0 ? { stepSelectors } : {}),
    },
    repository,
    ...(startingBranch ? { branch: startingBranch, startingBranch } : {}),
    publishMode: repository && !options.securityFinding ? 'pr' : 'none',
    ...(executionProfileRef ? { executionProfileRef } : {}),
    ...(launchPolicyRef ? { launchPolicyRef } : {}),
    runtime: {
      ...(cleanText(runtime.mode) ? { mode: cleanText(runtime.mode) } : {}),
      ...readModelSelection(runtime),
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
      (options.securityFinding
        ? `Investigate security finding ${options.securityFinding.findingId} from the pinned comparison and original evidence in the remediation context. Preserve the hypothesis, scope, expected behavior and selected candidate. Assessment authority does not authorize edits, publication or cloud mutation. Use existing separately admitted authority for any fix and comparable retest; CI success alone is not a verified security fix. Infrastructure-as-code uses the ordinary code path; direct infrastructure mutation is unsupported.`
        : `Diagnose why workflow ${title || workflowId} failed using the remediation context in the workspace, fix the cause where it is safe to, and report how to keep it from recurring.`),
    skill: options.securityFinding ? 'security-investigate' : REMEDIATION_SKILL_ID,
    remediation: {
      target: remediationTarget,
      ...OBSERVE_ONLY_DIAGNOSIS,
      evidencePolicy: {
        includeStepLedger: true,
        includeDiagnostics: true,
        includeRecovery: true,
        includeIncident: true,
        includeCheckpointBranches: true,
        includeAdapterRefs: true,
        tailLines: 2000,
      },
      trigger: { type: 'manual' },
    },
  };
}

/**
 * A draft opened before #5157 but not yet submitted would otherwise carry
 * authority Create no longer shows. Reload it as the observe-only diagnosis
 * the form presents, keeping its pinned target and evidence choices.
 */
function withObserveOnlyAuthoring(draft: RemediationCreateDraft): RemediationCreateDraft {
  const {
    actionPolicyRef: _actionPolicyRef,
    approvalPolicy: _approvalPolicy,
    lockPolicy: _lockPolicy,
    verificationPolicy: _verificationPolicy,
    checkpointBranchPolicy: _checkpointBranchPolicy,
    ...remediation
  } = draft.remediation;
  return { ...draft, remediation: { ...remediation, ...OBSERVE_ONLY_DIAGNOSIS } };
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

function securityFindingIsValid(value: unknown): boolean {
  return value === undefined || (isRecordValue(value) &&
    isNonEmptyText(value.comparisonArtifactRef) && value.comparisonArtifactRef.startsWith('art_') &&
    isNonEmptyText(value.findingId));
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

function authoringIsValid(value: unknown, remediation: Record<string, unknown>): boolean {
  if (value === undefined) return true;
  if (!isRecordValue(value) || !isRecordValue(value.request) || !isRecordValue(value.schedule)) return false;
  const request = value.request;
  const schedule = value.schedule;
  const payload = asRecord(request.payload);
  const task = asRecord(payload.task);
  return request.type === 'task' &&
    typeof request.priority === 'number' && Number.isFinite(request.priority) &&
    typeof request.maxAttempts === 'number' && Number.isFinite(request.maxAttempts) &&
    isNonEmptyText(payload.targetRuntime) &&
    typeof task.instructions === 'string' &&
    optionalRecordIsValid(task.runtime) &&
    optionalRecordIsValid(task.tool) && optionalRecordIsValid(task.skill) &&
    (task.steps === undefined || (Array.isArray(task.steps) && task.steps.every((step) =>
      isRecordValue(step) && optionalTextIsValid(step.instructions) && optionalTextIsValid(step.title) &&
      optionalTextIsValid(step.id) && optionalRecordIsValid(step.runtime) &&
      optionalRecordIsValid(step.tool) && optionalRecordIsValid(step.skill),
    ))) &&
    (task.dependsOn === undefined || (Array.isArray(task.dependsOn) && task.dependsOn.every(isNonEmptyText))) &&
    JSON.stringify(task.remediation) === JSON.stringify(remediation) &&
    typeof value.publishMode === 'string' &&
    ['immediate', 'once', 'deferred_minutes', 'recurring'].includes(cleanText(schedule.mode)) &&
    ['scheduledFor', 'deferredMinutes', 'cron', 'timezone', 'name'].every(
      (key) => typeof schedule[key] === 'string',
    );
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
    typeof draft.repository === 'string' &&
    remediationTarget.workflowId === target.workflowId &&
    remediationTarget.runId === target.runId &&
    securityFindingIsValid(target.securityFinding) &&
    securityFindingIsValid(remediationTarget.securityFinding) &&
    JSON.stringify(target.securityFinding) === JSON.stringify(remediationTarget.securityFinding) &&
    stepSelectorsAreValid(target.stepSelectors) &&
    stepSelectorsAreValid(remediationTarget.stepSelectors) &&
    agentRunIdsAreValid(target.agentRunIds) &&
    agentRunIdsAreValid(remediationTarget.agentRunIds) &&
    optionalTextIsValid(draft.branch) &&
    optionalTextIsValid(draft.startingBranch) &&
    optionalTextIsValid(draft.publishMode) &&
    optionalTextIsValid(draft.executionProfileRef) &&
    optionalTextIsValid(draft.launchPolicyRef) &&
    optionalTextIsValid(draft.skill) &&
    authoringIsValid(draft.authoring, remediation) &&
    (
      remediation.actionPolicyRef === undefined ||
      remediation.actionPolicyRef === LEGACY_REMEDIATION_ACTION_POLICY
    ) &&
    optionalRecordIsValid(draft.runtime) &&
    optionalRecordIsValid(draft.agentProfile) &&
    (
      draft.agentProfile === undefined ||
      (isNonEmptyText(agentProfile.profileId) &&
        isNonEmptyText(agentProfile.providerProfileRef))
    ) &&
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
    // A retained submission may already be admitted; replay it as authored.
    return { status: 'valid', draft: parsed.authoring ? parsed : withObserveOnlyAuthoring(parsed) };
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

export function updateRemediationCreateDraft(draftId: string, draft: RemediationCreateDraft): void {
  const previous = inspectRemediationCreateDraft(draftId);
  if (previous.status !== 'valid' || !draftIsStructurallyValid(draft) ||
    previous.draft.target.workflowId !== draft.target.workflowId ||
    previous.draft.target.runId !== draft.target.runId ||
    previous.draft.createdAt !== draft.createdAt ||
    JSON.stringify(previous.draft.target.securityFinding) !== JSON.stringify(draft.target.securityFinding)) {
    throw new Error('The remediation draft is unavailable or changed. Open Remediate again before submitting.');
  }
  // Keep the original target pin, tab and expiry. A failed write stops before
  // admission rather than letting reload repeat an uncertain effect as new work.
  try {
    window.sessionStorage.setItem(storageKey(draftId), JSON.stringify(draft));
  } catch (cause) {
    throw new Error('The remediation draft could not be saved. Submission was not sent.', { cause });
  }
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
