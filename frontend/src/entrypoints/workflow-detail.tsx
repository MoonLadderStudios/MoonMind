import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Anser from 'anser';
import { Virtuoso } from 'react-virtuoso';
import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import {
  ExecutionStatusPill,
  StepExecutionStatusPill,
  StepLedgerStatusPill,
  WorkflowLifecycleStatusPill,
} from '../components/ExecutionStatusPill';
import { resolveWorkflowDisplayStatus } from '../status/workflowStatus';
import { DashboardActionDialog } from '../components/DashboardActionDialog';
import { EntityDetailFrame } from '../components/EntityDetailFrame';
import { CollectionWorkspace } from '../components/CollectionWorkspace';
import {
  DashboardToastProvider,
  useDashboardToast,
} from '../components/dashboard/DashboardToast';
import { executionStatusPillProps } from '../utils/executionStatusPillClasses';
import { CANONICAL_STEP_STATUSES, StatusIcon } from '../utils/statusIcons';
import { SkillProvenanceBadge } from '../components/skills/SkillProvenanceBadge';
import { LogPanel } from '../components/dashboard/LogPanel';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { formatDurationMs, formatRuntimeLabel, formatStatusLabel } from '../utils/formatters';
import {
  readDashboardPreferences,
  updateDashboardPreferences,
} from '../utils/dashboardPreferences';
import {
  recordTemporalTaskEditingClientEvent,
  taskCompareHref,
  taskEditForRerunHref,
  taskEditHref,
} from '../lib/temporalTaskEditing';
import { navigateTo as navigateToDashboardRoute } from '../lib/navigation';
import {
  readWorkflowListDisplayMode,
  type CollectionListDisplayMode,
} from '../lib/collectionListDisplayMode';
import {
  WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS,
  WORKFLOW_SIDEBAR_ROUTE_ICON_ANIMATION_MS,
  WorkflowWorkspaceSidebarPanel,
} from '../components/workflows/WorkflowWorkspaceSidebar';
import { workflowWorkspaceRowFromDetail } from '../lib/workflowWorkspaceList';
import { WorkflowActionsMenu } from '../components/WorkflowActionsMenu';
import {
  buildWorkflowActionMenuItems,
  DEFAULT_REMEDIATION_ACTION_POLICY,
  DEFAULT_REMEDIATION_AUTHORITY,
  DEFAULT_REMEDIATION_MODE,
  ExecutionActionsSchema,
  isRemediationEligibleTarget,
  type WorkflowActionMenuItem,
} from '../lib/workflowActions';
import {
  buildRemediationCreateDraft,
  remediationCreateDraftHref,
  storeRemediationCreateDraft,
} from '../lib/remediationCreateDraft';
import {
  projectChatSessionBlocks,
  type ChatBlock as ProjectedChatBlock,
  type OptimisticUserMessage,
  type RunObservabilityEventRow,
} from '../lib/chatSession';
import {
  type WorkflowDetailSubroute,
  decodeWorkflowIdFromPath,
  workflowDetailSubrouteFromPath,
  workflowDetailSubrouteHref,
} from '../lib/workflowDetailRoutes';

export {
  WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS,
  WORKFLOW_SIDEBAR_ROUTE_ICON_ANIMATION_MS,
};

type DashboardConfig = {
  pollIntervalsMs?: { list?: number; detail?: number; events?: number };
  features?: {
    temporalDashboard?: {
      actionsEnabled?: boolean;
      temporalWorkflowEditing?: boolean;
      temporalTaskEditing?: boolean;
      debugFieldsEnabled?: boolean;
      listEnabled?: boolean;
      workspaceShellEnabled?: boolean;
    };
    logStreamingEnabled?: boolean;
    liveLogsSessionTimelineEnabled?: boolean;
    liveLogsSessionTimelineRollout?: string;
    liveLogsStructuredHistoryEnabled?: boolean;
  };
  sources?: {
    temporal?: Record<string, string>;
    agentRuns?: Record<string, string>;
  };
};

type LiveLogsSessionTimelineRollout = 'off' | 'internal' | 'codex_managed' | 'all_managed';

const GITHUB_PULL_REQUEST_PATH_PATTERN = /^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/i;
const SESSION_PROJECTION_POLL_MS = 5000;
const SESSION_CAPABILITY_POLL_MS = 5000;
const WORKFLOW_WORKSPACE_DESKTOP_MEDIA_QUERY = '(min-width: 768px)';

function useWorkflowWorkspaceDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return true;
    }
    return window.matchMedia(WORKFLOW_WORKSPACE_DESKTOP_MEDIA_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const query = window.matchMedia(WORKFLOW_WORKSPACE_DESKTOP_MEDIA_QUERY);
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  return isDesktop;
}

function normalizeLiveLogsSessionTimelineRollout(
  value: string | null | undefined,
): LiveLogsSessionTimelineRollout | null {
  const normalized = String(value || '').trim().toLowerCase();
  if (
    normalized === 'off'
    || normalized === 'internal'
    || normalized === 'codex_managed'
    || normalized === 'all_managed'
  ) {
    return normalized;
  }
  return null;
}

function isCodexManagedRuntime(runtimeId: string | null | undefined): boolean {
  const normalized = String(runtimeId || '').trim().toLowerCase();
  return normalized === 'codex' || normalized === 'codex_cli';
}

function shouldEnableSessionTimelineViewer({
  config,
  targetRuntime,
  agentRunId,
}: {
  config: DashboardConfig | undefined;
  targetRuntime: string | null | undefined;
  agentRunId: string | null | undefined;
}): boolean {
  const rollout = normalizeLiveLogsSessionTimelineRollout(
    config?.features?.liveLogsSessionTimelineRollout,
  );
  if (rollout === 'off') {
    return false;
  }
  if (rollout === 'internal') {
    return true;
  }
  if (rollout === 'codex_managed') {
    return isCodexManagedRuntime(targetRuntime);
  }
  if (rollout === 'all_managed') {
    return Boolean(String(agentRunId || '').trim());
  }
  return config?.features?.liveLogsSessionTimelineEnabled === true;
}

function shouldUseStructuredHistory(config: DashboardConfig | undefined): boolean {
  return config?.features?.liveLogsStructuredHistoryEnabled !== false;
}

type SegmentedNavItem<T extends string> = {
  value: T;
  label: string;
  href: string;
  badge?: ReactNode;
  /** Optional leading glyph (e.g. the diagnostic Debug tab). */
  icon?: ReactNode;
  /** `quiet` visually de-emphasizes the item without hiding it. */
  tone?: 'quiet';
};
type WorkflowDialogKind =
  | 'rename'
  | 'send-message';

function useWorkflowDetailSubroute(
  pathname: string,
): [WorkflowDetailSubroute, (next: WorkflowDetailSubroute, href: string) => void] {
  const [subroute, setSubroute] = useState(() => workflowDetailSubrouteFromPath(pathname));

  useEffect(() => {
    setSubroute(workflowDetailSubrouteFromPath(pathname));
  }, [pathname]);

  useEffect(() => {
    const onPopState = () => {
      setSubroute(workflowDetailSubrouteFromPath(window.location.pathname));
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const navigate = (next: WorkflowDetailSubroute, href: string) => {
    if (next === subroute && href === `${window.location.pathname}${window.location.search}`) {
      return;
    }
    window.history.pushState({}, '', href);
    setSubroute(next);
  };

  return [subroute, navigate];
}

export function WorkflowWorkspaceShell({
  payload,
  workflowId,
  search,
  displayMode,
}: {
  payload: BootPayload;
  workflowId: string;
  search: URLSearchParams;
  displayMode?: CollectionListDisplayMode | undefined;
}) {
  const cfg = readDashboardConfig(payload);
  const effectiveDisplayMode = displayMode ?? (
    readDashboardPreferences().workflowListDisplayMode
  );
  const sourceTemporal = search.get('source') === 'temporal';
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const selectedWorkflowQuery = useQuery(
    workflowDetailQueryOptions({
      apiBase: payload.apiBase,
      workflowId,
      sourceTemporal,
      detailPoll,
    }),
  );
  const pinnedCurrentRow = selectedWorkflowQuery.data
    ? workflowWorkspaceRowFromDetail(selectedWorkflowQuery.data)
    : null;

  return (
    <CollectionWorkspace
      collection="workflow"
      mode={effectiveDisplayMode === 'sidebar' ? 'sidebar' : 'detail'}
      sidebar={effectiveDisplayMode === 'sidebar' ? (
        <WorkflowWorkspaceSidebarPanel
          payload={payload}
          activeWorkflowId={workflowId}
          pinnedCurrentRow={pinnedCurrentRow}
          search={search}
        />
      ) : null}
      className="workflow-workspace-shell"
      primaryClassName="workflow-workspace-detail"
      primaryLabel="Workflow detail"
      data-sidebar-collapsed={effectiveDisplayMode === 'sidebar' ? 'false' : 'true'}
      data-workflow-list-display-mode={effectiveDisplayMode}
      data-jira-issue="MM-997 MM-999 MM-1000 MM-1002 MM-1005 MM-1008 MM-1138"
      data-source-issue="MM-975"
    >
      <WorkflowDetailPage payload={payload} />
    </CollectionWorkspace>
  );
}

function detailObjectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function firstDetailObjectValue(...values: unknown[]): Record<string, unknown> {
  for (const value of values) {
    const normalized = detailObjectValue(value);
    if (Object.keys(normalized).length > 0) {
      return normalized;
    }
  }
  return {};
}

function workflowActionResultHref(result: unknown, fallbackHref: string): string {
  const resultObject = detailObjectValue(result);
  const execution = detailObjectValue(resultObject.execution);
  const redirectPath =
    typeof execution.redirectPath === 'string' ? execution.redirectPath.trim() : '';
  if (redirectPath) return redirectPath;

  const resultWorkflowId =
    typeof execution.workflowId === 'string' ? execution.workflowId.trim() : '';
  if (resultWorkflowId) {
    const params = new URLSearchParams(typeof window !== 'undefined' ? window.location.search : '');
    if (!params.has('source')) {
      params.set('source', 'temporal');
    }
    const query = params.toString();
    return `/workflows/${encodeURIComponent(resultWorkflowId)}${query ? `?${query}` : ''}`;
  }

  return fallbackHref;
}

function detailStringValue(...values: unknown[]): string {
  for (const value of values) {
    const normalized = String(value ?? '').trim();
    if (normalized) {
      return normalized;
    }
  }
  return '';
}

const PUBLISH_MODE_LABELS: Record<string, string> = {
  auto: 'Auto',
  pr_with_merge_automation: 'PR with Merge Automation',
  pr: 'PR',
  branch: 'Branch',
  none: 'None',
};

function formatPublishModeLabel(value: string | null | undefined): string {
  const normalized = String(value || '').trim().toLowerCase();
  return PUBLISH_MODE_LABELS[normalized] ?? String(value || '').trim();
}

function runtimeCommandFromExecution(execution: unknown): Record<string, unknown> {
  const detail = detailObjectValue(execution);
  const direct = firstDetailObjectValue(detail.runtimeCommand, detail.runtime_command);
  if (Object.keys(direct).length > 0) {
    return direct;
  }
  const inputParameters = firstDetailObjectValue(
    detail.inputParameters,
    detail.input_parameters,
  );
  const task = detailObjectValue(inputParameters.task);
  const taskCommand = firstDetailObjectValue(task.runtimeCommand, task.runtime_command);
  if (Object.keys(taskCommand).length > 0) {
    return taskCommand;
  }
  const objective = detailObjectValue(inputParameters.objective);
  const objectiveCommand = firstDetailObjectValue(
    objective.runtimeCommand,
    objective.runtime_command,
  );
  if (Object.keys(objectiveCommand).length > 0) {
    return objectiveCommand;
  }
  return {};
}

function RuntimeCommandDetail({
  command,
}: {
  command: Record<string, unknown>;
}) {
  const hasCommand = Object.keys(command).length > 0;
  const commandName = detailStringValue(command.rawCommand)
    || (detailStringValue(command.command) ? `/${detailStringValue(command.command)}` : '');
  const runtime = detailStringValue(command.targetRuntime, command.runtimeId, command.runtime);
  const renderMode = detailStringValue(command.renderMode, command.render_mode);
  const status = detailStringValue(
    command.status,
    command.detectionStatus,
    command.hintStatus,
    command.recognitionMode,
  );
  const hintCatalogVersion = detailStringValue(command.hintCatalogVersion);

  return (
    <div className="td-summary-block">
      <h4>Runtime Command</h4>
      {hasCommand ? (
        <div className="td-facts-grid">
          {commandName ? <Fact label="Command"><code>{commandName}</code></Fact> : null}
          {runtime ? <Fact label="Runtime">{formatRuntimeLabel(runtime)}</Fact> : null}
          {renderMode ? <Fact label="Render Mode">{formatStatusLabel(renderMode)}</Fact> : null}
          {status ? <Fact label="Status">{formatStatusLabel(status)}</Fact> : null}
          {hintCatalogVersion ? <Fact label="Hint Catalog Version">{hintCatalogVersion}</Fact> : null}
        </div>
      ) : (
        <p className="small">Historical runtime command metadata is not available.</p>
      )}
    </div>
  );
}

export function getSessionProjectionRefetchInterval(
  isTerminal: boolean,
  hasProjection: boolean,
  hasError: boolean,
): number | false {
  if (isTerminal || hasProjection || hasError) {
    return false;
  }
  return SESSION_PROJECTION_POLL_MS;
}

export function getSessionCapabilityRefetchInterval(
  isTerminal: boolean,
  hasCapabilities: boolean,
  hasError: boolean,
): number | false {
  if (isTerminal || hasCapabilities || hasError) {
    return false;
  }
  return SESSION_CAPABILITY_POLL_MS;
}

function normalizeGitHubPullRequestUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'https:' || parsed.hostname.toLowerCase() !== 'github.com') {
      return null;
    }

    const normalizedPath = parsed.pathname.replace(/\/+$/, '');
    if (!GITHUB_PULL_REQUEST_PATH_PATTERN.test(normalizedPath)) {
      return null;
    }

    return `https://github.com${normalizedPath}`;
  } catch {
    return null;
  }
}

const DependencyOutcomeSchema = z
  .object({
    workflowId: z.string(),
    terminalState: z.string().nullable().optional(),
    closeStatus: z.string().nullable().optional(),
    resolvedAt: z.string().nullable().optional(),
    failureCategory: z.string().nullable().optional(),
    message: z.string().nullable().optional(),
    resolution: z.string().nullable().optional(),
    failureCount: z.number().nullable().optional(),
    lastFailedAt: z.string().nullable().optional(),
  })
  .passthrough();

const DependencySummarySchema = z
  .object({
    workflowId: z.string(),
    title: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    state: z.string().nullable().optional(),
    closeStatus: z.string().nullable().optional(),
    workflowType: z.string().nullable().optional(),
  })
  .passthrough();

const SkillRuntimeSchema = z
  .object({
    resolvedSkillsetRef: z.string().nullable().optional(),
    selectedSkills: z.array(z.string()).optional().default([]),
    selectedEvidence: z
      .array(
        z
          .object({
            name: z.string(),
            sourceKind: z.string().nullable().optional(),
            sourcePath: z.string().nullable().optional(),
            contentRef: z.string().nullable().optional(),
            contentDigest: z.string().nullable().optional(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
    sourceProvenance: z
      .array(
        z
          .object({
            name: z.string(),
            sourceKind: z.string().nullable().optional(),
            sourcePath: z.string().nullable().optional(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
    materializationMode: z.string().nullable().optional(),
    visiblePath: z.string().nullable().optional(),
    backingPath: z.string().nullable().optional(),
    readOnly: z.boolean().nullable().optional(),
    manifestRef: z.string().nullable().optional(),
    promptIndexRef: z.string().nullable().optional(),
    activationSummaryRef: z.string().nullable().optional(),
    diagnostics: z
      .object({
        path: z.string().nullable().optional(),
        objectKind: z.string().nullable().optional(),
        attemptedAction: z.string().nullable().optional(),
        remediation: z.string().nullable().optional(),
        cause: z.string().nullable().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    lifecycleIntent: z
      .object({
        source: z.string(),
        selectors: z.array(z.string()).optional().default([]),
        resolvedSkillsetRef: z.string().nullable().optional(),
        resolutionMode: z.string(),
        explanation: z.string(),
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .passthrough();

const MergeAutomationSchema = z
  .object({
    enabled: z.boolean().optional(),
    workflowId: z.string().nullable().optional(),
    childWorkflowId: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    prNumber: z.union([z.number(), z.string()]).nullable().optional(),
    prUrl: z.string().nullable().optional(),
    latestHeadSha: z.string().nullable().optional(),
    cycles: z.union([z.number(), z.string()]).nullable().optional(),
    resolverChildWorkflowIds: z.array(z.string()).default([]).optional(),
    resolverChildren: z
      .array(
        z
          .object({
            workflowId: z.string(),
            agentRunId: z.string().nullable().optional(),
            status: z.string().nullable().optional(),
            detailHref: z.string().nullable().optional(),
          })
          .passthrough(),
      )
      .default([])
      .optional(),
    blockers: z
      .array(
        z
          .object({
            kind: z.string().nullable().optional(),
            summary: z.string().nullable().optional(),
            source: z.string().nullable().optional(),
            retryable: z.boolean().nullable().optional(),
          })
          .passthrough(),
      )
      .default([])
      .optional(),
    artifactRefs: z
      .object({
        summary: z.string().nullable().optional(),
        gateSnapshots: z.array(z.string()).default([]).optional(),
        resolverAttempts: z.array(z.string()).default([]).optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .passthrough();

const ProposalSummarySchema = z
  .object({
    requested: z.boolean().optional(),
    generatedCount: z.number().optional(),
    submittedCount: z.number().optional(),
    deliveredCount: z.number().optional(),
    validationErrors: z.array(z.record(z.string(), z.unknown())).default([]),
    deliveryFailures: z.array(z.record(z.string(), z.unknown())).default([]),
    externalLinks: z.array(z.record(z.string(), z.unknown())).default([]),
    dedupUpdates: z.array(z.record(z.string(), z.unknown())).default([]),
  })
  .passthrough();

const TargetDiagnosticsSchema = z
  .object({
    targets: z
      .array(
        z
          .object({
            targetKind: z.enum(['objective', 'step']),
            stepId: z.string().nullable().optional(),
            label: z.string(),
            attachments: z
              .array(
                z
                  .object({
                    artifactRef: z.string().nullable().optional(),
                    filename: z.string().nullable().optional(),
                    contentType: z.string().nullable().optional(),
                    sizeBytes: z.number().nullable().optional(),
                    previewAvailable: z.boolean().optional(),
                  })
                  .passthrough(),
              )
              .default([]),
            refs: z
              .array(
                z
                  .object({
                    refKind: z.string(),
                    artifactRef: z.string().nullable().optional(),
                    path: z.string().nullable().optional(),
                  })
                  .passthrough(),
              )
              .default([]),
            failures: z
              .array(
                z
                  .object({
                    phase: z.enum([
                      'upload',
                      'validation',
                      'materialization',
                      'context_generation',
                      'degraded',
                    ]),
                    message: z.string(),
                    evidenceRef: z.string().nullable().optional(),
                  })
                  .passthrough(),
              )
              .default([]),
          })
          .passthrough(),
      )
      .default([]),
    recovery: z
      .object({
        resumed: z.boolean().optional(),
        sourceWorkflowId: z.string().nullable().optional(),
        sourceRunId: z.string().nullable().optional(),
        checkpointRef: z.string().nullable().optional(),
        preservedSteps: z
          .array(
            z
              .object({
                logicalStepId: z.string(),
                title: z.string().nullable().optional(),
                sourceExecutionOrdinal: z.number().nullable().optional(),
                sourceWorkflowId: z.string().nullable().optional(),
                sourceRunId: z.string().nullable().optional(),
              })
              .passthrough(),
          )
          .default([]),
        failedRecoveryPhase: z
          .enum([
            'checkpoint_validation',
            'workspace_restoration',
            'preserved_output_injection',
            'failed_step_execution',
          ])
          .nullable()
          .optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    degradedReason: z.string().nullable().optional(),
  })
  .passthrough();

const EvidenceRefStatusSchema = z
  .object({
    category: z.string(),
    status: z.string(),
    artifactRef: z.string().nullable().optional(),
    boundary: z.string().nullable().optional(),
    reasonCode: z.string().nullable().optional(),
    label: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
  })
  .passthrough();

const RecoveryEligibilitySchema = z
  .object({
    eligible: z.boolean(),
    requestedAction: z.enum(['continue_same_session', 'resume_from_workspace_checkpoint', 'full_retry', 'fix_environment', 'manual_intervention']).optional(),
    defaultAction: z.enum(['continue_same_session', 'resume_from_workspace_checkpoint', 'full_retry', 'fix_environment', 'manual_intervention', 'resume_from_checkpoint', 'environment_fix', 'none']),
    disabledReasonCode: z.string().nullable().optional(),
    checkpointBoundary: z.string().nullable().optional(),
    requiredBoundary: z.string().nullable().optional(),
    resumePhase: z.enum(['rerun_failed_step', 'continue_to_gate', 'continue_after_gate', 'resume_publication', 'retry_restoration']).nullable().optional(),
    checkpointRef: z.string().nullable().optional(),
    checkpointKind: z.string().nullable().optional(),
    targetRuntimeId: z.string().nullable().optional(),
    restoreActivity: z.string().nullable().optional(),
    sourceWorkflowId: z.string().nullable().optional(),
    sourceRunId: z.string().nullable().optional(),
    runtimeId: z.string().nullable().optional(),
    deploymentGeneration: z.string().nullable().optional(),
    capabilitySetVersion: z.string().nullable().optional(),
    capabilityDigest: z.string().nullable().optional(),
    promotionState: z.string().nullable().optional(),
    liveSessionId: z.string().nullable().optional(),
    supportsSameSessionContinuation: z.boolean().nullable().optional(),
    sessionRecoverable: z.boolean().nullable().optional(),
    workspaceRecoverable: z.boolean().nullable().optional(),
    authoritativeWorkspaceCheckpointKind: z.string().nullable().optional(),
    partialRecoveryReason: z.string().nullable().optional(),
    operatorGuidance: z.enum(['continue_same_session', 'resume_from_workspace_checkpoint', 'full_retry', 'fix_environment', 'manual_intervention', 'resume', 'needs_human']),
    evidence: z.array(EvidenceRefStatusSchema).default([]),
  })
  .passthrough();

const StepTimingSchema = z
  .object({
    startedAt: z.string().nullable().optional(),
    endedAt: z.string().nullable().optional(),
    durationMs: z.number().nullable().optional(),
    elapsedMs: z.number().nullable().optional(),
    serverNow: z.string().nullable().optional(),
    precision: z.enum(['exact', 'live', 'fallback', 'unavailable']).default('unavailable'),
    preserved: z.boolean().optional(),
  })
  .passthrough();

// MM-831: bounded Step Execution evidence projection consumed by the expanded
// Step Execution history surface. These mirror the API camelCase ref-only
// projection models; raw artifact bodies are never inlined.
const GateSummaryStatusSchema = z
  .object({
    verdict: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    artifactRef: z.string().nullable().optional(),
  })
  .passthrough();

const SideEffectSummarySchema = z
  .object({
    status: z.string().optional(),
    artifactRefs: z.record(z.string(), z.string()).default({}),
    summary: z.string().nullable().optional(),
  })
  .passthrough();

const EnvironmentDiagnosticReferenceSchema = z
  .object({
    kind: z.string(),
    status: z.string(),
    diagnosticsRef: z.string().nullable().optional(),
    reasonCode: z.string(),
    summary: z.string(),
  })
  .passthrough();

const StepEvidenceSummarySchema = z
  .object({
    logicalStepId: z.string(),
    executionOrdinal: z.number().nullable().optional(),
    checkpointRefsByBoundary: z.record(z.string(), EvidenceRefStatusSchema).default({}),
    contextBundleRef: EvidenceRefStatusSchema.nullable().optional(),
    retrievalManifestRef: EvidenceRefStatusSchema.nullable().optional(),
    memoryManifestRef: EvidenceRefStatusSchema.nullable().optional(),
    gateSummary: GateSummaryStatusSchema.nullable().optional(),
    terminalDisposition: z.string().nullable().optional(),
    sideEffectSummary: SideEffectSummarySchema.nullable().optional(),
    diagnosticRefs: z.array(EnvironmentDiagnosticReferenceSchema).default([]),
  })
  .passthrough();

const StepExecutionLineageSchema = z
  .object({
    sourceWorkflowId: z.string(),
    sourceRunId: z.string(),
    sourceLogicalStepId: z.string(),
    sourceExecutionOrdinal: z.number(),
    relationship: z.string().nullable().optional(),
    lineageExecutionOrdinal: z.number().nullable().optional(),
  })
  .passthrough();

const StepExecutionProjectionSchema = z
  .object({
    manifestArtifactRef: z.string(),
    stepExecutionId: z.string(),
    workflowId: z.string(),
    runId: z.string(),
    logicalStepId: z.string(),
    executionOrdinal: z.number(),
    sourceExecutionOrdinal: z.number().nullable().optional(),
    lineage: StepExecutionLineageSchema.nullable().optional(),
    reason: z.string(),
    status: z.string(),
    terminalDisposition: z.string().nullable().optional(),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
    timing: StepTimingSchema.nullable().optional(),
    summary: z.string().nullable().optional(),
    runtimeChildRefs: z.record(z.string(), z.unknown()).default({}),
    workspacePolicy: z.string().nullable().optional(),
    gitDisposition: z.string().nullable().optional(),
    qualityGateVerdict: z.string().nullable().optional(),
    manifestRefs: z.record(z.string(), z.unknown()).default({}),
    outputRefs: z.record(z.string(), z.unknown()).default({}),
    stepEvidence: StepEvidenceSummarySchema.nullable().optional(),
    recoveryEligibility: RecoveryEligibilitySchema.nullable().optional(),
  })
  .passthrough();

const StepExecutionListSchema = z
  .object({
    workflowId: z.string(),
    runId: z.string(),
    runScope: z.string().default('latest'),
    logicalStepId: z.string().default(''),
    stepExecutions: z.array(StepExecutionProjectionSchema).default([]),
  })
  .passthrough();

const ExecutionDetailSchema = z
  .object({
    taskId: z.string().optional(),
    workflowId: z.string().optional(),
    namespace: z.string(),
    temporalRunId: z.string().optional(),
    runId: z.string().optional(),
    source: z.string(),
    workflowType: z.string().optional(),
    entry: z.string().optional(),
    title: z.string(),
    summary: z.string(),
    taskInstructions: z.string().nullable().optional(),
    status: z.string(),
    state: z.string(),
    rawState: z.string().optional(),
    temporalStatus: z.string().optional(),
    closeStatus: z.string().nullable().optional(),
    waitingReason: z.string().nullable().optional(),
    dependsOn: z.array(z.string()).default([]),
    hasDependencies: z.boolean().optional(),
    dependencyWaitOccurred: z.boolean().optional(),
    dependencyWaitDurationMs: z.number().nullable().optional(),
    dependencyResolution: z.string().nullable().optional(),
    failedDependencyId: z.string().nullable().optional(),
    blockedOnDependencies: z.boolean().optional(),
    dependencyOutcomes: z.array(DependencyOutcomeSchema).default([]),
    proposalSummary: ProposalSummarySchema.nullable().optional(),
    proposalOutcomes: z.array(z.record(z.string(), z.unknown())).default([]),
    prerequisites: z.array(DependencySummarySchema).default([]),
    dependents: z.array(DependencySummarySchema).default([]),
    attentionRequired: z.boolean().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    model: z.string().nullable().optional(),
    requestedModel: z.string().nullable().optional(),
    resolvedModel: z.string().nullable().optional(),
    modelSource: z.string().nullable().optional(),
    profileId: z.string().nullable().optional(),
    providerId: z.string().nullable().optional(),
    providerLabel: z.string().nullable().optional(),
    effort: z.string().nullable().optional(),
    priority: z.number().nullable().optional(),
    startingBranch: z.string().nullable().optional(),
    targetBranch: z.string().nullable().optional(),
    outputBranch: z
      .object({
        name: z.string(),
        url: z.string().nullable().optional(),
        headSha: z.string().nullable().optional(),
        baseBranch: z.string().nullable().optional(),
        intent: z.enum(['normal', 'terminal_checkpoint']),
        status: z.string(),
        evidenceRef: z.string().nullable().optional(),
      })
      .nullable()
      .optional(),
    repository: z.string().nullable().optional(),
    prUrl: z.string().nullable().optional(),
    resolvedSkillsetRef: z.string().nullable().optional(),
    taskSkills: z.array(z.string()).nullable().optional(),
    skillRuntime: SkillRuntimeSchema.nullable().optional(),
    publishMode: z.string().nullable().optional(),
    mergeAutomation: MergeAutomationSchema.nullable().optional(),
    summaryArtifactRef: z.string().nullable().optional(),
    summary_artifact_ref: z.string().nullable().optional(),
    scheduledFor: z.string().nullable().optional(),
    createdAt: z.string(),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().optional(),
    closedAt: z.string().nullable().optional(),
    agentRunId: z.string().nullable().optional(),
    agent_run_id: z.string().nullable().optional(),
    bridgeSessionId: z.string().nullable().optional(),
    bridge_session_id: z.string().nullable().optional(),
    omnigentBridgeSessionId: z.string().nullable().optional(),
    omnigent_bridge_session_id: z.string().nullable().optional(),
    idempotencyKey: z.string().nullable().optional(),
    idempotency_key: z.string().nullable().optional(),
    stepsHref: z.string().nullable().optional(),
    debugFields: z
      .object({
        workflowId: z.string().optional(),
        temporalRunId: z.string().optional(),
        namespace: z.string().optional(),
        temporalStatus: z.string().optional(),
        rawState: z.string().optional(),
        closeStatus: z.string().nullable().optional(),
        waitingReason: z.string().nullable().optional(),
        attentionRequired: z.boolean().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    actions: ExecutionActionsSchema.optional(),
    finishSummary: z.unknown().nullable().optional(),
    resume: z
      .object({
        available: z.boolean().optional(),
        checkpointRef: z.string().nullable().optional(),
        failedStepId: z.string().nullable().optional(),
        sourceRunId: z.string().nullable().optional(),
        disabledReason: z.string().nullable().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    relatedRuns: z
      .array(
        z
          .object({
            workflowId: z.string(),
            runId: z.string().nullable().optional(),
            relationship: z.string(),
            status: z.string().nullable().optional(),
            targetRuntime: z.string().nullable().optional(),
            model: z.string().nullable().optional(),
            requestedModel: z.string().nullable().optional(),
            resolvedModel: z.string().nullable().optional(),
            effort: z.string().nullable().optional(),
            href: z.string(),
          })
          .passthrough(),
      )
      .default([])
      .optional(),
    recurrence: z
      .object({
        definitionId: z.string(),
        href: z.string(),
      })
      .passthrough()
      .nullable()
      .optional(),
    targetDiagnostics: TargetDiagnosticsSchema.nullable().optional(),
    recoveryEligibility: RecoveryEligibilitySchema.nullable().optional(),
    interventionAudit: z
      .array(
        z
          .object({
            action: z.string(),
            transport: z.string(),
            summary: z.string(),
            detail: z.string().nullable().optional(),
            createdAt: z.string(),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

type ExecutionDetail = z.infer<typeof ExecutionDetailSchema>;

const RemediationApprovalStateSchema = z
  .object({
    requestId: z.string().nullable().optional(),
    actionKind: z.string().nullable().optional(),
    riskTier: z.string().nullable().optional(),
    preconditions: z.string().nullable().optional(),
    blastRadius: z.string().nullable().optional(),
    decision: z.string().default('not_required'),
    decisionActor: z.string().nullable().optional(),
    decisionAt: z.string().nullable().optional(),
    canDecide: z.boolean().default(false),
    auditRef: z.string().nullable().optional(),
  })
  .passthrough();

const RemediationLiveObservationSchema = z
  .object({
    status: z.string().nullable().optional(),
    label: z.string().nullable().optional(),
    sequenceCursor: z.string().nullable().optional(),
    reconnectState: z.string().nullable().optional(),
    epoch: z.string().nullable().optional(),
    fallbackReason: z.string().nullable().optional(),
  })
  .passthrough();

const RemediationLockOutcomeSchema = z
  .object({
    state: z.string().nullable().optional(),
    holder: z.string().nullable().optional(),
    releasedAt: z.string().nullable().optional(),
  })
  .passthrough();

const RemediationLinkSchema = z
  .object({
    remediationWorkflowId: z.string(),
    remediationRunId: z.string(),
    targetWorkflowId: z.string(),
    targetRunId: z.string(),
    mode: z.string(),
    authorityMode: z.string(),
    status: z.string(),
    activeLockScope: z.string().nullable().optional(),
    activeLockHolder: z.string().nullable().optional(),
    latestActionSummary: z.string().nullable().optional(),
    resolution: z.string().nullable().optional(),
    contextArtifactRef: z.string().nullable().optional(),
    selectedSteps: z.array(z.string()).nullable().optional(),
    currentTargetState: z.string().nullable().optional(),
    allowedActions: z.array(z.string()).nullable().optional(),
    evidenceDegraded: z.boolean().nullable().optional(),
    unavailableEvidenceClasses: z.array(z.string()).nullable().optional(),
    liveObservation: RemediationLiveObservationSchema.nullable().optional(),
    lockOutcome: RemediationLockOutcomeSchema.nullable().optional(),
    approvalState: RemediationApprovalStateSchema.nullable().optional(),
    checkpointBranches: z
      .array(
        z
          .object({
            workflowId: z.string(),
            branchId: z.string(),
            branchTurnId: z.string().nullable().optional(),
            checkpointRef: z.string().nullable().optional(),
            contextArtifactRef: z.string().nullable().optional(),
            loopId: z.string().nullable().optional(),
            rootCheckpointRef: z.string().nullable().optional(),
            rootWorkspaceDigest: z.string().nullable().optional(),
            headCheckpointRef: z.string().nullable().optional(),
            headWorkspaceDigest: z.string().nullable().optional(),
            headStepExecutionId: z.string().nullable().optional(),
            headAttemptOrdinal: z.number().int().nullable().optional(),
            headVersion: z.number().int().nullable().optional(),
            headStatus: z.string().nullable().optional(),
            latestVerificationRef: z.string().nullable().optional(),
            latestVerificationVerdict: z.string().nullable().optional(),
            remainingWorkRef: z.string().nullable().optional(),
            nextActionBaseline: z.object({
              checkpointRef: z.string(),
              workspaceDigest: z.string(),
              headVersion: z.number().int(),
            }).nullable().optional(),
            operation: z.string().nullable().optional(),
            idempotencyKey: z.string().nullable().optional(),
            createdAt: z.string().nullable().optional(),
          })
          .passthrough(),
      )
      .default([]),
    createdAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
  })
  .passthrough();

const RemediationLinksSchema = z
  .object({
    direction: z.string().default('inbound'),
    items: z.array(RemediationLinkSchema).default([]),
  })
  .passthrough();

const CheckpointBranchModelSchema = z
  .object({
    branchId: z.string(),
    workflowId: z.string(),
    rootWorkflowId: z.string().nullable().optional(),
    sourceRunId: z.string(),
    logicalStepId: z.string().nullable().optional(),
    sourceExecutionOrdinal: z.number().nullable().optional(),
    sourceCheckpointBoundary: z.string(),
    sourceCheckpointRef: z.string(),
    sourceCheckpointDigest: z.string().nullable().optional(),
    parentBranchId: z.string().nullable().optional(),
    parentTurnId: z.string().nullable().optional(),
    label: z.string(),
    state: z.string(),
    branchKind: z.string().nullable().optional(),
    workspacePolicy: z.string(),
    runtimeContextPolicy: z.string(),
    gitRepository: z.string().nullable().optional(),
    gitBaseBranch: z.string().nullable().optional(),
    gitWorkBranch: z.string().nullable().optional(),
    currentHeadStepExecutionId: z.string().nullable().optional(),
    currentHeadCheckpointRef: z.string().nullable().optional(),
    artifactRefs: z.record(z.string(), z.unknown()).default({}),
    currentHeadCommit: z.string().nullable().optional(),
    pullRequestUrl: z.string().nullable().optional(),
    publishStatus: z.string().nullable().optional(),
    promotedAt: z.string().nullable().optional(),
    archivedAt: z.string().nullable().optional(),
    createdBy: z.string().nullable().optional(),
    createdAt: z.string(),
    updatedAt: z.string(),
  })
  .passthrough();

const CheckpointBranchListSchema = z
  .object({
    items: z.array(CheckpointBranchModelSchema).default([]),
  })
  .passthrough();

const CheckpointBranchTurnModelSchema = z
  .object({
    branchTurnId: z.string(),
    branchId: z.string(),
    parentTurnId: z.string().nullable().optional(),
    instructionRef: z.string(),
    instructionDigest: z.string(),
    sourceCheckpointRef: z.string(),
    sourceCheckpointDigest: z.string().nullable().optional(),
    contextBundleRef: z.string().nullable().optional(),
    stepExecutionManifestRef: z.string().nullable().optional(),
    createdStepExecutionId: z.string().nullable().optional(),
    runtimeAgentRunId: z.string().nullable().optional(),
    providerSessionId: z.string().nullable().optional(),
    idempotencyKey: z.string(),
    status: z.string(),
    diagnostics: z.record(z.string(), z.unknown()).default({}),
    createdAt: z.string(),
    updatedAt: z.string(),
  })
  .passthrough();

const CheckpointBranchTurnListSchema = z
  .object({
    items: z.array(CheckpointBranchTurnModelSchema).default([]),
  })
  .passthrough();

const CheckpointBranchCompareSchema = z
  .object({
    branchId: z.string(),
    againstBranchId: z.string(),
    workflowId: z.string(),
    branchState: z.string(),
    againstState: z.string(),
    branchHeadStepExecutionId: z.string().nullable().optional(),
    againstHeadStepExecutionId: z.string().nullable().optional(),
    summaryRef: z.string(),
    diagnosticsRefs: z.array(z.string()).default([]),
    comparisonRecord: z.record(z.string(), z.unknown()).default({}),
  })
  .passthrough();

const ArtifactSummarySchema = z
  .object({
    artifactId: z.string(),
    contentType: z.string().nullable().optional(),
    sizeBytes: z.number().nullable().optional(),
    status: z.string().optional(),
    downloadUrl: z.string().nullable().optional(),
    defaultReadRef: z
      .object({
        artifactId: z.string(),
      })
      .passthrough()
      .nullable()
      .optional(),
    rawAccessAllowed: z.boolean().nullable().optional(),
    metadata: z.record(z.string(), z.unknown()).default({}),
    links: z
      .array(
        z
          .object({
            linkType: z.string(),
            label: z.string().nullable().optional(),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

const ArtifactRefSummarySchema = z
  .object({
    artifact_id: z.string(),
  })
  .passthrough();

const ArtifactSessionProjectionSchema = z.object({
  agent_run_id: z.string(),
  session_id: z.string(),
  session_epoch: z.number(),
  grouped_artifacts: z
    .array(
      z.object({
        group_key: z.string(),
        title: z.string(),
        artifacts: z
          .array(
            z
              .object({
                artifact_id: z.string().optional(),
                artifactId: z.string().optional(),
                status: z.string().optional(),
              })
              .passthrough()
              .transform((artifact) =>
          ArtifactSummarySchema.parse({
            ...artifact,
            artifactId: artifact.artifactId ?? artifact.artifact_id,
          }),
              ),
          )
          .default([]),
      }),
    )
    .default([]),
  latest_summary_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_checkpoint_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_control_event_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_reset_boundary_ref: ArtifactRefSummarySchema.nullable().optional(),
});

const ArtifactSessionControlResponseSchema = z.object({
  action: z.enum(['continue_same_session', 'clear_session', 'interrupt_turn', 'cancel_session']),
  controlRequestId: z.string().default('legacy-control-request'),
  status: z.enum(['accepted', 'rejected', 'completed', 'failed', 'delivery_unknown']).default('completed'),
  stableReasonCode: z.string().nullable().optional(),
  controlEventRef: z.string().nullable().optional(),
  completedAt: z.string().nullable().optional(),
  projection: ArtifactSessionProjectionSchema,
});

const SessionResourceSchema = z
  .object({
    resource_id: z.string().optional(),
    resourceId: z.string().optional(),
    artifact_id: z.string().optional(),
    artifactId: z.string().optional(),
    group_key: z.string().optional(),
    groupKey: z.string().optional(),
    group_title: z.string().optional(),
    groupTitle: z.string().optional(),
    label: z.string().nullable().optional(),
    content_type: z.string().nullable().optional(),
    contentType: z.string().nullable().optional(),
    size_bytes: z.number().nullable().optional(),
    sizeBytes: z.number().nullable().optional(),
    content_url: z.string().optional(),
    contentUrl: z.string().optional(),
    download_url: z.string().optional(),
    downloadUrl: z.string().optional(),
    preview_available: z.boolean().optional(),
    previewAvailable: z.boolean().optional(),
    download_available: z.boolean().optional(),
    downloadAvailable: z.boolean().optional(),
    completeness_status: z.enum(['complete', 'degraded', 'pending']).optional(),
    completenessStatus: z.enum(['complete', 'degraded', 'pending']).optional(),
    unavailable_reason: z.string().nullable().optional(),
    unavailableReason: z.string().nullable().optional(),
    metadata: z.record(z.string(), z.unknown()).default({}),
  })
  .passthrough()
  .transform((resource) => ({
    resourceId: resource.resourceId ?? resource.resource_id ?? resource.artifactId ?? resource.artifact_id ?? '',
    artifactId: resource.artifactId ?? resource.artifact_id ?? '',
    groupKey: resource.groupKey ?? resource.group_key ?? '',
    groupTitle: resource.groupTitle ?? resource.group_title ?? 'Resources',
    label: resource.label ?? null,
    contentType: resource.contentType ?? resource.content_type ?? null,
    sizeBytes: resource.sizeBytes ?? resource.size_bytes ?? null,
    contentUrl: resource.contentUrl ?? resource.content_url ?? null,
    downloadUrl: resource.downloadUrl ?? resource.download_url ?? null,
    previewAvailable: resource.previewAvailable ?? resource.preview_available ?? false,
    downloadAvailable: resource.downloadAvailable ?? resource.download_available ?? true,
    completenessStatus: resource.completenessStatus ?? resource.completeness_status ?? 'complete',
    unavailableReason: resource.unavailableReason ?? resource.unavailable_reason ?? null,
    metadata: resource.metadata ?? {},
  }));

const SessionResourceListSchema = z.object({
  agent_run_id: z.string(),
  session_id: z.string(),
  session_epoch: z.number(),
  resources: z.array(SessionResourceSchema).default([]),
});

type ArtifactSessionControlAction = z.infer<typeof ArtifactSessionControlResponseSchema>['action'];

type ArtifactSessionControlRequest = {
  schemaVersion: 1;
  controlRequestId: string;
  idempotencyKey: string;
  action: ArtifactSessionControlAction;
  expectedSessionEpoch: number;
  expectedTurnId?: string;
  message?: string;
  reason?: string;
};

type ChatSessionMessageEvent = {
  type: 'chat_session.message_submitted';
  clientEventKey: string;
  sessionId: string;
  sessionEpoch: number;
  message: string;
};

type OptimisticChatSessionMessage = ChatSessionMessageEvent & {
  status: 'pending' | 'delivery_unknown' | 'failed';
  error?: string;
};

const InterventionCapabilitiesSchema = z
  .object({
    sendFollowUp: z.boolean().default(false),
    clearSession: z.boolean().default(false),
    interruptTurn: z.boolean().default(false),
    cancelSession: z.boolean().default(false),
    resolveElicitation: z.boolean().default(false),
    harvestResources: z.boolean().default(false),
    terminalCleanup: z.boolean().default(false),
  })
  .default({
    sendFollowUp: false,
    clearSession: false,
    interruptTurn: false,
    cancelSession: false,
    resolveElicitation: false,
    harvestResources: false,
    terminalCleanup: false,
  });

const SessionSnapshotSchema = z
  .object({
    sessionId: z.string(),
    sessionEpoch: z.number(),
    containerId: z.string(),
    threadId: z.string(),
    activeTurnId: z.string().nullable().optional(),
    status: z.string().optional(),
    latestSummaryRef: z.string().nullable().optional(),
    latestCheckpointRef: z.string().nullable().optional(),
    latestControlEventRef: z.string().nullable().optional(),
    latestResetBoundaryRef: z.string().nullable().optional(),
  })
  .passthrough();

const ObservabilitySummarySchema = z.object({
  supportsLiveStreaming: z.boolean().default(false),
  liveStreamStatus: z.string().default('unavailable'),
  status: z.string().default(''),
  sessionSnapshot: SessionSnapshotSchema.nullable().optional(),
  interventionCapabilities: InterventionCapabilitiesSchema,
});

const RawObservabilityEventSchema = z
  .object({
    sequence: z.number(),
    timestamp: z.string(),
    stream: z.enum(['stdout', 'stderr', 'system', 'session']),
    text: z.string(),
    offset: z.number().nullable().optional(),
    kind: z.string().nullable().optional(),
    sessionId: z.string().nullable().optional(),
    session_id: z.string().nullable().optional(),
    sessionEpoch: z.number().nullable().optional(),
    session_epoch: z.number().nullable().optional(),
    containerId: z.string().nullable().optional(),
    container_id: z.string().nullable().optional(),
    threadId: z.string().nullable().optional(),
    thread_id: z.string().nullable().optional(),
    turnId: z.string().nullable().optional(),
    turn_id: z.string().nullable().optional(),
    activeTurnId: z.string().nullable().optional(),
    active_turn_id: z.string().nullable().optional(),
    metadata: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough();

export function normalizeObservabilityEvent(event: z.infer<typeof RawObservabilityEventSchema>) {
  return {
    sequence: event.sequence,
    timestamp: event.timestamp,
    stream: event.stream,
    text: event.text,
    offset: event.offset ?? null,
    kind: event.kind ?? null,
    session_id: event.session_id ?? event.sessionId ?? null,
    session_epoch: event.session_epoch ?? event.sessionEpoch ?? null,
    container_id: event.container_id ?? event.containerId ?? null,
    thread_id: event.thread_id ?? event.threadId ?? null,
    turn_id: event.turn_id ?? event.turnId ?? null,
    active_turn_id: event.active_turn_id ?? event.activeTurnId ?? null,
    metadata: event.metadata ?? {},
  };
}

const ObservabilityEventSchema = RawObservabilityEventSchema.transform((event) =>
  normalizeObservabilityEvent(event),
);

const LegacyObservabilityEventsResponseSchema = z.object({
  schemaVersion: z.never().optional(),
  events: z.array(ObservabilityEventSchema).default([]),
  truncated: z.boolean().default(false),
  sessionSnapshot: SessionSnapshotSchema.nullable().optional(),
});

const BridgeSessionEventsPageSchema = z
  .object({
    schemaVersion: z.literal('moonmind.bridge-session-events-page.v1'),
    bridgeSessionId: z.string(),
    items: z.array(ObservabilityEventSchema).default([]),
    after: z.number().int().nonnegative(),
    nextCursor: z.string().nullable(),
    hasMore: z.boolean(),
    terminal: z.boolean(),
    latestSequence: z.number().int().nonnegative(),
    retentionGap: z
      .object({ requestedAfter: z.number().int(), earliestAvailable: z.number().int() })
      .nullable()
      .optional(),
    terminalEnvelope: z
      .object({
        schemaVersion: z.literal('moonmind.bridge-session-terminal.v1'),
        status: z.enum(['completed', 'failed', 'canceled', 'timed_out']),
        failureClass: z.string().nullable().optional(),
        failureCode: z.string().nullable().optional(),
        summary: z.string().nullable().optional(),
        diagnosticsRef: z.string().nullable().optional(),
        captureManifestRef: z.string().nullable().optional(),
        initialSnapshotRef: z.string().nullable().optional(),
        finalSnapshotRef: z.string().nullable().optional(),
        rawEventsRef: z.string().nullable().optional(),
        normalizedEventsRef: z.string().nullable().optional(),
        externalStateRef: z.string().nullable().optional(),
        cleanupState: z.string().nullable().optional(),
        leaseReleaseState: z.string().nullable().optional(),
        evidenceIncompleteReason: z.string().nullable().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .transform((page) => ({
    events: page.items,
    truncated: page.retentionGap != null,
    sessionSnapshot: undefined,
    nextCursor: page.nextCursor,
    hasMore: page.hasMore,
    terminal: page.terminal,
    terminalEnvelope: page.terminalEnvelope ?? null,
  }));

const ObservabilityEventsResponseSchema = z.union([
  BridgeSessionEventsPageSchema,
  LegacyObservabilityEventsResponseSchema,
]);

export function parseObservabilityEventsResponse(value: unknown) {
  return ObservabilityEventsResponseSchema.parse(value);
}

const ArtifactListSchema = z.object({
  artifacts: z
    .array(
      z
        .object({
          artifactId: z.string().optional(),
          artifact_id: z.string().optional(),
          contentType: z.string().nullable().optional(),
          content_type: z.string().nullable().optional(),
          sizeBytes: z.number().nullable().optional(),
          size_bytes: z.number().nullable().optional(),
          status: z.string().optional(),
          downloadUrl: z.string().nullable().optional(),
          download_url: z.string().nullable().optional(),
          defaultReadRef: z.unknown().optional(),
          default_read_ref: z.unknown().optional(),
          rawAccessAllowed: z.boolean().nullable().optional(),
          raw_access_allowed: z.boolean().nullable().optional(),
        })
        .passthrough()
        .transform((artifact) => {
          const rawArtifact = artifact as Record<string, unknown>;
          const defaultReadRefRaw = rawArtifact.defaultReadRef ?? rawArtifact.default_read_ref;
          const defaultReadRef =
            defaultReadRefRaw && typeof defaultReadRefRaw === 'object'
              ? {
                  ...(defaultReadRefRaw as Record<string, unknown>),
                  artifactId:
                    (defaultReadRefRaw as Record<string, unknown>).artifactId ??
                    (defaultReadRefRaw as Record<string, unknown>).artifact_id,
                }
              : null;
          const links = (Array.isArray(rawArtifact.links) ? rawArtifact.links : []).map((link) => {
            const rawLink = link as Record<string, unknown>;
            return {
              ...rawLink,
              linkType: rawLink.linkType ?? rawLink.link_type,
              label: rawLink.label ?? null,
            };
          });
          return ArtifactSummarySchema.parse({
            ...rawArtifact,
            artifactId: rawArtifact.artifactId ?? rawArtifact.artifact_id,
            contentType: rawArtifact.contentType ?? rawArtifact.content_type ?? null,
            sizeBytes: rawArtifact.sizeBytes ?? rawArtifact.size_bytes ?? null,
            downloadUrl: rawArtifact.downloadUrl ?? rawArtifact.download_url ?? null,
            defaultReadRef,
            rawAccessAllowed: rawArtifact.rawAccessAllowed ?? rawArtifact.raw_access_allowed ?? null,
            metadata: rawArtifact.metadata ?? {},
            links,
          });
        }),
    )
    .default([]),
});

const StepLedgerToolSchema = z
  .object({
    type: z.string().nullable().optional(),
    name: z.string().nullable().optional(),
    version: z.string().nullable().optional(),
  })
  .passthrough();

const StepLedgerCheckSchema = z
  .object({
    kind: z.string(),
    status: z.string(),
    summary: z.string().nullable().optional(),
    retryCount: z.number().default(0),
    artifactRef: z.string().nullable().optional(),
  })
  .passthrough();

const StepLedgerRefsSchema = z
  .object({
    childWorkflowId: z.string().nullable().optional(),
    childRunId: z.string().nullable().optional(),
    agentRunId: z.string().nullable().optional(),
    bridgeSessionId: z.string().nullable().optional(),
    bridge_session_id: z.string().nullable().optional(),
    omnigentBridgeSessionId: z.string().nullable().optional(),
    omnigent_bridge_session_id: z.string().nullable().optional(),
    idempotencyKey: z.string().nullable().optional(),
    idempotency_key: z.string().nullable().optional(),
  })
  .default({
    childWorkflowId: null,
    childRunId: null,
    agentRunId: null,
    bridgeSessionId: null,
    bridge_session_id: null,
    omnigentBridgeSessionId: null,
    omnigent_bridge_session_id: null,
    idempotencyKey: null,
    idempotency_key: null,
  });

const StepLedgerArtifactsSchema = z
  .object({
    outputSummary: z.string().nullable().optional(),
    outputPrimary: z.string().nullable().optional(),
    runtimeStdout: z.string().nullable().optional(),
    runtimeStderr: z.string().nullable().optional(),
    runtimeMergedLogs: z.string().nullable().optional(),
    runtimeDiagnostics: z.string().nullable().optional(),
    providerSnapshot: z.string().nullable().optional(),
    stepExecutionManifestRef: z.string().nullable().optional(),
    stepExecutionManifestRefs: z.array(z.string()).nullable().optional(),
  })
  .default({
    outputSummary: null,
    outputPrimary: null,
    runtimeStdout: null,
    runtimeStderr: null,
    runtimeMergedLogs: null,
    runtimeDiagnostics: null,
    providerSnapshot: null,
    stepExecutionManifestRef: null,
    stepExecutionManifestRefs: [],
  });

const StepLedgerRecoveryPreservationSchema = z
  .object({
    eligible: z.boolean(),
    reason: z.string(),
    message: z.string().nullable().optional(),
  })
  .passthrough();

const StepLedgerWorkloadSchema = z
  .object({
    agentRunId: z.string().nullable().optional(),
    stepId: z.string().nullable().optional(),
    executionOrdinal: z.number().nullable().optional(),
    toolName: z.string().nullable().optional(),
    profileId: z.string().nullable().optional(),
    imageRef: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    exitCode: z.number().nullable().optional(),
    durationSeconds: z.number().nullable().optional(),
    timeoutReason: z.string().nullable().optional(),
    cancelReason: z.string().nullable().optional(),
    sessionContext: z.record(z.string(), z.unknown()).nullable().optional(),
    artifactPublication: z
      .object({
        status: z.string(),
        error: z.string().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .passthrough();

const StepLedgerRowSchema = z
  .object({
    logicalStepId: z.string(),
    order: z.number(),
    title: z.string().nullable().optional().transform((value) => value ?? ''),
    tool: StepLedgerToolSchema.default({}),
    dependsOn: z.array(z.string()).default([]),
    status: z.enum(CANONICAL_STEP_STATUSES),
    waitingReason: z.string().nullable().optional(),
    attentionRequired: z.boolean().optional(),
    executionOrdinal: z.number().default(0),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    annotations: z.record(z.string(), z.unknown()).default({}),
    checks: z.array(StepLedgerCheckSchema).default([]),
    refs: StepLedgerRefsSchema,
    artifacts: StepLedgerArtifactsSchema,
    preservedFrom: z
      .object({
        workflowId: z.string(),
        runId: z.string(),
        logicalStepId: z.string(),
        executionOrdinal: z.number(),
      })
      .passthrough()
      .nullable()
      .optional(),
    workload: StepLedgerWorkloadSchema.nullable().optional(),
    timing: StepTimingSchema.nullable().optional(),
    stateCheckpointRef: z.string().nullable().optional(),
    recoveryPreservation: StepLedgerRecoveryPreservationSchema.nullable().optional(),
    lastError: z.unknown().nullable().optional(),
  })
  .passthrough();

const StepLedgerSnapshotSchema = z.object({
  workflowId: z.string(),
  runId: z.string(),
  runScope: z.string().default('latest'),
  steps: z.array(StepLedgerRowSchema).default([]),
});

const StepExecutionDetailSchema = z
  .object({
    logicalStepId: z.string(),
    executionOrdinal: z.number(),
    recoveryEligibility: RecoveryEligibilitySchema.nullable().optional(),
  })
  .passthrough();

const RunSummaryArtifactSchema = z
  .object({
    finishOutcome: z
      .object({
        code: z.string().optional(),
        stage: z.string().optional(),
        reason: z.string().optional(),
      })
      .passthrough()
      .optional(),
    publish: z
      .object({
        mode: z.string().optional(),
        status: z.string().optional(),
        reason: z.string().optional(),
      })
      .passthrough()
      .optional(),
    operatorSummary: z.string().nullable().optional(),
    nextAction: z.string().nullable().optional(),
    lastStep: z
      .object({
        id: z.string().nullable().optional(),
        summary: z.string().nullable().optional(),
        diagnosticsRef: z.string().nullable().optional(),
      })
      .passthrough()
      .optional(),
    failure: z
      .object({
        failureCode: z.string().nullable().optional(),
        queuedChildCount: z.number().int().nonnegative().optional(),
        queuedChildren: z
          .array(
            z.object({
              workflowId: z.string().nullable().optional(),
              executionId: z.string().nullable().optional(),
              ref: z.string().nullable().optional(),
            }).passthrough(),
          )
          .optional(),
      })
      .passthrough()
      .optional(),
    publishContext: z
      .object({
        branch: z.string().nullable().optional(),
        baseRef: z.string().nullable().optional(),
        commitCount: z.union([z.number(), z.string()]).nullable().optional(),
        pullRequestUrl: z.string().nullable().optional(),
        boundedStoryLoop: z.object({
          continuationDecision: z.object({
            reason: z.string().optional(),
            continueLoop: z.boolean().optional(),
            progressVectorDigest: z.string().optional(),
            gate: z.object({
              progressVector: z.object({
                classification: z.string(),
                unresolvedGapScore: z.number().int().nonnegative(),
                priorUnresolvedGapScore: z.number().int().nonnegative().nullable().optional(),
                requiredChecks: z.record(z.string(), z.number().int().nonnegative()),
                priorRequiredChecks: z.record(z.string(), z.number().int().nonnegative()).nullable().optional(),
                regressions: z.array(z.string()).optional(),
                repeatedFailureSignatures: z.array(z.string()).optional(),
                newAuthoritativeEvidenceDigest: z.string().nullable().optional(),
                relevantDiffDigest: z.string().nullable().optional(),
                gaps: z.array(z.object({ status: z.string() }).passthrough()).optional(),
              }).passthrough().optional(),
            }).passthrough().optional(),
            budget: z.object({
              maxAttempts: z.number().int().positive().optional(),
              maxElapsedSeconds: z.number().int().positive().nullable().optional(),
              providerBudget: z.number().int().nonnegative().nullable().optional(),
              tokenBudget: z.number().int().nonnegative().nullable().optional(),
              costBudget: z.number().int().nonnegative().nullable().optional(),
              consumed: z.record(z.string(), z.number().int().nonnegative()),
            }).passthrough().optional(),
          }).passthrough().optional(),
        }).passthrough().optional(),
      })
      .passthrough()
      .optional(),
    controlStop: z
      .object({
        kind: z.string(),
        verdict: z.string().nullable().optional(),
        reasonCode: z.string().nullable().optional(),
        remainingWorkRef: z.string().nullable().optional(),
        workspaceHeadRef: z.string().nullable().optional(),
        publicationFeasible: z.boolean().optional(),
        publicationFeasibilityReason: z.string().nullable().optional(),
        publicationAttempted: z.boolean().optional(),
        auxiliaryOutcomes: z.record(z.string(), z.unknown()).optional(),
      })
      .passthrough()
      .optional(),
    mergeAutomation: MergeAutomationSchema.optional(),
    proposals: ProposalSummarySchema.optional(),
  })
  .passthrough();

function readDashboardConfig(payload: BootPayload): DashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

export function expandRouteTemplate(
  template: string | null | undefined,
  params: Record<string, string | null | undefined>,
): string | null {
  if (!template) return null;
  let path = template;
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) {
      return null;
    }
    path = path.replaceAll(`{${key}}`, encodeURIComponent(value));
  }
  return path.includes('{') && path.includes('}') ? null : path;
}

function joinApiBasePath(apiBase: string, path: string): string {
  const base = apiBase.replace(/\/+$/g, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${suffix}`;
}

function resolveApiBaseTemplate(apiBase: string, expandedTemplate: string): string {
  const template = expandedTemplate.trim();
  if (!template) return template;
  if (/^[a-z][a-z\d+.-]*:\/\//i.test(template)) return template;

  const normalizedApiBase = apiBase.replace(/\/+$/g, '');
  if (!normalizedApiBase) return template;
  if (template.startsWith(normalizedApiBase)) return template;

  if (template === '/api') {
    return normalizedApiBase;
  }
  if (template.startsWith('/api/')) {
    return joinApiBasePath(normalizedApiBase, template.slice('/api'.length));
  }
  return joinApiBasePath(normalizedApiBase, template);
}

function agentRunRoute(
  apiBase: string,
  template: string | null | undefined,
  fallback: string,
  params: Record<string, string | null | undefined>,
): string {
  const expandedTemplate = expandRouteTemplate(template, params);
  if (expandedTemplate) {
    return resolveApiBaseTemplate(apiBase, expandedTemplate);
  }
  return joinApiBasePath(apiBase, fallback);
}

function agentRunRouteParams(agentRunId: string, extra: Record<string, string | null | undefined> = {}) {
  return { agentRunId, ...extra };
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function proposalFieldText(row: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  }
  return '';
}

function proposalNestedRecord(row: Record<string, unknown>, ...keys: string[]): Record<string, unknown> {
  for (const key of keys) {
    const value = row[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  }
  return {};
}

function proposalStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function proposalErrorText(row: Record<string, unknown>): string {
  const direct = proposalFieldText(row, 'message', 'sanitizedReason', 'reason');
  if (direct) return direct;
  const error = proposalNestedRecord(row, 'error');
  return proposalFieldText(error, 'message', 'sanitizedReason', 'reason');
}

function ProposalDeliveryCard({ outcome, index }: { outcome: Record<string, unknown>; index: number }) {
  const provider = proposalFieldText(outcome, 'provider') || 'tracker';
  const externalKey = proposalFieldText(outcome, 'externalKey', 'external_key') || `Proposal ${index + 1}`;
  const externalUrl = proposalFieldText(outcome, 'externalUrl', 'external_url');
  const deliveryStatus = proposalFieldText(outcome, 'deliveryStatus', 'status') || 'available';
  const deliveredAt = proposalFieldText(outcome, 'deliveredAt', 'delivered_at');
  const lastSyncedAt = proposalFieldText(outcome, 'lastSyncedAt', 'last_synced_at');
  const duplicateSource = proposalFieldText(outcome, 'duplicateSource', 'duplicate_source');
  const created = outcome['created'];
  const errorText = proposalErrorText(outcome);
  const taskPreview = proposalNestedRecord(outcome, 'taskPreview', 'task_preview');
  const promotionResult = proposalNestedRecord(outcome, 'promotionResult', 'promotion_result');
  const taskSkills = proposalStringList(taskPreview['taskSkills'] ?? taskPreview['task_skills']);
  const promotedExecutionId = proposalFieldText(promotionResult, 'promotedExecutionId', 'promoted_execution_id');
  const promotedExecutionUrl = proposalFieldText(promotionResult, 'promotedExecutionUrl', 'promoted_execution_url');

  return (
    <div className="card">
      <strong>
        {externalUrl ? (
          <a href={externalUrl} target="_blank" rel="noreferrer">
            {provider}: {externalKey}
          </a>
        ) : (
          `${provider}: ${externalKey}`
        )}
      </strong>
      <div className="td-facts-grid" style={{ marginTop: '0.5rem' }}>
        <Fact label="Delivery Status">{formatStatusLabel(deliveryStatus)}</Fact>
        {deliveredAt ? <Fact label="Delivered">{formatWhen(deliveredAt)}</Fact> : null}
        {lastSyncedAt ? <Fact label="Last Sync">{formatWhen(lastSyncedAt)}</Fact> : null}
        {created === false ? <Fact label="Dedup">Updated existing issue</Fact> : null}
        {created === true ? <Fact label="Dedup">Created new issue</Fact> : null}
        {duplicateSource ? <Fact label="Dedup Source">{duplicateSource}</Fact> : null}
        {proposalFieldText(taskPreview, 'repository') ? (
          <Fact label="Repo">
            <code className="text-xs break-all">{proposalFieldText(taskPreview, 'repository')}</code>
          </Fact>
        ) : null}
        {proposalFieldText(taskPreview, 'runtimeMode', 'runtime_mode') ? (
          <Fact label="Runtime">{formatRuntimeLabel(proposalFieldText(taskPreview, 'runtimeMode', 'runtime_mode'))}</Fact>
        ) : null}
        {proposalFieldText(taskPreview, 'publishMode', 'publish_mode') ? (
          <Fact label="Publish Mode">{proposalFieldText(taskPreview, 'publishMode', 'publish_mode')}</Fact>
        ) : null}
        {proposalFieldText(taskPreview, 'priority') ? (
          <Fact label="Priority">{proposalFieldText(taskPreview, 'priority')}</Fact>
        ) : null}
        {proposalFieldText(taskPreview, 'maxAttempts', 'max_attempts') ? (
          <Fact label="Max Attempts">{proposalFieldText(taskPreview, 'maxAttempts', 'max_attempts')}</Fact>
        ) : null}
        {taskSkills.length > 0 ? <Fact label="Skills">{taskSkills.join(', ')}</Fact> : null}
        {proposalFieldText(taskPreview, 'skillId', 'skill_id') ? (
          <Fact label="Skill">{proposalFieldText(taskPreview, 'skillId', 'skill_id')}</Fact>
        ) : null}
        {proposalFieldText(taskPreview, 'presetProvenance', 'preset_provenance') ? (
          <Fact label="Preset">{proposalFieldText(taskPreview, 'presetProvenance', 'preset_provenance')}</Fact>
        ) : null}
        {promotedExecutionId ? (
          <Fact label="Promoted Run">
            {promotedExecutionUrl ? (
              <a href={promotedExecutionUrl}>{promotedExecutionId}</a>
            ) : (
              <code className="text-xs break-all">{promotedExecutionId}</code>
            )}
          </Fact>
        ) : null}
      </div>
      {errorText ? <p className="small whitespace-pre-wrap">{errorText}</p> : null}
    </div>
  );
}

function Card({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="card">
      <strong>{label}:</strong> <span className="break-words">{children}</span>
    </div>
  );
}

function MetricStrip({
  items,
}: {
  items: Array<{ label: string; value: ReactNode }>;
}) {
  const visibleItems = items.filter((item) => item.value !== null && item.value !== undefined && item.value !== '');
  if (visibleItems.length === 0) return null;
  return (
    <dl className="metric-strip">
      {visibleItems.map((item) => (
        <div key={item.label} className="metric-strip-item">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function FlatFactGrid({ children }: { children: ReactNode }) {
  return <dl className="flat-fact-grid">{children}</dl>;
}

function FactGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="td-group">
      <h4>{title}</h4>
      <FlatFactGrid>{children}</FlatFactGrid>
    </div>
  );
}

function SegmentedNav<T extends string>({
  items,
  active,
  ariaLabel,
  onNavigate,
}: {
  items: Array<SegmentedNavItem<T>>;
  active: T | null;
  ariaLabel: string;
  onNavigate: (value: T, href: string) => void;
}) {
  const activeIndex = items.findIndex((item) => item.value === active);
  return (
    <nav
      className={['segmented-control', activeIndex < 0 ? 'segmented-control-no-active' : ''].filter(Boolean).join(' ')}
      data-intensity="quiet"
      aria-label={ariaLabel}
      style={{
        '--segmented-control-count': items.length,
        '--segmented-control-active-index': Math.max(0, activeIndex),
      } as CSSProperties}
    >
      {items.map((item) => (
        <a
          key={item.value}
          className={['segmented-control-item', item.tone === 'quiet' ? 'segmented-control-item-quiet' : '']
            .filter(Boolean)
            .join(' ')}
          href={item.href}
          aria-current={active === item.value ? 'page' : undefined}
          onClick={(event) => {
            event.preventDefault();
            onNavigate(item.value, item.href);
          }}
        >
          {item.icon ? (
            <span className="segmented-control-item-icon" aria-hidden="true">{item.icon}</span>
          ) : null}
          <span className="segmented-control-item-label">{item.label}</span>
          {item.badge !== null && item.badge !== undefined ? (
            <span className="segmented-control-badge" aria-hidden="true">{item.badge}</span>
          ) : null}
        </a>
      ))}
    </nav>
  );
}

const KEBAB_ICON = (
  <svg
    aria-hidden="true"
    className="td-workflow-actions-trigger-icon"
    viewBox="0 0 16 16"
    focusable="false"
  >
    <circle cx="8" cy="3" r="1.5" />
    <circle cx="8" cy="8" r="1.5" />
    <circle cx="8" cy="13" r="1.5" />
  </svg>
);

function IconMenuButton({
  items,
  ariaLabel,
}: {
  items: WorkflowActionMenuItem[];
  ariaLabel: string;
}) {
  return (
    <WorkflowActionsMenu
      items={items}
      triggerContent={KEBAB_ICON}
      triggerAriaLabel={ariaLabel}
      triggerClassName="secondary td-workflow-actions-trigger td-workflow-actions-trigger-compact"
    />
  );
}

function workflowDuration(startedAt: string | null | undefined, closedAt: string | null | undefined): string {
  if (!startedAt || !closedAt) return '—';
  const started = new Date(startedAt).getTime();
  const closed = new Date(closedAt).getTime();
  if (Number.isNaN(started) || Number.isNaN(closed) || closed < started) return '—';
  return formatDurationMs(closed - started);
}

function githubRepositoryHref(repository: string | null | undefined): string | null {
  const normalized = String(repository || '').trim();
  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(normalized)) {
    return `https://github.com/${normalized}`;
  }
  return null;
}

function RepositoryFact({ repository }: { repository: string }) {
  const href = githubRepositoryHref(repository);
  const content = (
    <>
      <svg aria-hidden="true" className="td-repository-icon" viewBox="0 0 16 16" focusable="false">
        <path d="M4 2.5h6.5L13 5v8.5H4A1.5 1.5 0 0 1 2.5 12V4A1.5 1.5 0 0 1 4 2.5Zm6 1.2V5.5h1.8L10 3.7ZM4 4a.5.5 0 0 0-.5.5V12a.5.5 0 0 0 .5.5h8V6.5H9V4H4Zm1.25 4h4.5v1h-4.5V8Zm0 2h3.5v1h-3.5v-1Z" />
      </svg>
      <span className="td-repository-name">{repository}</span>
    </>
  );
  return (
    <span className="td-repository-value">
      {href ? (
        <a href={href} target="_blank" rel="noreferrer">
          {content}
        </a>
      ) : content}
    </span>
  );
}

function renderProviderProfileSummary(
  execution: z.infer<typeof ExecutionDetailSchema>,
): ReactNode {
  const providerLabel = execution.providerLabel?.trim();
  const providerId = execution.providerId?.trim();
  const profileId = execution.profileId?.trim();
  const primary = providerLabel || providerId || profileId;
  if (!primary) return '—';

  return (
    <span className="stack gap-1">
      <code className="text-xs break-all">{primary}</code>
      {profileId && profileId !== primary ? (
        <span className="small">
          Profile ID: <code className="text-xs break-all">{profileId}</code>
        </span>
      ) : null}
      {providerId && providerId !== primary ? (
        <span className="small">
          Provider ID: <code className="text-xs break-all">{providerId}</code>
        </span>
      ) : null}
    </span>
  );
}

function formatDependencyResolution(value: string | null | undefined): string {
  return formatStatusLabel(value);
}

function dependencyHref(workflowId: string): string {
  return `/workflows/${encodeURIComponent(workflowId)}?source=temporal`;
}

function MergeAutomationPanel({
  mergeAutomation,
}: {
  mergeAutomation: z.infer<typeof MergeAutomationSchema>;
}) {
  const workflowId = mergeAutomation.workflowId || mergeAutomation.childWorkflowId || '';
  const resolverChildren: Array<{
    workflowId: string;
    agentRunId?: string | null;
    status?: string | null;
    detailHref?: string | null;
  }> = mergeAutomation.resolverChildren?.length
    ? mergeAutomation.resolverChildren
    : (mergeAutomation.resolverChildWorkflowIds || []).map((childWorkflowId) => ({
        workflowId: childWorkflowId,
      }));
  const blockers = mergeAutomation.blockers || [];
  const artifactRefs = mergeAutomation.artifactRefs;

  return (
    <section className="stack">
      <h3>Merge Automation</h3>
      <div className="grid-2">
        <Card label="Status">{formatStatusLabel(mergeAutomation.status)}</Card>
        {mergeAutomation.cycles !== undefined && mergeAutomation.cycles !== null ? (
          <Card label="Cycles">{String(mergeAutomation.cycles)}</Card>
        ) : null}
        {mergeAutomation.prUrl ? (
          <Card label="PR Link">
            {(() => {
              const normalizedUrl = normalizeGitHubPullRequestUrl(mergeAutomation.prUrl);
              return normalizedUrl ? (
                <a href={normalizedUrl} target="_blank" rel="noreferrer">
                  {normalizedUrl}
                </a>
              ) : (
                '—'
              );
            })()}
          </Card>
        ) : null}
        {mergeAutomation.latestHeadSha ? (
          <Card label="Latest Head SHA">
            <code className="text-xs break-all">{mergeAutomation.latestHeadSha}</code>
          </Card>
        ) : null}
        {workflowId ? (
          <Card label="Child Workflow">
            <code className="text-xs break-all">{workflowId}</code>
          </Card>
        ) : null}
      </div>

      {resolverChildren.length ? (
        <div>
          <strong>Resolver Children</strong>
          <ul>
            {resolverChildren.map((child) => (
              <li key={child.workflowId}>
                <a href={child.detailHref || dependencyHref(child.workflowId)}>
                  <code className="text-xs break-all">{child.workflowId}</code>
                </a>
                {child.status ? <span className="small"> {formatStatusLabel(child.status)}</span> : null}
                {child.agentRunId ? (
                  <span className="small">
                    {' '}
                    logs: <code className="text-xs break-all">{child.agentRunId}</code>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="small">Waiting for required checks before launching pr-resolver.</p>
      )}

      {blockers.length ? (
        <div>
          <strong>Blockers</strong>
          <ul>
            {blockers.map((blocker, index) => (
              <li key={`${blocker.kind || 'blocker'}-${index}`}>
                {blocker.summary || blocker.kind || 'Blocked'}
                {blocker.source ? <span className="small"> ({blocker.source})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {artifactRefs ? (
        <div>
          <strong>Artifacts</strong>
          <ul>
            {artifactRefs.summary ? (
              <li>
                Summary: <code className="text-xs break-all">{artifactRefs.summary}</code>
              </li>
            ) : null}
            {artifactRefs.gateSnapshots?.map((artifactRef) => (
              <li key={`gate-${artifactRef}`}>
                Gate snapshot: <code className="text-xs break-all">{artifactRef}</code>
              </li>
            ))}
            {artifactRefs.resolverAttempts?.map((artifactRef) => (
              <li key={`resolver-${artifactRef}`}>
                Resolver attempt: <code className="text-xs break-all">{artifactRef}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function formatDebugValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function buildDebugFieldEntries(execution: z.infer<typeof ExecutionDetailSchema>) {
  const debugFields = execution.debugFields || {};
  const primaryEntries: Array<[string, unknown]> = [
    ['Workflow ID', debugFields.workflowId || execution.workflowId || execution.taskId],
    ['Current Run ID', debugFields.temporalRunId || execution.temporalRunId || execution.runId],
    ['Namespace', debugFields.namespace || execution.namespace],
    ['Temporal Status', debugFields.temporalStatus || execution.temporalStatus],
    ['Workflow State', debugFields.rawState || execution.rawState || execution.state],
    ['Close Status', debugFields.closeStatus ?? execution.closeStatus],
    ['Waiting Reason', debugFields.waitingReason ?? execution.waitingReason],
    ['Attention Required', debugFields.attentionRequired ?? execution.attentionRequired],
  ];
  const knownKeys = new Set([
    'workflowId',
    'temporalRunId',
    'namespace',
    'temporalStatus',
    'rawState',
    'closeStatus',
    'waitingReason',
    'attentionRequired',
  ]);
  const extraEntries = Object.entries(debugFields)
    .filter(([key]) => !knownKeys.has(key))
    .map(([key, value]) => [key, value] as [string, unknown]);
  return [...primaryEntries, ...extraEntries];
}

const TERMINAL_STATES = new Set(['succeeded', 'failed', 'canceled', 'cancelled', 'completed']);

type LogViewerState = 'not_available' | 'starting' | 'live' | 'ended' | 'error';

class ObservabilityRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ObservabilityRequestError';
    this.status = status;
  }
}

function buildObservabilityRequestError(status: number): ObservabilityRequestError {
  if (status === 403) {
    return new ObservabilityRequestError(status, 'You do not have permission to view observability for this run.');
  }
  return new ObservabilityRequestError(status, `Observability request failed: ${status}`);
}

async function fetchMergedTail(
  apiBase: string,
  agentRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/logs/merged`,
      agentRunRouteParams(agentRunId),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchStream(
  apiBase: string,
  agentRunId: string,
  stream: 'stdout' | 'stderr',
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/logs/${stream}`,
      agentRunRouteParams(agentRunId),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchDiagnostics(
  apiBase: string,
  agentRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/diagnostics`,
      agentRunRouteParams(agentRunId),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchRunSummaryArtifact(
  apiBase: string,
  artifactId: string,
): Promise<z.infer<typeof RunSummaryArtifactSchema> | null> {
  const resp = await fetch(
    `${apiBase}/artifacts/${encodeURIComponent(artifactId)}/download`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Run summary: ${resp.statusText}`);
  }
  const text = await resp.text();
  if (!text.trim()) return null;
  return RunSummaryArtifactSchema.parse(JSON.parse(text));
}

async function fetchArtifactSessionProjection(
  apiBase: string,
  agentRunId: string,
  sessionId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionProjectionSchema> | null> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}`,
      agentRunRouteParams(agentRunId, { sessionId }),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Session continuity: ${resp.status}`);
  }
  return ArtifactSessionProjectionSchema.parse(await resp.json());
}

async function fetchSessionResources(
  apiBase: string,
  agentRunId: string,
  sessionId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof SessionResourceListSchema> | null> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/sessions/${encodeURIComponent(sessionId)}/resources`,
      agentRunRouteParams(agentRunId, { sessionId }),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Session resources: ${resp.status}`);
  }
  return SessionResourceListSchema.parse(await resp.json());
}

async function controlArtifactSession(
  apiBase: string,
  agentRunId: string,
  sessionId: string,
  body: ArtifactSessionControlRequest,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionControlResponseSchema>> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}/control`,
      agentRunRouteParams(agentRunId, { sessionId }),
    ),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    let detail = `Session control: ${resp.status}`;
    try {
      const payload = await resp.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        detail = payload.detail;
      }
    } catch {
      // Keep the status fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  return ArtifactSessionControlResponseSchema.parse(await resp.json());
}

function chatSessionMessageEventToControlRequest(
  event: ChatSessionMessageEvent,
): ArtifactSessionControlRequest {
  return {
    schemaVersion: 1,
    controlRequestId: event.clientEventKey,
    idempotencyKey: event.clientEventKey,
    action: 'continue_same_session',
    message: event.message,
    expectedSessionEpoch: event.sessionEpoch,
  };
}

/** Fetch the observability summary for a agent run. */
async function fetchObservabilitySummary(
  apiBase: string,
  agentRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilitySummarySchema> | null> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/observability-summary`,
      agentRunRouteParams(agentRunId),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  const body = (await resp.json()) as { summary: Record<string, unknown> };
  return ObservabilitySummarySchema.parse(body.summary);
}

async function fetchObservabilityEvents(
  apiBase: string,
  agentRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilityEventsResponseSchema> | null> {
  const resp = await fetch(
    agentRunRoute(
      apiBase,
      routeTemplate,
      `/agent-runs/${encodeURIComponent(agentRunId)}/observability/events`,
      agentRunRouteParams(agentRunId),
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  return ObservabilityEventsResponseSchema.parse(await resp.json());
}

type BridgeSessionProjection = {
  bridgeSessionId: string;
  workflowId?: string | undefined;
  runId?: string | undefined;
  stepExecutionId?: string | undefined;
  agentRunId?: string | undefined;
  idempotencyKey?: string | undefined;
  status?: string | undefined;
  compatibilityProfile?: string | undefined;
  providerProfileId?: string | undefined;
  providerLeaseRef?: string | undefined;
  credentialGeneration?: number | undefined;
  hostBindingRef?: string | undefined;
  hostLeaseRef?: string | undefined;
  hostMode?: string | undefined;
  executionProfileRef?: string | undefined;
  launchPolicyRef?: string | undefined;
  effectiveLaunchSnapshotRef?: string | undefined;
  providerSessionRef?: string | undefined;
  omnigentHostRef?: string | undefined;
  omnigentRunnerRef?: string | undefined;
  firstMessageState?: string | undefined;
  capabilities: Record<string, boolean>;
};

function bridgeSessionRoute(apiBase: string, bridgeSessionId: string, suffix: 'events' | 'stream' | 'resources'): string {
  return joinApiBasePath(
    apiBase,
    `/omnigent/bridge-sessions/${encodeURIComponent(bridgeSessionId)}/${suffix}`,
  );
}

type BridgeResourceProjection = {
  completeness: string;
  groups: Array<{ groupKey: string; title: string; resources: Array<{
    label: string;
    artifactRef?: string;
    relatedArtifactRefs?: string[];
    path?: string;
    status: string;
    unavailableReason?: string;
    sourceEventSequence?: number;
    previewAvailable?: boolean;
    downloadAvailable?: boolean;
  }> }>;
};

type BridgeResource = BridgeResourceProjection['groups'][number]['resources'][number];

function chatBlockEventSequences(block: ProjectedChatBlock): Set<number> {
  const sequences = new Set<number>();
  for (const eventId of block.sourceEventIds) {
    const match = eventId.match(/^(\d+)-|(?::seq:|:)(\d+)(?::|$)/);
    const sequence = match?.[1] ?? match?.[2];
    if (sequence) sequences.add(Number(sequence));
  }
  return sequences;
}

function ContextualBridgeResourceLinks({
  apiBase,
  block,
  resources,
}: {
  apiBase: string;
  block: ProjectedChatBlock;
  resources: BridgeResource[];
}) {
  const sequences = chatBlockEventSequences(block);
  const contextual = resources.filter((resource) =>
    resource.sourceEventSequence != null && sequences.has(resource.sourceEventSequence));
  if (contextual.length === 0) return null;
  return <div className="timeline-artifact-links" aria-label="Resources announced by this event">
    {contextual.map((resource, index) => {
      const href = resource.artifactRef ? artifactRefHref(apiBase, resource.artifactRef) : null;
      return <span key={`${resource.label}-${index}`}>
        {href && resource.previewAvailable
          ? <a href={href} target="_blank" rel="noreferrer" aria-label={`Open ${resource.label}`}>Open {resource.label}</a>
          : <span>{resource.label}: {resource.status === 'pending' ? 'Harvesting…' : resource.unavailableReason || resource.status}</span>}
      </span>;
    })}
  </div>;
}

type BridgeTerminalEnvelope = {
  status: 'completed' | 'failed' | 'canceled' | 'timed_out';
  failureClass?: string | null | undefined;
  failureCode?: string | null | undefined;
  summary?: string | null | undefined;
  diagnosticsRef?: string | null | undefined;
  captureManifestRef?: string | null | undefined;
  initialSnapshotRef?: string | null | undefined;
  finalSnapshotRef?: string | null | undefined;
  rawEventsRef?: string | null | undefined;
  normalizedEventsRef?: string | null | undefined;
  externalStateRef?: string | null | undefined;
  cleanupState?: string | null | undefined;
  leaseReleaseState?: string | null | undefined;
  evidenceIncompleteReason?: string | null | undefined;
};

function BridgeTerminalEvidence({ apiBase, envelope }: { apiBase: string; envelope: BridgeTerminalEnvelope }) {
  const evidence = [
    ['Final snapshot', envelope.finalSnapshotRef],
    ['Capture manifest', envelope.captureManifestRef],
    ['Diagnostics', envelope.diagnosticsRef],
    ['Raw event journal', envelope.rawEventsRef],
    ['Normalized event journal', envelope.normalizedEventsRef],
    ['External-state evidence', envelope.externalStateRef],
  ] as const;
  const links = evidence.flatMap(([label, ref]) => {
    const href = ref ? artifactRefHref(apiBase, ref) : null;
    return href ? [{ label, href }] : [];
  });
  return <section className={`notice ${envelope.status === 'completed' ? '' : 'warning'}`} aria-label="Terminal outcome evidence">
    <strong>Terminal outcome: {formatStatusLabel(envelope.status)}</strong>
    {envelope.summary ? <p>{envelope.summary}</p> : null}
    {links.length > 0 ? <div className="button-group">
      {links.map(({ label, href }) => <a key={label} className="button secondary small" href={href} target="_blank" rel="noreferrer" aria-label={`Open terminal ${label.toLowerCase()}`}>{label}</a>)}
    </div> : null}
    {envelope.evidenceIncompleteReason ? <p className="small">Evidence incomplete: {envelope.evidenceIncompleteReason}</p> : null}
    {envelope.cleanupState || envelope.leaseReleaseState ? <p className="small">{[
      envelope.cleanupState ? `Cleanup: ${formatStatusLabel(envelope.cleanupState)}` : null,
      envelope.leaseReleaseState ? `Lease release: ${formatStatusLabel(envelope.leaseReleaseState)}` : null,
    ].filter(Boolean).join(' — ')}</p> : null}
    {envelope.failureClass || envelope.failureCode ? <p className="small">Failure: {[envelope.failureClass, envelope.failureCode].filter(Boolean).join(' — ')}</p> : null}
  </section>;
}

async function fetchBridgeSessionResources(apiBase: string, bridgeSessionId: string): Promise<BridgeResourceProjection> {
  const resp = await fetch(bridgeSessionRoute(apiBase, bridgeSessionId, 'resources'), { credentials: 'include' });
  if (!resp.ok) throw buildObservabilityRequestError(resp.status);
  const body = (await resp.json()) as Partial<BridgeResourceProjection>;
  return {
    completeness: body.completeness || 'pending',
    groups: Array.isArray(body.groups) ? body.groups : [],
  };
}

async function resolveBridgeSessionProjection({
  apiBase,
  workflowId,
  agentRunId,
  idempotencyKey,
}: {
  apiBase: string;
  workflowId?: string | null;
  agentRunId?: string | null;
  idempotencyKey?: string | null;
}): Promise<BridgeSessionProjection | null> {
  const params = new URLSearchParams();
  if (workflowId) params.set('workflowId', workflowId);
  if (agentRunId) params.set('agentRunId', agentRunId);
  if (idempotencyKey) params.set('idempotencyKey', idempotencyKey);
  if (!params.toString()) return null;

  const resp = await fetch(
    joinApiBasePath(apiBase, `/omnigent/bridge-sessions/resolve?${params.toString()}`),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  const body = (await resp.json()) as Record<string, unknown>;
  const bridgeSessionId = typeof body.bridgeSessionId === 'string' ? body.bridgeSessionId.trim() : '';
  if (!bridgeSessionId) return null;
  return {
    bridgeSessionId,
    workflowId: typeof body.workflowId === 'string' ? body.workflowId : undefined,
    runId: typeof body.runId === 'string' ? body.runId : undefined,
    stepExecutionId: typeof body.stepExecutionId === 'string' ? body.stepExecutionId : undefined,
    agentRunId: typeof body.agentRunId === 'string' ? body.agentRunId : undefined,
    idempotencyKey: typeof body.idempotencyKey === 'string' ? body.idempotencyKey : undefined,
    status: typeof body.status === 'string' ? body.status : undefined,
    compatibilityProfile: typeof body.compatibilityProfile === 'string' ? body.compatibilityProfile : undefined,
    providerProfileId: typeof body.providerProfileId === 'string' ? body.providerProfileId : undefined,
    providerLeaseRef: typeof body.providerLeaseRef === 'string' ? body.providerLeaseRef : undefined,
    credentialGeneration: typeof body.credentialGeneration === 'number' ? body.credentialGeneration : undefined,
    hostBindingRef: typeof body.hostBindingRef === 'string' ? body.hostBindingRef : undefined,
    hostLeaseRef: typeof body.hostLeaseRef === 'string' ? body.hostLeaseRef : undefined,
    hostMode: typeof body.hostMode === 'string' ? body.hostMode : undefined,
    executionProfileRef: typeof body.executionProfileRef === 'string' ? body.executionProfileRef : undefined,
    launchPolicyRef: typeof body.launchPolicyRef === 'string' ? body.launchPolicyRef : undefined,
    effectiveLaunchSnapshotRef: typeof body.effectiveLaunchSnapshotRef === 'string' ? body.effectiveLaunchSnapshotRef : undefined,
    providerSessionRef: typeof body.providerSessionRef === 'string' ? body.providerSessionRef : undefined,
    omnigentHostRef: typeof body.omnigentHostRef === 'string' ? body.omnigentHostRef : undefined,
    omnigentRunnerRef: typeof body.omnigentRunnerRef === 'string' ? body.omnigentRunnerRef : undefined,
    firstMessageState: typeof body.firstMessageState === 'string' ? body.firstMessageState : undefined,
    capabilities: body.capabilities && typeof body.capabilities === 'object'
      ? Object.fromEntries(Object.entries(body.capabilities).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'))
      : {},
  };
}

async function postBridgeSessionControl(
  apiBase: string,
  providerSessionRef: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const resp = await fetch(joinApiBasePath(apiBase, `/omnigent/v1/sessions/${encodeURIComponent(providerSessionRef)}/events`), {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!resp.ok) throw buildObservabilityRequestError(resp.status);
}

async function resolveBridgeElicitation(
  apiBase: string,
  providerSessionRef: string,
  elicitationId: string,
  decision: 'approved' | 'rejected',
): Promise<void> {
  const resp = await fetch(joinApiBasePath(
    apiBase,
    `/omnigent/v1/sessions/${encodeURIComponent(providerSessionRef)}/elicitations/${encodeURIComponent(elicitationId)}/resolve`,
  ), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  });
  if (!resp.ok) throw buildObservabilityRequestError(resp.status);
}

async function fetchBridgeSessionEvents(
  apiBase: string,
  bridgeSessionId: string,
): Promise<z.infer<typeof ObservabilityEventsResponseSchema> | null> {
  const events: z.infer<typeof ObservabilityEventSchema>[] = [];
  let cursor: string | null = null;
  let truncated = false;
  let terminalEnvelope: z.infer<typeof BridgeSessionEventsPageSchema>['terminalEnvelope'] = null;
  let terminal = false;
  do {
    const route = bridgeSessionRoute(apiBase, bridgeSessionId, 'events');
    const url = cursor ? `${route}?cursor=${encodeURIComponent(cursor)}` : route;
    const resp = await fetch(url, { credentials: 'include' });
    if (!resp.ok) {
      if (resp.status === 404) return null;
      throw buildObservabilityRequestError(resp.status);
    }
    const page = BridgeSessionEventsPageSchema.parse(await resp.json());
    events.push(...page.events);
    truncated ||= page.truncated;
    terminal ||= page.terminal;
    terminalEnvelope = page.terminalEnvelope ?? terminalEnvelope;
    cursor = page.hasMore ? page.nextCursor : null;
    if (page.hasMore && cursor == null) {
      throw new Error('Bridge event page hasMore without nextCursor');
    }
  } while (cursor != null);
  return { events, truncated, sessionSnapshot: undefined, terminal, terminalEnvelope };
}

async function fetchStepLedger(stepsHref: string): Promise<z.infer<typeof StepLedgerSnapshotSchema>> {
  const resp = await fetch(stepsHref, { credentials: 'include' });
  if (!resp.ok) {
    const statusText = resp.statusText.trim();
    const detail = statusText ? ` ${statusText}` : '';
    throw new Error(`Steps: ${resp.status}${detail} (${stepsHref})`);
  }
  return StepLedgerSnapshotSchema.parse(await resp.json());
}

async function fetchCheckpointBranches(
  apiBase: string,
  workflowId: string,
): Promise<z.infer<typeof CheckpointBranchListSchema>> {
  const resp = await fetch(
    `${apiBase}/executions/${encodeURIComponent(workflowId)}/checkpoint-branches`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    const statusText = resp.statusText.trim();
    const detail = statusText ? ` ${statusText}` : '';
    throw new Error(`Checkpoint branches: ${resp.status}${detail}`);
  }
  return CheckpointBranchListSchema.parse(await resp.json());
}

async function fetchCheckpointBranchTurns(
  apiBase: string,
  workflowId: string,
  branchId: string,
): Promise<z.infer<typeof CheckpointBranchTurnListSchema>> {
  const resp = await fetch(
    `${apiBase}/executions/${encodeURIComponent(workflowId)}/checkpoint-branches/${encodeURIComponent(branchId)}/turns`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    const statusText = resp.statusText.trim();
    const detail = statusText ? ` ${statusText}` : '';
    throw new Error(`Branch turns: ${resp.status}${detail}`);
  }
  return CheckpointBranchTurnListSchema.parse(await resp.json());
}

const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'no_commit',
  'failed',
  'canceled',
  'cancelled',
  'timed_out',
]);

function isExecutionTerminal(
  execution: z.infer<typeof ExecutionDetailSchema> | null | undefined,
): boolean {
  if (!execution) {
    return false;
  }
  const lifecycleState = (execution.rawState || execution.state || execution.status || '').toLowerCase();
  const temporalStatus = (execution.temporalStatus || execution.closeStatus || '').toLowerCase();
  return Boolean(
    execution.closedAt ||
      TERMINAL_STATES.has(lifecycleState) ||
      TERMINAL_RUN_STATUSES.has(lifecycleState) ||
      TERMINAL_RUN_STATUSES.has(temporalStatus),
  );
}

export function workflowEvidenceStaleTime({
  detailPoll,
}: {
  isTerminal: boolean;
  detailPoll: number;
}): number {
  return Math.max(detailPoll, 5000);
}

export function workflowDetailQueryOptions({
  apiBase,
  workflowId,
  sourceTemporal,
  detailPoll,
}: {
  apiBase: string;
  workflowId: string | null | undefined;
  sourceTemporal: boolean;
  detailPoll: number;
}) {
  const encodedWorkflowId = workflowId ? encodeURIComponent(workflowId) : null;
  return {
    queryKey: ['workflow-detail', encodedWorkflowId, sourceTemporal] as const,
    enabled: Boolean(encodedWorkflowId),
    queryFn: async () => {
      if (!encodedWorkflowId) {
        throw new Error('Workflow ID is required.');
      }
      const suffix = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(`${apiBase}/executions/${encodedWorkflowId}${suffix}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch workflow: ${response.statusText}`);
      }
      return ExecutionDetailSchema.parse(await response.json());
    },
    refetchInterval: (query: { state: { data: z.infer<typeof ExecutionDetailSchema> | undefined } }) => (
      !isExecutionTerminal(query.state.data) ? detailPoll : false
    ),
    staleTime: detailPoll,
  };
}

function usePageVisibility() {
  const [isVisible, setIsVisible] = useState(!document.hidden);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden);
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  return isVisible;
}

type ObservabilityEvent = z.infer<typeof ObservabilityEventSchema>;
type SessionSnapshot = z.infer<typeof SessionSnapshotSchema>;
type TimelineStream = 'stdout' | 'stderr' | 'system' | 'session' | 'unknown';
type TimelineRow = {
  id: string;
  text: string;
  stream: TimelineStream;
  kind: string | null;
  sequence: number | null;
  timestamp: string | null;
  sessionId: string | null;
  sessionEpoch: number | null;
  containerId: string | null;
  threadId: string | null;
  turnId: string | null;
  activeTurnId: string | null;
  metadata: Record<string, unknown>;
  rowType:
    | 'output'
    | 'system'
    | 'session'
    | 'approval'
    | 'publication'
    | 'boundary'
    | 'user'
    | 'assistant'
    | 'tool'
    | 'turn'
    | 'turn-failure'
    | 'fallback';
};

function splitLogText(content: string): string[] {
  if (!content) return [];
  const normalized = content.endsWith('\n') ? content.slice(0, -1) : content;
  return normalized ? normalized.split('\n') : [];
}

function copyTextToClipboard(text: string): void {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    return;
  }
  try {
    const maybePromise = navigator.clipboard.writeText(text);
    if (maybePromise && typeof maybePromise.catch === 'function') {
      void maybePromise.catch(() => {});
    }
  } catch {
    // Ignore synchronous clipboard failures for now; the UI should stay stable.
  }
}

function parseArtifactToRows(content: string): TimelineRow[] {
  const lines = splitLogText(content);
  let currentStream: TimelineStream = 'unknown';

  return lines.map((line, i) => {
    if (line.startsWith('--- stdout ---')) currentStream = 'stdout';
    else if (line.startsWith('--- stderr ---')) currentStream = 'stderr';
    else if (line.startsWith('--- system ---')) currentStream = 'system';
    else if (line.startsWith('--- session ---')) currentStream = 'session';

    return {
      id: `artifact-${i}`,
      text: line,
      stream: currentStream,
      kind: null,
      sequence: null,
      timestamp: null,
      sessionId: null,
      sessionEpoch: null,
      containerId: null,
      threadId: null,
      turnId: null,
      activeTurnId: null,
      metadata: {},
      rowType: 'fallback',
    };
  });
}

const USER_MESSAGE_EVENT_KINDS = new Set(['user_message_submitted']);
const ASSISTANT_MESSAGE_EVENT_KINDS = new Set([
  'assistant_message_delta',
  'assistant_message_completed',
  'assistant_message',
]);
const TOOL_CALL_EVENT_KINDS = new Set([
  'tool_call_started',
  'tool_call_output',
  'tool_call_completed',
  'tool_call_failed',
]);
const TURN_BOUNDARY_EVENT_KINDS = new Set(['turn_started', 'turn_completed']);
const TURN_FAILURE_EVENT_KINDS = new Set(['turn_failed', 'turn_interrupted']);
const OPERATOR_ATTENTION_EVENT_KINDS = new Set([
  'approval_requested',
  'approval_resolved',
  'approval_granted',
  'approval_denied',
  'intervention_requested',
  'intervention_resolved',
]);

export function classifyTimelineRow(event: ObservabilityEvent): TimelineRow['rowType'] {
  const kind = event.kind ?? '';
  if (kind === 'session_reset_boundary' || kind === 'session_cleared') {
    return 'boundary';
  }
  if (USER_MESSAGE_EVENT_KINDS.has(kind)) {
    return 'user';
  }
  if (ASSISTANT_MESSAGE_EVENT_KINDS.has(kind)) {
    return 'assistant';
  }
  if (TOOL_CALL_EVENT_KINDS.has(kind)) {
    return 'tool';
  }
  if (TURN_FAILURE_EVENT_KINDS.has(kind)) {
    return 'turn-failure';
  }
  if (TURN_BOUNDARY_EVENT_KINDS.has(kind)) {
    return 'turn';
  }
  if (OPERATOR_ATTENTION_EVENT_KINDS.has(kind)) {
    return 'approval';
  }
  if (event.stream === 'system' || kind.startsWith('lifecycle_')) {
    return 'system';
  }
  if (event.stream === 'session') {
    if (kind.startsWith('approval_') || kind.startsWith('intervention_')) {
      return 'approval';
    }
    if (kind.endsWith('_published')) {
      return 'publication';
    }
    return 'session';
  }
  return 'output';
}

function eventToTimelineRows(event: ObservabilityEvent): TimelineRow[] {
  const stream = event.stream as TimelineStream;
  const rowType = classifyTimelineRow(event);
  const lines = splitLogText(event.text);
  const sourceLines = lines.length > 0 ? lines : [event.text];
  return sourceLines.map((line, index) => ({
    id: `${event.sequence}-${index}-${event.kind ?? 'event'}`,
    text: line,
    stream,
    kind: event.kind ?? null,
    sequence: event.sequence,
    timestamp: event.timestamp ?? null,
    sessionId: event.session_id ?? null,
    sessionEpoch: event.session_epoch ?? null,
    containerId: event.container_id ?? null,
    threadId: event.thread_id ?? null,
    turnId: event.turn_id ?? null,
    activeTurnId: event.active_turn_id ?? null,
    metadata: event.metadata ?? {},
    rowType,
  }));
}

function mapEventsToTimelineRows(
  payload: z.infer<typeof ObservabilityEventsResponseSchema> | null | undefined,
): TimelineRow[] {
  if (!payload) return [];
  return payload.events.flatMap((event) => eventToTimelineRows(event));
}

function timelineRowsToObservabilityRows(rows: TimelineRow[]): RunObservabilityEventRow[] {
  return rows
    .filter((row) => (
      row.rowType !== 'fallback'
      && row.rowType !== 'output'
      && (
        row.rowType !== 'system'
        || row.kind?.startsWith('lifecycle.')
        || row.kind?.startsWith('lifecycle_')
      )
    ))
    .map((row) => ({
      id: row.id,
      runId: null,
      agentRunId: null,
      sequence: row.sequence,
      timestamp: row.timestamp,
      stream: row.stream,
      text: row.text,
      kind: row.kind?.startsWith('lifecycle_')
        ? `lifecycle.${row.kind.slice('lifecycle_'.length)}`
        : row.kind,
      sessionId: row.sessionId,
      sessionEpoch: row.sessionEpoch,
      turnId: row.turnId,
      activeTurnId: row.activeTurnId,
      metadata: row.metadata,
    }));
}

function optimisticMessagesToChatSeeds(
  agentRunId: string,
  messages: OptimisticChatSessionMessage[],
): OptimisticUserMessage[] {
  return messages.map((message) => ({
    key: message.clientEventKey,
    agentRunId,
    text: message.message,
    sessionId: message.sessionId,
    sessionEpoch: message.sessionEpoch,
    timestamp: undefined,
  }));
}

export function reduceTimelineRowsToChatBlocks(
  rows: TimelineRow[],
  agentRunId = '',
  optimisticMessages: OptimisticChatSessionMessage[] = [],
): ProjectedChatBlock[] {
  return projectChatSessionBlocks(
    timelineRowsToObservabilityRows(rows),
    agentRunId,
    optimisticMessagesToChatSeeds(agentRunId, optimisticMessages),
  ).blocks;
}

function deriveSessionSnapshotFromEvent(
  event: ObservabilityEvent,
  previous: SessionSnapshot | null,
): SessionSnapshot | null {
  if (!event.session_id || typeof event.session_epoch !== 'number') {
    return previous;
  }
  return {
    sessionId: event.session_id,
    sessionEpoch: event.session_epoch,
    containerId: event.container_id ?? previous?.containerId ?? '',
    threadId: event.thread_id ?? previous?.threadId ?? '',
    activeTurnId: event.active_turn_id ?? previous?.activeTurnId ?? null,
    status: previous?.status,
    latestSummaryRef: previous?.latestSummaryRef ?? null,
    latestCheckpointRef: previous?.latestCheckpointRef ?? null,
    latestControlEventRef: previous?.latestControlEventRef ?? null,
    latestResetBoundaryRef: previous?.latestResetBoundaryRef ?? null,
  };
}

function renderAnsiFragments(text: string): ReactNode {
  const fragments = Anser.ansiToJson(text, { json: true, remove_empty: true });
  if (fragments.length === 0) {
    return text;
  }
  return fragments.map((fragment, index) => {
    const style: Record<string, string> = {};
    const foreground = fragment.fg_truecolor || fragment.fg;
    const background = fragment.bg_truecolor || fragment.bg;
    if (foreground) {
      style.color = foreground;
    }
    if (background) {
      style.backgroundColor = background;
    }
    if (fragment.decorations.includes('bold')) {
      style.fontWeight = '700';
    }
    if (fragment.decorations.includes('italic')) {
      style.fontStyle = 'italic';
    }
    const textDecoration = [
      fragment.decorations.includes('underline') ? 'underline' : null,
      fragment.decorations.includes('strikethrough') ? 'line-through' : null,
    ]
      .filter(Boolean)
      .join(' ');
    if (textDecoration) {
      style.textDecoration = textDecoration;
    }
    return (
      <span key={`${fragment.content}-${index}`} data-ansi-fragment="true" style={style}>
        {fragment.content}
      </span>
    );
  });
}

function renderTimelineRowText(row: TimelineRow, timelineViewerEnabled: boolean): ReactNode {
  if (!timelineViewerEnabled) {
    return row.text;
  }
  if (row.stream === 'stdout' || row.stream === 'stderr') {
    return renderAnsiFragments(row.text);
  }
  return row.text;
}

function getTimelineRowTreatmentLabel(row: TimelineRow): string | null {
  if (row.rowType === 'user') {
    return 'User turn';
  }
  if (row.rowType === 'assistant') {
    return 'Assistant output';
  }
  if (row.rowType === 'tool') {
    if (row.kind === 'tool_call_failed') return 'Tool failed';
    if (row.kind === 'tool_call_completed') return 'Tool completed';
    if (row.kind === 'tool_call_output') return 'Tool output';
    return 'Tool call';
  }
  if (row.rowType === 'approval') {
    return row.kind?.startsWith('intervention_') ? 'Operator intervention' : 'Operator approval';
  }
  if (row.rowType === 'turn-failure') {
    return row.kind === 'turn_interrupted' ? 'Turn interrupted' : 'Turn failed';
  }
  if (row.rowType === 'turn') {
    return row.kind === 'turn_completed' ? 'Turn completed' : 'Turn started';
  }
  return null;
}

function getCopyableRowText(row: TimelineRow): string {
  if (row.stream === 'stdout' || row.stream === 'stderr') {
    return Anser.ansiToText(row.text, { remove_empty: true });
  }
  return row.text;
}

type TimelineArtifactLink = {
  key: string;
  label: string;
  href: string;
};

function TimelineArtifactLinks({ links }: { links: TimelineArtifactLink[] }): ReactNode {
  if (links.length === 0) {
    return null;
  }

  return (
    <div className="live-logs-artifact-links">
      {links.map((link) => (
        <a
          key={link.key}
          className="live-logs-artifact-link"
          href={link.href}
          target="_blank"
          rel="noreferrer"
          aria-label={link.label}
        >
          {link.label}
        </a>
      ))}
    </div>
  );
}

function coerceArtifactRef(value: unknown): string | null {
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized || null;
  }
  if (value && typeof value === 'object') {
    const candidate = (value as { artifactRef?: unknown; artifact_id?: unknown; artifactId?: unknown }).artifactRef
      ?? (value as { artifactRef?: unknown; artifact_id?: unknown; artifactId?: unknown }).artifact_id
      ?? (value as { artifactRef?: unknown; artifact_id?: unknown; artifactId?: unknown }).artifactId;
    if (typeof candidate === 'string') {
      const normalized = candidate.trim();
      return normalized || null;
    }
  }
  return null;
}

function buildArtifactDownloadHref(apiBase: string, artifactId: string): string {
  return joinApiBasePath(apiBase, `/artifacts/${encodeURIComponent(artifactId)}/download`);
}

function artifactDownloadHref(
  apiBase: string,
  artifact: z.infer<typeof ArtifactSummarySchema>,
  options: { preferMoonMindEndpoint?: boolean } = {},
): string {
  if (!options.preferMoonMindEndpoint && artifact.downloadUrl) {
    return artifact.downloadUrl;
  }
  return buildArtifactDownloadHref(apiBase, artifact.artifactId);
}

function reportOpenHref(
  apiBase: string,
  artifact: z.infer<typeof ArtifactSummarySchema>,
): string {
  const defaultReadArtifactId = artifact.defaultReadRef?.artifactId;
  if (defaultReadArtifactId) {
    return buildArtifactDownloadHref(apiBase, defaultReadArtifactId);
  }
  return artifactDownloadHref(apiBase, artifact);
}

function metadataString(metadata: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'string') {
      const normalized = value.trim();
      if (normalized) {
        return normalized;
      }
    }
  }
  return '';
}

function metadataStrings(metadata: Record<string, unknown>, ...keys: string[]): string {
  return keys
    .map((key) => {
      const value = metadata[key];
      return typeof value === 'string' ? value.trim() : '';
    })
    .filter(Boolean)
    .join(' ');
}

function artifactReportLinkType(
  artifact: z.infer<typeof ArtifactSummarySchema>,
): string | null {
  return artifact.links.find((link) => link.linkType.startsWith('report.'))?.linkType ?? null;
}

function artifactReportLinkLabel(
  artifact: z.infer<typeof ArtifactSummarySchema>,
): string {
  return artifact.links.find((link) => link.linkType.startsWith('report.'))?.label || '';
}

function reportArtifactTitle(artifact: z.infer<typeof ArtifactSummarySchema>): string {
  return (
    metadataString(artifact.metadata, 'title', 'name') ||
    artifactReportLinkLabel(artifact) ||
    artifact.artifactId
  );
}

function reportViewerLabel(artifact: z.infer<typeof ArtifactSummarySchema>): string {
  const renderHint = metadataString(artifact.metadata, 'render_hint', 'renderHint').toLowerCase();
  if (renderHint) return renderHint;
  const contentType = String(artifact.contentType || '').toLowerCase();
  if (contentType.includes('markdown')) return 'markdown';
  if (contentType.includes('json')) return 'json';
  if (contentType.startsWith('text/x-diff')) return 'diff';
  if (contentType.startsWith('text/')) return 'text';
  if (contentType.startsWith('image/')) return 'image';
  if (contentType.includes('pdf')) return 'pdf';
  return 'download';
}

function relatedReportArtifacts(
  artifacts: z.infer<typeof ArtifactSummarySchema>[],
): z.infer<typeof ArtifactSummarySchema>[] {
  const relatedTypes = new Set(['report.summary', 'report.structured', 'report.evidence']);
  return artifacts.filter((artifact) => {
    const linkType = artifactReportLinkType(artifact);
    return linkType ? relatedTypes.has(linkType) : false;
  });
}

type InputImageArtifact = {
  artifact: z.infer<typeof ArtifactSummarySchema>;
  targetKey: string;
  targetLabel: string;
  filename: string;
};

function inputImageArtifactFrom(artifact: z.infer<typeof ArtifactSummarySchema>): InputImageArtifact | null {
  const contentType = String(artifact.contentType || '').trim().toLowerCase();
  if (!contentType.startsWith('image/')) {
    return null;
  }
  const metadata = artifact.metadata || {};
  const source = metadataString(metadata, 'source');
  if (
    source !== 'workflow-console-objective-attachment' &&
    source !== 'workflow-console-step-attachment'
  ) {
    return null;
  }

  const target = metadataString(metadata, 'target', 'targetKind', 'target_kind').toLowerCase();
  if (target === 'objective') {
    return {
      artifact,
      targetKey: 'objective',
      targetLabel: 'Objective',
      filename: metadataString(metadata, 'filename', 'name', 'label') || artifact.artifactId,
    };
  }

  if (source === 'workflow-console-step-attachment') {
    const stepLabel = metadataString(metadata, 'stepLabel', 'step_label');
    if (!stepLabel) {
      return null;
    }
    return {
      artifact,
      targetKey: `step:${stepLabel}`,
      targetLabel: stepLabel,
      filename: metadataString(metadata, 'filename', 'name', 'label') || artifact.artifactId,
    };
  }

  return null;
}

function InputImagesSection({
  artifacts,
  apiBase,
}: {
  artifacts: z.infer<typeof ArtifactSummarySchema>[];
  apiBase: string;
}) {
  const [failedPreviewIds, setFailedPreviewIds] = useState<Record<string, boolean>>({});
  const inputImages = artifacts
    .map((artifact) => inputImageArtifactFrom(artifact))
    .filter((item): item is InputImageArtifact => item !== null);

  if (inputImages.length === 0) {
    return null;
  }

  const groups = Object.values(
    inputImages.reduce<Record<string, { key: string; label: string; items: InputImageArtifact[] }>>(
      (acc, item) => {
        const group =
          acc[item.targetKey] ||
          (acc[item.targetKey] = { key: item.targetKey, label: item.targetLabel, items: [] });
        group.items.push(item);
        return acc;
      },
      {},
    ),
  );

  return (
    <section className="stack">
      <h3>Input Images</h3>
      <p className="small">
        Images are grouped by the persisted workflow target from the execution snapshot.
      </p>
      <div className="queue-step-attachments">
        {groups.map((group) => (
          <div key={group.key} className="queue-attachment-target">
            <h4>{group.label}</h4>
            <ul className="list queue-step-attachments-list">
              {group.items.map(({ artifact, filename }) => {
                const href = artifactDownloadHref(apiBase, artifact, {
                  preferMoonMindEndpoint: true,
                });
                const failed = Boolean(failedPreviewIds[artifact.artifactId]);
                return (
                  <li key={artifact.artifactId}>
                    <span>
                      <strong>{filename}</strong>{' '}
                      <span className="small">
                        {`${artifact.contentType || 'image'}, ${artifact.sizeBytes ?? '—'} bytes`}
                      </span>
                    </span>
                    {!failed ? (
                      <img
                        alt={`Preview of ${group.label} attachment ${filename}`}
                        className="queue-attachment-preview"
                        src={href}
                        onError={() =>
                          setFailedPreviewIds((current) => ({
                            ...current,
                            [artifact.artifactId]: true,
                          }))
                        }
                      />
                    ) : (
                      <p className="small notice error">
                        {`${group.label}: Preview unavailable for ${filename}. Attachment metadata remains available.`}
                      </p>
                    )}
                    <a
                      className="button secondary"
                      href={href}
                      download={filename}
                    >
                      Download
                    </a>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function ReportPresentationSection({
  primaryReport,
  relatedArtifacts,
  apiBase,
}: {
  primaryReport: z.infer<typeof ArtifactSummarySchema> | null;
  relatedArtifacts: z.infer<typeof ArtifactSummarySchema>[];
  apiBase: string;
}) {
  if (!primaryReport) {
    return null;
  }

  const reportType = metadataString(primaryReport.metadata, 'report_type', 'reportType');
  const reportScope = metadataString(primaryReport.metadata, 'report_scope', 'reportScope');

  return (
    <section className="stack td-report-region td-evidence-region">
      <div>
        <h3>Report</h3>
        <p className="small">
          Canonical final report selected from server report artifact linkage.
        </p>
      </div>
      <div className="td-evidence-slab stack">
        <div className="grid-2">
          <Card label="Title">{reportArtifactTitle(primaryReport)}</Card>
          <Card label="Viewer">{reportViewerLabel(primaryReport)}</Card>
          {reportType ? <Card label="Report Type">{reportType}</Card> : null}
          {reportScope ? <Card label="Report Scope">{reportScope}</Card> : null}
        </div>
        <div className="actions">
          <a
            className="button secondary"
            href={reportOpenHref(apiBase, primaryReport)}
            title="Open report"
          >
            Open report
          </a>
        </div>
      </div>
      {relatedArtifacts.length > 0 ? (
        <div className="stack">
          <h4>Related Report Content</h4>
          <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
            <table>
              <thead>
                <tr>
                  <th>Content</th>
                  <th>Type</th>
                  <th>Viewer</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {relatedArtifacts.map((artifact) => (
                  <tr key={artifact.artifactId}>
                    <td>{reportArtifactTitle(artifact)}</td>
                    <td>{artifactReportLinkType(artifact) || 'report'}</td>
                    <td>{reportViewerLabel(artifact)}</td>
                    <td>
                      <a
                        className="button secondary"
                        href={reportOpenHref(apiBase, artifact)}
                        title={`Open ${artifactReportLinkLabel(artifact) || 'report content'}`}
                      >
                        Open {artifactReportLinkLabel(artifact) || 'content'}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function buildTimelineArtifactLinks(row: TimelineRow, apiBase: string): TimelineArtifactLink[] {
  const links: TimelineArtifactLink[] = [];
  const seen = new Set<string>();
  const addLink = (label: string, value: unknown) => {
    const artifactId = coerceArtifactRef(value);
    if (!artifactId) return;
    const key = `${label}:${artifactId}`;
    if (seen.has(key)) return;
    seen.add(key);
    links.push({
      key,
      label,
      href: buildArtifactDownloadHref(apiBase, artifactId),
    });
  };

  if (row.kind === 'summary_published') {
    addLink('Open summary artifact', row.metadata?.summaryRef ?? row.metadata?.artifactRef);
  }
  if (row.kind === 'checkpoint_published') {
    addLink('Open checkpoint artifact', row.metadata?.checkpointRef ?? row.metadata?.artifactRef);
  }
  if (row.kind === 'session_cleared' || row.kind === 'session_reset_boundary') {
    addLink(
      'Open control event artifact',
      row.metadata?.controlEventRef ?? (row.kind === 'session_cleared' ? row.metadata?.artifactRef : null),
    );
    addLink(
      'Open reset boundary artifact',
      row.metadata?.resetBoundaryRef ?? (row.kind === 'session_reset_boundary' ? row.metadata?.artifactRef : null),
    );
  }
  if (row.metadata?.terminalStatus) {
    addLink('Open diagnostics', row.metadata?.diagnosticsRef);
    addLink('Open capture manifest', row.metadata?.captureManifestRef);
    addLink('Open initial snapshot', row.metadata?.initialSnapshotRef);
    addLink('Open final snapshot', row.metadata?.finalSnapshotRef);
    addLink('Open raw events', row.metadata?.rawEventsRef);
    addLink('Open normalized events', row.metadata?.normalizedEventsRef);
    addLink('Open external state', row.metadata?.externalStateRef);
  }

  return links;
}

function renderTimelineRow(
  row: TimelineRow,
  wrapLines: boolean,
  timelineViewerEnabled: boolean,
  apiBase: string,
): ReactNode {
  const rowClasses = [
    'live-logs-row',
    `live-logs-row-${row.rowType}`,
    `live-logs-stream-${row.stream}`,
    wrapLines ? 'is-wrapped' : 'is-unwrapped',
  ].join(' ');
  const artifactLinks = timelineViewerEnabled ? buildTimelineArtifactLinks(row, apiBase) : [];

  if (timelineViewerEnabled && row.rowType === 'boundary') {
    return (
      <div
        key={row.id}
        className={rowClasses}
      >
        <div className="live-logs-boundary-label">Session reset boundary</div>
        <div
          className="live-logs-row-text"
          data-stream={row.stream}
          data-kind={row.kind ?? undefined}
          data-row-type={row.rowType}
        >
          {row.text}
        </div>
        <TimelineArtifactLinks links={artifactLinks} />
      </div>
    );
  }

  return (
    <div
      key={row.id}
      className={rowClasses}
    >
      {timelineViewerEnabled && (row.kind || getTimelineRowTreatmentLabel(row)) ? (
        <div className="live-logs-row-heading">
          {getTimelineRowTreatmentLabel(row) ? (
            <span className="live-logs-treatment-label">{getTimelineRowTreatmentLabel(row)}</span>
          ) : null}
          {row.kind ? (
            <span className="live-logs-kind-chip">{row.kind.replaceAll('_', ' ')}</span>
          ) : null}
        </div>
      ) : null}
      <div
        className="live-logs-row-text"
        data-stream={row.stream}
        data-kind={row.kind ?? undefined}
        data-row-type={row.rowType}
      >
        {renderTimelineRowText(row, timelineViewerEnabled)}
      </div>
      <TimelineArtifactLinks links={artifactLinks} />
    </div>
  );
}

function chatBlockLabel(block: ProjectedChatBlock): string {
  if (block.kind === 'user') return 'User';
  if (block.kind === 'assistant') return 'Assistant';
  if (block.kind === 'tool') return 'Tool';
  if (block.kind === 'approval') return 'Approval';
  if (block.kind === 'boundary') return 'Session boundary';
  if (block.kind === 'error') return 'Error';
  if (block.kind === 'system') return 'System';
  return 'Status';
}

function chatBlockKindLabel(block: ProjectedChatBlock): string | null {
  const kind = chatBlockSourceKind(block)
    ?? (typeof block.status === 'string'
      ? block.status
      : null);
  return kind ? kind.replaceAll('_', ' ') : null;
}

function chatBlockStatus(block: ProjectedChatBlock): string {
  return block.status || chatBlockSourceKind(block) || block.kind;
}

function chatBlockRowType(block: ProjectedChatBlock): string {
  const sourceKind = chatBlockSourceKind(block);
  if (sourceKind && sourceKind.endsWith('_published')) return 'publication';
  if (sourceKind === 'turn_started' || sourceKind === 'turn_completed') return 'turn';
  if (sourceKind === 'turn_failed' || sourceKind === 'turn_interrupted') return 'turn-failure';
  return block.kind;
}

function chatBlockSourceKind(block: ProjectedChatBlock): string | null {
  if (typeof block.metadata?.sourceKind === 'string') {
    return block.metadata.sourceKind;
  }
  for (const eventId of block.sourceEventIds) {
    const seqMatch = eventId.match(/:seq:\d+:([^:]+)$/);
    if (seqMatch?.[1]) return seqMatch[1];
    const kindMatch = eventId.match(/^[^:]+:([^:]+):/);
    if (kindMatch?.[1] && kindMatch[1] !== 'seq') return kindMatch[1];
  }
  return null;
}

function chatBlockArtifactLinks(block: ProjectedChatBlock, apiBase: string): TimelineArtifactLink[] {
  const metadata = block.metadata ?? {};
  const sourceKind = chatBlockSourceKind(block);
  const links: TimelineArtifactLink[] = [];
  const seen = new Set<string>();
  const addLink = (label: string, value: unknown) => {
    const artifactId = coerceArtifactRef(value);
    if (!artifactId) return;
    const key = `${label}:${artifactId}`;
    if (seen.has(key)) return;
    seen.add(key);
    links.push({
      key,
      label,
      href: buildArtifactDownloadHref(apiBase, artifactId),
    });
  };

  if (sourceKind === 'summary_published') {
    addLink('Open summary artifact', metadata.summaryRef ?? metadata.artifactRef);
  }
  if (sourceKind === 'checkpoint_published') {
    addLink('Open checkpoint artifact', metadata.checkpointRef ?? metadata.artifactRef);
  }
  if (sourceKind === 'session_cleared' || sourceKind === 'session_reset_boundary') {
    addLink(
      'Open control event artifact',
      metadata.controlEventRef ?? (sourceKind === 'session_cleared' ? metadata.artifactRef : null),
    );
    addLink(
      'Open reset boundary artifact',
      metadata.resetBoundaryRef ?? (sourceKind === 'session_reset_boundary' ? metadata.artifactRef : null),
    );
  }
  if (sourceKind?.startsWith('lifecycle.')) {
    addLink('Open diagnostics', metadata.diagnosticsRef);
  }
  if (metadata.terminalStatus) {
    addLink('Open diagnostics', metadata.diagnosticsRef);
    addLink('Open capture manifest', metadata.captureManifestRef);
    addLink('Open initial snapshot', metadata.initialSnapshotRef);
    addLink('Open final snapshot', metadata.finalSnapshotRef);
    addLink('Open raw events', metadata.rawEventsRef);
    addLink('Open normalized events', metadata.normalizedEventsRef);
    addLink('Open external state', metadata.externalStateRef);
  }

  return links;
}

function renderChatBlock(block: ProjectedChatBlock, wrapLines: boolean, apiBase: string, resources: BridgeResource[] = []): ReactNode {
  const className = [
    'chat-session-block',
    `chat-session-block-${block.kind}`,
    wrapLines ? 'is-wrapped' : 'is-unwrapped',
  ].join(' ');
  const roleLabel = chatBlockLabel(block);
  const kindLabel = chatBlockKindLabel(block);
  const displayKindLabel = kindLabel && kindLabel.toLowerCase() !== roleLabel.toLowerCase() ? kindLabel : null;
  const artifactLinks = chatBlockArtifactLinks(block, apiBase);

  if (block.kind === 'tool') {
    return (
      <div key={block.id} className={className} data-chat-block-type="tool">
        <div className="chat-session-block-heading">
          <span className="chat-session-role-label">{roleLabel}</span>
          <span className={`chat-session-status-chip chat-session-status-${chatBlockStatus(block)}`}>
            {chatBlockStatus(block)}
          </span>
          {block.toolName ? <span className="chat-session-kind-chip">{block.toolName}</span> : null}
          {displayKindLabel && displayKindLabel !== block.toolName ? (
            <span className="chat-session-kind-chip">{displayKindLabel}</span>
          ) : null}
        </div>
        <div
          className="chat-session-block-text"
          data-kind={chatBlockSourceKind(block) ?? undefined}
          data-row-type="tool"
        >
          {block.text || block.toolName || 'Tool call'}
        </div>
        <TimelineArtifactLinks links={artifactLinks} />
        <ContextualBridgeResourceLinks apiBase={apiBase} block={block} resources={resources} />
      </div>
    );
  }

  if (block.kind === 'approval') {
    return (
      <div key={block.id} className={className} data-chat-block-type="approval">
        <div className="chat-session-block-heading">
          <span className="chat-session-role-label">{roleLabel}</span>
          <span className={`chat-session-status-chip chat-session-status-${chatBlockStatus(block)}`}>
            {chatBlockStatus(block)}
          </span>
          {displayKindLabel ? <span className="chat-session-kind-chip">{displayKindLabel}</span> : null}
        </div>
        <div
          className="chat-session-block-text"
          data-kind={chatBlockSourceKind(block) ?? undefined}
          data-row-type="approval"
        >
          {block.text}
        </div>
        <TimelineArtifactLinks links={artifactLinks} />
      </div>
    );
  }

  return (
    <div key={block.id} className={className} data-chat-block-type={block.kind}>
      <div className="chat-session-block-heading">
        <span className="chat-session-role-label">{roleLabel}</span>
        {block.sourceEventIds.length === 0 ? <span className="chat-session-kind-chip">pending</span> : null}
        {displayKindLabel ? <span className="chat-session-kind-chip">{displayKindLabel}</span> : null}
      </div>
      <div
        className="chat-session-block-text"
        data-kind={chatBlockSourceKind(block) ?? undefined}
        data-row-type={chatBlockRowType(block)}
      >
        {block.text}
      </div>
      <TimelineArtifactLinks links={artifactLinks} />
      <ContextualBridgeResourceLinks apiBase={apiBase} block={block} resources={resources} />
    </div>
  );
}

function ChatSessionView({
  apiBase,
  chatBlocks,
  rows,
  wrapLines,
  resources = [],
  liveAnnouncement,
}: {
  apiBase: string;
  chatBlocks: ProjectedChatBlock[];
  rows: TimelineRow[];
  wrapLines: boolean;
  resources?: BridgeResource[];
  liveAnnouncement?: string | undefined;
}) {
  const hasFallbackRows = rows.some((row) => row.rowType === 'fallback' || row.rowType === 'output');
  return (
    <div className="chat-session-view" aria-label="Chat session projection">
      {liveAnnouncement ? <div className="sr-only" aria-live="polite" aria-atomic="true">{liveAnnouncement}</div> : null}
      <div className="chat-session-header">
        <div>
          <h3>Chat Session</h3>
          <p className="small">Messages, tools, approvals, status, and session boundaries.</p>
        </div>
        {hasFallbackRows ? (
          <span className="chat-session-fallback-chip">Raw history fallback active</span>
        ) : null}
      </div>
      {chatBlocks.length === 0 ? (
        <div className="chat-session-empty">
          Structured chat projection is unavailable for these rows. Use Raw Timeline for durable history.
        </div>
      ) : (
        <div
          className="chat-session-blocks"
          data-testid="chat-session-blocks"
        >
          <Virtuoso
            style={{ height: 'min(480px, 62vh)' }}
            data={chatBlocks}
            followOutput={(atBottom) => atBottom ? 'smooth' : false}
            computeItemKey={(index, block) => `${block.id}:${index}`}
            itemContent={(index, block) => renderChatBlock({ ...block, id: `${block.id}:${index}` }, wrapLines, apiBase, resources)}
          />
        </div>
      )}
    </div>
  );
}

type AgentRunRouteTemplates = {
  observabilitySummary?: string | undefined;
  observabilityEvents?: string | undefined;
  logsStream?: string | undefined;
  logsStdout?: string | undefined;
  logsStderr?: string | undefined;
  logsMerged?: string | undefined;
  diagnostics?: string | undefined;
  artifactSession?: string | undefined;
  artifactSessionControl?: string | undefined;
  sessionResources?: string | undefined;
};

function readAgentRunRouteTemplates(config: DashboardConfig | undefined): AgentRunRouteTemplates {
  const sourceRoutes = config?.sources?.agentRuns;
  return {
    observabilitySummary: sourceRoutes?.observabilitySummary,
    observabilityEvents: sourceRoutes?.observabilityEvents,
    logsStream: sourceRoutes?.logsStream,
    logsStdout: sourceRoutes?.logsStdout,
    logsStderr: sourceRoutes?.logsStderr,
    logsMerged: sourceRoutes?.logsMerged,
    diagnostics: sourceRoutes?.diagnostics,
    artifactSession: sourceRoutes?.artifactSession,
    artifactSessionControl: sourceRoutes?.artifactSessionControl,
    sessionResources: sourceRoutes?.sessionResources,
  };
}

function formatStepToolLabel(tool: z.infer<typeof StepLedgerToolSchema>): string {
  const name = String(tool.name || '').trim();
  const type = String(tool.type || '').trim();
  if (name) return name;
  if (type) return type;
  return 'unknown';
}

function formatStepLastError(lastError: unknown): string | null {
  if (!lastError) return null;
  if (typeof lastError === 'string') return lastError;
  if (typeof lastError === 'object') {
    const candidate = (lastError as { summary?: unknown; message?: unknown }).summary
      ?? (lastError as { summary?: unknown; message?: unknown }).message;
    return candidate ? String(candidate) : JSON.stringify(lastError);
  }
  return String(lastError);
}

function stepTerminal(status: string | null | undefined): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return normalized === 'completed'
    || normalized === 'succeeded'
    || normalized === 'failed'
    || normalized === 'canceled'
    || normalized === 'cancelled'
    || normalized === 'skipped';
}

function stepCheckStatusClass(status: string | null | undefined): string {
  const normalized = String(status || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `check-${normalized || 'unknown'}`;
}

function stepCheckStatusPillProps(status: string | null | undefined) {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'pending') {
    return {
      ...executionStatusPillProps('executing'),
      'data-shimmer-label': formatStatusLabel(status),
    };
  }
  return executionStatusPillProps(status);
}

function StepCheckBadge({ check }: { check: z.infer<typeof StepLedgerCheckSchema> }) {
  const checkStatusClass = stepCheckStatusClass(check.status);
  const statusPillProps = stepCheckStatusPillProps(check.status);
  const label = `${check.kind.replaceAll('_', ' ')}: ${formatStatusLabel(check.status)}`;
  const hasShimmerSweep = statusPillProps['data-effect'] === 'shimmer-sweep';
  return (
    <span
      {...statusPillProps}
      aria-label={hasShimmerSweep ? label : undefined}
      className={`step-check-badge ${checkStatusClass} ${statusPillProps.className}`}
    >
      {hasShimmerSweep ? (
        <span className="status-letter-wave" aria-hidden="true" data-label={label}>
          {label}
        </span>
      ) : (
        label
      )}
    </span>
  );
}

function StepCheckDetails({ check }: { check: z.infer<typeof StepLedgerCheckSchema> }) {
  return (
    <div className="step-check-details">
      {typeof check.retryCount === 'number' ? (
        <span className="small">Retry count: {check.retryCount}</span>
      ) : null}
      {check.artifactRef ? (
        <span className="small">
          Review artifact: <code className="text-xs break-all">{check.artifactRef}</code>
        </span>
      ) : (
        <span className="small">No review artifact linked yet.</span>
      )}
    </div>
  );
}

function StepArtifactsList({
  artifacts,
}: {
  artifacts: z.infer<typeof StepLedgerArtifactsSchema>;
}) {
  const entries = [
    ['Output summary', artifacts.outputSummary],
    ['Output primary', artifacts.outputPrimary],
    ['Runtime stdout', artifacts.runtimeStdout],
    ['Runtime stderr', artifacts.runtimeStderr],
    ['Runtime merged logs', artifacts.runtimeMergedLogs],
    ['Runtime diagnostics', artifacts.runtimeDiagnostics],
    ['Provider snapshot', artifacts.providerSnapshot],
  ].filter(([, value]) => Boolean(value)) as Array<[string, string]>;

  if (entries.length === 0) {
    return <p className="small">No step artifacts linked yet.</p>;
  }

  return (
    <ul className="step-detail-list">
      {entries.map(([label, value]) => (
        <li key={`${label}-${value}`}>
          <strong>{label}:</strong> <code className="text-xs break-all">{value}</code>
        </li>
      ))}
    </ul>
  );
}

function StepMetadataList({
  row,
  runId,
}: {
  row: z.infer<typeof StepLedgerRowSchema>;
  runId: string;
}) {
  return (
    <ul className="step-detail-list">
      <li><strong>Logical step id:</strong> <code className="text-xs break-all">{row.logicalStepId}</code></li>
      <li><strong>Run id:</strong> <code className="text-xs break-all">{runId}</code></li>
      <li><strong>Tool:</strong> <code className="text-xs break-all">{formatStepToolLabel(row.tool)}</code></li>
      <li><strong>Execution ordinal:</strong> {row.executionOrdinal}</li>
      {row.dependsOn && row.dependsOn.length > 0 ? (
        <li><strong>Prior step evidence:</strong> {row.dependsOn.join(', ')}</li>
      ) : null}
      <li><strong>Child workflow:</strong> {row.refs.childWorkflowId ? <code className="text-xs break-all">{row.refs.childWorkflowId}</code> : '—'}</li>
      <li><strong>Child run:</strong> {row.refs.childRunId ? <code className="text-xs break-all">{row.refs.childRunId}</code> : '—'}</li>
      <li><strong>Workflow run:</strong> {row.refs.agentRunId ? <code className="text-xs break-all">{row.refs.agentRunId}</code> : '—'}</li>
      <li><strong>Started:</strong> {formatWhen(row.startedAt)}</li>
      <li><strong>Updated:</strong> {formatWhen(row.updatedAt)}</li>
    </ul>
  );
}

type RemediationCadenceInfo = {
  role: 'remediation' | 'verification';
  attempt: number | null;
  maxAttempts: number | null;
};

function positiveInt(value: unknown): number | null {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) return value;
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) {
    const parsed = Number.parseInt(value.trim(), 10);
    return parsed > 0 ? parsed : null;
  }
  return null;
}

function remediationCadenceInfo(row: z.infer<typeof StepLedgerRowSchema>): RemediationCadenceInfo | null {
  const annotations = row.annotations ?? {};
  const role = String(
    annotations.jiraOrchestrateRole ?? annotations.issueImplementRole ?? '',
  ).toLowerCase();
  const rowTitle = row.title ?? '';
  const title = rowTitle.toLowerCase();
  const isRemediation = role === 'moonspec-remediation'
    || title.startsWith('remediate verification gaps')
    || title.startsWith('remediate remaining gaps');
  const isVerification = role === 'moonspec-verification-gate'
    || title.startsWith('verify remediation');
  if (!isRemediation && !isVerification) return null;
  const titleMatch = rowTitle
    ? (rowTitle.match(/\battempt\s+(\d+)\s+of\s+(\d+)\b/i) ?? rowTitle.match(/\b(\d+)\s+of\s+(\d+)\b/i))
    : null;
  return {
    role: isRemediation ? 'remediation' : 'verification',
    attempt: positiveInt(annotations.moonSpecRemediationAttempt) ?? positiveInt(titleMatch?.[1]),
    maxAttempts: positiveInt(annotations.moonSpecRemediationMaxAttempts) ?? positiveInt(titleMatch?.[2]),
  };
}

function RemediationCadenceChip({ row }: { row: z.infer<typeof StepLedgerRowSchema> }) {
  const cadence = remediationCadenceInfo(row);
  if (!cadence) return null;
  const attemptLabel = cadence.attempt && cadence.maxAttempts
    ? `Attempt ${cadence.attempt} of ${cadence.maxAttempts}`
    : 'Attempt scoped';
  return (
    <span
      className="step-execution-pill"
      title={cadence.role === 'verification'
        ? 'Full verification for the remediation attempt.'
        : 'Attempt-scoped remediation; gap details and targeted checks belong inside this attempt.'}
    >
      {cadence.role === 'verification' ? 'Full verification' : 'Remediation'} · {attemptLabel}
    </span>
  );
}

function RemediationCadenceDetails({ row }: { row: z.infer<typeof StepLedgerRowSchema> }) {
  const cadence = remediationCadenceInfo(row);
  if (!cadence) return null;
  const attemptLabel = cadence.attempt && cadence.maxAttempts
    ? `Attempt ${cadence.attempt} of ${cadence.maxAttempts}`
    : 'Attempt scoped';
  return (
    <section className="step-tl-detail-section">
      <h4>Remediation cadence</h4>
      <ul className="step-detail-list">
        <li><strong>Attempt progress:</strong> {attemptLabel}</li>
        <li><strong>Cadence role:</strong> {cadence.role === 'verification' ? 'Full attempt verification' : 'Remediation attempt'}</li>
        <li><strong>Gap progress:</strong> Recorded inside the remediation attempt artifact.</li>
        <li><strong>Targeted checks:</strong> Recorded inside the remediation attempt, not as sibling full-verifier steps.</li>
      </ul>
    </section>
  );
}

function formatOptionalValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function StepWorkloadDetails({
  workload,
}: {
  workload: z.infer<typeof StepLedgerWorkloadSchema> | null | undefined;
}) {
  if (!workload) return null;

  const sessionContext = workload.sessionContext;
  const hasSessionContext = Boolean(sessionContext && Object.keys(sessionContext).length > 0);

  return (
    <section className="step-tl-detail-section">
      <h4>Workload</h4>
      <ul className="step-detail-list">
        <li><strong>Runner profile:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.profileId)}</code></li>
        <li><strong>Image:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.imageRef)}</code></li>
        <li><strong>Status:</strong> {formatStatusLabel(workload.status)}</li>
        <li><strong>Exit code:</strong> {formatOptionalValue(workload.exitCode)}</li>
        <li><strong>Duration:</strong> {formatOptionalValue(workload.durationSeconds)}s</li>
        <li><strong>Tool:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.toolName)}</code></li>
        <li><strong>Step:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.stepId)}</code></li>
        <li><strong>Workflow run:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.agentRunId)}</code></li>
        {workload.timeoutReason ? <li><strong>Timeout reason:</strong> {workload.timeoutReason}</li> : null}
        {workload.cancelReason ? <li><strong>Cancel reason:</strong> {workload.cancelReason}</li> : null}
        {workload.artifactPublication?.status === 'failed' ? (
          <li>
            <strong>Artifact publication:</strong>{' '}
            <span className="text-red-500">{workload.artifactPublication.error || 'failed'}</span>
          </li>
        ) : null}
        {hasSessionContext ? (
          <li>
            <strong>Session association:</strong>{' '}
            <code className="text-xs break-all">{JSON.stringify(sessionContext)}</code>
          </li>
        ) : null}
      </ul>
    </section>
  );
}

function StepObservabilityGroup({
  apiBase,
  logStreamingEnabled,
  sessionTimelineEnabled,
  structuredHistoryEnabled,
  row,
  workflowId,
  routes,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  workflowId: string;
  routes: AgentRunRouteTemplates;
}) {
  const [bridgeOptimisticMessages, setBridgeOptimisticMessages] = useState<OptimisticChatSessionMessage[]>([]);
  const agentRunId = row.refs.agentRunId;
  const explicitBridgeSessionId =
    row.refs.bridgeSessionId ||
    row.refs.bridge_session_id ||
    row.refs.omnigentBridgeSessionId ||
    row.refs.omnigent_bridge_session_id ||
    '';
  const bridgeIdempotencyKey = row.refs.idempotencyKey || row.refs.idempotency_key || '';
  const bridgeWorkflowId = workflowId;
  const bridgeResolutionQuery = useQuery({
    queryKey: [
      'omnigent-bridge-step-projection',
      bridgeWorkflowId,
      row.logicalStepId,
      row.executionOrdinal,
      agentRunId,
      bridgeIdempotencyKey,
    ],
    queryFn: () =>
      resolveBridgeSessionProjection({
        apiBase,
        workflowId: bridgeWorkflowId,
        agentRunId: agentRunId ?? null,
        idempotencyKey: agentRunId ? null : bridgeIdempotencyKey,
      }),
    enabled: Boolean(
        logStreamingEnabled &&
        !explicitBridgeSessionId &&
        bridgeWorkflowId &&
        (agentRunId || bridgeIdempotencyKey),
    ),
    staleTime: stepTerminal(row.status) ? Infinity : 2000,
    retry: false,
  });
  const bridgeSessionId = explicitBridgeSessionId || bridgeResolutionQuery.data?.bridgeSessionId || '';

  if (!logStreamingEnabled) {
    return (
      <p className="small">Live log streaming is disabled in the server dashboard config.</p>
    );
  }

  if (!agentRunId) {
    if (bridgeSessionId) {
      return (
        <BridgeSessionLogsPanel
          apiBase={apiBase}
          bridgeSessionId={bridgeSessionId}
          isTerminal={stepTerminal(row.status)}
          projection={bridgeResolutionQuery.data ?? { bridgeSessionId, capabilities: {} }}
          optimisticMessages={bridgeOptimisticMessages}
          setOptimisticMessages={setBridgeOptimisticMessages}
        />
      );
    }
    if (bridgeResolutionQuery.isLoading) {
      return <p className="small">Checking bridge session evidence.</p>;
    }
    return (
      <p className="small">
        {renderMissingAgentRunCopy(
          row.status === 'executing' || row.status === 'awaiting_external'
            ? 'waiting_for_launch'
            : 'binding_missing',
        )}
      </p>
    );
  }

  return (
    <div className="stack">
      <LiveLogsPanel
        apiBase={apiBase}
        agentRunId={agentRunId}
        isTerminal={stepTerminal(row.status)}
        autoExpand
        disclosure={false}
        routes={routes}
        sessionTimelineEnabled={sessionTimelineEnabled}
        structuredHistoryEnabled={structuredHistoryEnabled}
      />
      <StaticLogPanel
        apiBase={apiBase}
        agentRunId={agentRunId}
        stream="stdout"
        routes={routes}
      />
      <StaticLogPanel
        apiBase={apiBase}
        agentRunId={agentRunId}
        stream="stderr"
        routes={routes}
      />
      <DiagnosticsPanel
        apiBase={apiBase}
        agentRunId={agentRunId}
        routes={routes}
      />
    </div>
  );
}

// MM-815: Collect the latest attempt's evidence refs for the default (collapsed)
// step row. These are ref-only pointers (artifact + gate refs) derived from the
// current row; no transcripts, diffs, or provider payloads are inlined, and no
// prior-attempt history is mixed in.
function collectStepEvidenceRefs(
  row: z.infer<typeof StepLedgerRowSchema>,
): Array<{ label: string; ref: string }> {
  if (!row) return [];
  const manifestRefs = row.artifacts?.stepExecutionManifestRefs ?? [];
  const latestManifestRef =
    row.artifacts?.stepExecutionManifestRef ??
    (manifestRefs.length > 0 ? manifestRefs[manifestRefs.length - 1] : null);
  const entries: Array<[string, string | null | undefined]> = [
    ['Output', row.artifacts?.outputPrimary ?? row.artifacts?.outputSummary],
    ['Diagnostics', row.artifacts?.runtimeDiagnostics],
    ['Provider', row.artifacts?.providerSnapshot],
    ['Manifest', latestManifestRef],
    ['Checkpoint', row.stateCheckpointRef],
  ];
  const refs = entries
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => ({ label, ref: value as string }));
  const checks = row.checks ?? [];
  for (const check of checks) {
    if (check.artifactRef) {
      refs.push({ label: `${check.kind.replaceAll('_', ' ')} verdict`, ref: check.artifactRef });
    }
  }
  return refs;
}

function StepEvidenceRefs({ row }: { row: z.infer<typeof StepLedgerRowSchema> }) {
  const refs = collectStepEvidenceRefs(row);
  if (refs.length === 0) return null;
  return (
    <div className="step-evidence-refs" aria-label="Latest evidence refs">
      <span className="step-evidence-label">Evidence</span>
      {refs.map(({ label, ref }, index) => (
        <span className="step-evidence-ref" key={`${label}-${ref}-${index}`} title={ref}>
          {label}: <code className="text-xs break-all">{ref}</code>
        </span>
      ))}
    </div>
  );
}

function StepProvenanceMarker({ row }: { row: z.infer<typeof StepLedgerRowSchema> }) {
  if (!row.preservedFrom) return null;
  const source = row.preservedFrom;
  return (
    <span
      className="step-provenance-marker"
      title={`Preserved from source run ${source.workflowId} run ${source.runId} execution ${source.executionOrdinal}.`}
    >
      Preserved
    </span>
  );
}

const ACTIVE_STEP_TIMING_STATUSES = new Set(['executing', 'reviewing', 'awaiting_external']);

type StepTiming = z.infer<typeof StepTimingSchema> | null | undefined;
type StepLedgerRow = z.infer<typeof StepLedgerRowSchema>;
type CheckpointBranch = z.infer<typeof CheckpointBranchModelSchema>;
type CheckpointBranchTurn = z.infer<typeof CheckpointBranchTurnModelSchema>;
type StepExecutionProjection = z.infer<typeof StepExecutionProjectionSchema>;

type BranchCreateDraft = {
  sourceStepId: string;
  label: string;
  instructions: string;
  workspacePolicy: string;
  runtimeContextPolicy: string;
  publishMode: string;
  gitWorkBranch: string;
  maxBudgetUsd: string;
};

type BranchMutationKind = 'create' | 'continue' | 'fork' | 'promote' | 'publish' | 'archive' | 'compare';

type BranchMutationRequest =
  | { kind: 'create'; draft: BranchCreateDraft; source: StepLedgerRow; idempotencyKey: string }
  | { kind: 'continue'; branch: CheckpointBranch; instructions: string; idempotencyKey: string }
  | { kind: 'fork'; branch: CheckpointBranch; instructions: string; idempotencyKey: string }
  | { kind: 'promote'; branch: CheckpointBranch; competingBranches: CheckpointBranch[]; idempotencyKey: string }
  | { kind: 'publish'; branch: CheckpointBranch; idempotencyKey: string }
  | { kind: 'archive'; branch: CheckpointBranch; idempotencyKey: string }
  | { kind: 'compare'; branch: CheckpointBranch; againstBranchId: string };

const BRANCH_MUTATING_STATES = new Set(['created', 'active', 'blocked', 'failed', 'succeeded', 'promotable']);
const DEFAULT_BRANCH_CREATE_DRAFT: BranchCreateDraft = {
  sourceStepId: '',
  label: 'Checkpoint branch',
  instructions: 'Continue from this checkpoint with a bounded alternative implementation.',
  workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
  runtimeContextPolicy: 'fresh_agent_run',
  publishMode: 'none',
  gitWorkBranch: '',
  maxBudgetUsd: '',
};

function stepCheckpointRef(row: StepLedgerRow): string | null {
  return row.stateCheckpointRef || null;
}

function isSupportedCheckpointRef(ref: string | null): boolean {
  return Boolean(ref?.startsWith('artifact://'));
}

function stepBranchKey(row: StepLedgerRow): string {
  return `${row.logicalStepId}:${row.executionOrdinal}`;
}

function branchStepKey(branch: CheckpointBranch): string {
  return `${branch.logicalStepId || ''}:${branch.sourceExecutionOrdinal ?? 0}`;
}

function buildBranchGroups(branches: CheckpointBranch[]): Map<string, CheckpointBranch[]> {
  const groups = new Map<string, CheckpointBranch[]>();
  for (const branch of branches) {
    const key = branchStepKey(branch);
    groups.set(key, [...(groups.get(key) ?? []), branch]);
  }
  return groups;
}

function branchArtifactEntries(branch: CheckpointBranch): Array<{ label: string; ref: string }> {
  const entries: Array<{ label: string; ref: string }> = [];
  for (const [key, value] of Object.entries(branch.artifactRefs || {})) {
    const ref = coerceArtifactRef(value);
    if (ref) entries.push({ label: formatStatusLabel(key), ref });
  }
  if (branch.currentHeadCheckpointRef) {
    entries.push({ label: 'Head checkpoint', ref: branch.currentHeadCheckpointRef });
  }
  return entries;
}

function artifactRefHref(apiBase: string, ref: string): string | null {
  const artifactId = coerceArtifactRef(ref);
  if (!artifactId) return null;
  if (/^https?:\/\//i.test(artifactId)) return artifactId;
  if (artifactId.startsWith('artifact://')) return null;
  return buildArtifactDownloadHref(apiBase, artifactId);
}

function branchEvidenceLinks(apiBase: string, branch: CheckpointBranch): ReactNode {
  const entries = branchArtifactEntries(branch);
  if (entries.length === 0 && !branch.pullRequestUrl) {
    return <span className="small">No evidence refs linked yet.</span>;
  }
  return (
    <div className="live-logs-artifact-links">
      {entries.map((entry) => {
        const href = artifactRefHref(apiBase, entry.ref);
        return href ? (
          <a
            key={`${entry.label}-${entry.ref}`}
            className="live-logs-artifact-link"
            href={href}
            target="_blank"
            rel="noreferrer"
          >
            {entry.label}
          </a>
        ) : (
          <code key={`${entry.label}-${entry.ref}`} className="text-xs break-all">{entry.ref}</code>
        );
      })}
      {branch.pullRequestUrl ? (
        <a className="live-logs-artifact-link" href={branch.pullRequestUrl} target="_blank" rel="noreferrer">
          Pull request
        </a>
      ) : null}
    </div>
  );
}

function BranchStatusAffordance({ branches }: { branches: CheckpointBranch[] }) {
  if (branches.length === 0) return null;
  const states = Array.from(new Set(branches.map((branch) => branch.state || 'unknown')));
  const stateLabels = states.map((state) => formatStatusLabel(state));
  return (
    <span className="step-execution-pill" title={stateLabels.join(', ')}>
      {branches.length} branch{branches.length === 1 ? '' : 'es'} · {stateLabels.join(', ')}
    </span>
  );
}

function safeBranchSlug(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  return slug || 'checkpoint-branch';
}

function branchIdempotencyKey(kind: BranchMutationKind, workflowId: string, subject: string): string {
  return `dashboard:${kind}:${workflowId}:${subject}:${Date.now()}`;
}

function branchCanMutate(branch: CheckpointBranch): boolean {
  return BRANCH_MUTATING_STATES.has(branch.state);
}

function firstBranchBlockedReason(reasons: Array<string | null | false | undefined>): string | null {
  return reasons.find((reason): reason is string => Boolean(reason)) ?? null;
}

function branchCreateBlockedReason({
  actionsEnabled,
  busy,
  selectedSource,
  draft,
  publishModeRequiresBranch,
}: {
  actionsEnabled: boolean;
  busy: boolean;
  selectedSource: StepLedgerRow | null;
  draft: BranchCreateDraft;
  publishModeRequiresBranch: boolean;
}): string | null {
  return firstBranchBlockedReason([
    !actionsEnabled && 'Branch actions are disabled by workflow policy.',
    busy && 'Another workflow action is in progress.',
    !selectedSource && 'Select a checkpoint with an artifact-backed checkpoint ref.',
    !draft.label.trim() && 'Enter a branch label.',
    !draft.instructions.trim() && 'Enter branch instructions.',
    publishModeRequiresBranch && !draft.gitWorkBranch.trim() && 'Enter a git work branch for the selected publish mode.',
  ]);
}

function branchActionBlockedReason({
  actionsEnabled,
  busy,
  selectedBranch,
  selectedMutable,
  action,
  againstBranchId,
}: {
  actionsEnabled: boolean;
  busy: boolean;
  selectedBranch: CheckpointBranch | null;
  selectedMutable: boolean;
  action: 'continue' | 'fork' | 'compare' | 'promote' | 'publish' | 'archive';
  againstBranchId?: string;
}): string | null {
  const baseReason = firstBranchBlockedReason([
    !actionsEnabled && 'Branch actions are disabled by workflow policy.',
    busy && 'Another workflow action is in progress.',
    !selectedBranch && 'Select a checkpoint branch.',
  ]);
  if (baseReason) return baseReason;
  const branch = selectedBranch!;
  if ((action === 'continue' || action === 'fork' || action === 'archive') && !selectedMutable) {
    return `Branch state ${formatStatusLabel(branch.state)} cannot be changed.`;
  }
  if (action === 'compare' && (!againstBranchId || againstBranchId === branch.branchId)) {
    return 'Select a different branch to compare against.';
  }
  if (action === 'promote' && !branch.currentHeadStepExecutionId) {
    return 'Promotion requires a current head step execution.';
  }
  if (
    action === 'publish' &&
    (!branch.gitRepository || !branch.gitBaseBranch || !branch.gitWorkBranch)
  ) {
    return 'Publishing requires repository, base branch, and git work branch refs.';
  }
  return null;
}

function branchPromotionGateVerdict(branch: CheckpointBranch): string {
  return branch.state === 'promotable' || branch.state === 'succeeded' ? 'passed' : branch.state;
}

function branchPromotionSideEffectStatus(branch: CheckpointBranch): string {
  const publishStatus = branch.publishStatus || 'unpublished';
  return publishStatus === 'unpublished' ? 'none' : 'accepted';
}

function BranchExplorerPanel({
  apiBase,
  workflowId,
  runId,
  rows,
  branches,
  selectedBranch,
  turns,
  isLoading,
  error,
  turnsError,
  actionsEnabled,
  busy,
  latestCompare,
  onSelectBranch,
  onBranchAction,
}: {
  apiBase: string;
  workflowId: string;
  runId: string;
  rows: StepLedgerRow[];
  branches: CheckpointBranch[];
  selectedBranch: CheckpointBranch | null;
  turns: CheckpointBranchTurn[];
  isLoading: boolean;
  error: Error | null;
  turnsError: Error | null;
  actionsEnabled: boolean;
  busy: boolean;
  latestCompare: z.infer<typeof CheckpointBranchCompareSchema> | null;
  onSelectBranch: (branchId: string) => void;
  onBranchAction: (request: BranchMutationRequest) => void;
}) {
  const checkpointRows = rows.filter((row) => isSupportedCheckpointRef(stepCheckpointRef(row)));
  const branchGroups = useMemo(() => buildBranchGroups(branches), [branches]);
  const [draft, setDraft] = useState<BranchCreateDraft>(() => DEFAULT_BRANCH_CREATE_DRAFT);
  const [branchInstructions, setBranchInstructions] = useState('Continue this branch with bounded instructions.');
  const [againstBranchId, setAgainstBranchId] = useState('');

  useEffect(() => {
    const firstCheckpointRow = checkpointRows[0];
    const selectedCheckpointExists = checkpointRows.some((row) => stepBranchKey(row) === draft.sourceStepId);
    if ((!draft.sourceStepId || !selectedCheckpointExists) && firstCheckpointRow) {
      setDraft((current) => ({ ...current, sourceStepId: stepBranchKey(firstCheckpointRow) }));
    }
  }, [checkpointRows, draft.sourceStepId]);

  useEffect(() => {
    if (!selectedBranch) {
      if (againstBranchId) setAgainstBranchId('');
      return;
    }
    const targetExists = branches.some((branch) => branch.branchId === againstBranchId);
    if (againstBranchId === selectedBranch.branchId || !againstBranchId || !targetExists) {
      const next = branches.find((branch) => branch.branchId !== selectedBranch.branchId);
      setAgainstBranchId(next?.branchId || '');
    }
  }, [againstBranchId, branches, selectedBranch]);

  const selectedSource = checkpointRows.find((row) => stepBranchKey(row) === draft.sourceStepId) || checkpointRows[0] || null;
  const competingBranches = selectedBranch
    ? branches.filter((branch) => (
      branch.branchId !== selectedBranch.branchId &&
      branch.sourceCheckpointRef === selectedBranch.sourceCheckpointRef &&
      branch.state !== 'archived'
    ))
    : [];
  const publishModeRequiresBranch = draft.publishMode !== 'none';
  const selectedMutable = Boolean(selectedBranch && branchCanMutate(selectedBranch));
  const createBlockedReason = branchCreateBlockedReason({
    actionsEnabled,
    busy,
    selectedSource,
    draft,
    publishModeRequiresBranch,
  });
  const continueBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'continue',
  });
  const forkBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'fork',
  });
  const compareBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'compare',
    againstBranchId,
  });
  const promoteBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'promote',
  });
  const publishBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'publish',
  });
  const archiveBlockedReason = branchActionBlockedReason({
    actionsEnabled,
    busy,
    selectedBranch,
    selectedMutable,
    action: 'archive',
  });
  const createDisabled = Boolean(createBlockedReason);
  const promoteDisabled = Boolean(promoteBlockedReason);
  const publishDisabled = Boolean(publishBlockedReason);
  const compareDisabled = Boolean(compareBlockedReason);
  const selectedActionBlockedReason = firstBranchBlockedReason([
    !actionsEnabled && 'Branch actions are disabled by workflow policy.',
    busy && 'Another workflow action is in progress.',
  ]);

  return (
    <section className="stack td-branch-explorer td-evidence-region" aria-label="Branch Explorer">
      <div className="step-tl-section-header">
        <div>
          <h3>Branch Explorer</h3>
          <p className="small">Mainline remains selected by default; checkpoint branches are listed from persisted branch records.</p>
        </div>
        <span className="step-tl-header-meta">
          <span className="step-tl-count">{branches.length} branch{branches.length === 1 ? '' : 'es'}</span>
        </span>
      </div>
      {isLoading ? (
        <LoadingPlaceholder surface="workflow-detail" region="checkpoint-branches" variant="list" density="compact" preserveContext />
      ) : error ? (
        <div className="notice error">{error.message}</div>
      ) : branches.length === 0 ? (
        <p className="notice subtle">No checkpoint branches recorded for this workflow yet.</p>
      ) : (
        <div className="stack">
          {checkpointRows.map((row) => {
            const rowBranches = branchGroups.get(stepBranchKey(row)) ?? [];
            if (rowBranches.length === 0) return null;
            return (
              <div key={stepBranchKey(row)} className="card">
                <div className="stack gap-1">
                  <strong>{row.title}</strong>
                  <p className="small">
                    Checkpoint <code className="text-xs break-all">{stepCheckpointRef(row)}</code>
                  </p>
                  <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                    {rowBranches.map((branch) => {
                      const childCount = branches.filter((candidate) => candidate.parentBranchId === branch.branchId).length;
                      return (
                        <li key={branch.branchId} className="stack gap-1">
                          <button
                            type="button"
                            className="secondary"
                            aria-pressed={selectedBranch?.branchId === branch.branchId}
                            onClick={() => onSelectBranch(branch.branchId)}
                          >
                            {branch.label}
                          </button>
                          <div className="td-facts-grid">
                            <Fact label="State"><ExecutionStatusPill status={branch.state || 'unknown'} /></Fact>
                            <Fact label="Branch ID"><code className="text-xs break-all">{branch.branchId}</code></Fact>
                            <Fact label="Kind">{formatStatusLabel(branch.branchKind || 'root')}</Fact>
                            <Fact label="Children">{childCount}</Fact>
                            {branch.parentBranchId ? <Fact label="Parent"><code className="text-xs break-all">{branch.parentBranchId}</code></Fact> : null}
                            {branch.gitWorkBranch ? <Fact label="Git Branch"><code className="text-xs break-all">{branch.gitWorkBranch}</code></Fact> : null}
                          </div>
                          {branchEvidenceLinks(apiBase, branch)}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="stack td-branch-preview">
        <h4>Create branch preview</h4>
        <div className="grid-2">
          <label>
            Source checkpoint
            <select value={draft.sourceStepId} disabled={checkpointRows.length === 0 || busy} onChange={(event) => setDraft((current) => ({ ...current, sourceStepId: event.target.value }))}>
              {checkpointRows.map((row) => (
                <option key={stepBranchKey(row)} value={stepBranchKey(row)}>
                  {row.title} · execution {row.executionOrdinal}
                </option>
              ))}
            </select>
          </label>
          <label>
            Branch label
            <input value={draft.label} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))} />
          </label>
          <label>
            Workspace policy
            <select value={draft.workspacePolicy} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, workspacePolicy: event.target.value }))}>
              <option value="apply_previous_execution_diff_to_clean_baseline">Apply previous diff to clean baseline</option>
              <option value="restore_pre_execution">Restore pre-execution checkpoint</option>
              <option value="start_from_last_passed_commit">Start from last passed commit</option>
              <option value="fresh_branch_from_source">Fresh branch from source</option>
            </select>
          </label>
          <label>
            Runtime/session policy
            <select value={draft.runtimeContextPolicy} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, runtimeContextPolicy: event.target.value }))}>
              <option value="fresh_agent_run">Fresh agent run</option>
              <option value="reuse_session_new_epoch">Reuse session new epoch</option>
              <option value="reuse_session_same_epoch">Reuse session same epoch</option>
            </select>
          </label>
          <label>
            Publish mode
            <select value={draft.publishMode} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, publishMode: event.target.value }))}>
              <option value="none">None</option>
              <option value="branch">Git branch</option>
              <option value="pull_request">Pull request</option>
            </select>
          </label>
          <label>
            Git work branch
            <input value={draft.gitWorkBranch} disabled={busy} placeholder={`mm/${safeBranchSlug(workflowId)}/${safeBranchSlug(draft.label)}`} onChange={(event) => setDraft((current) => ({ ...current, gitWorkBranch: event.target.value }))} />
          </label>
          <label>
            Budget impact
            <input value={draft.maxBudgetUsd} disabled={busy} inputMode="decimal" placeholder="No explicit cap" onChange={(event) => setDraft((current) => ({ ...current, maxBudgetUsd: event.target.value }))} />
          </label>
          <Card label="Approval requirements">Policy evaluated by checkpoint branch API</Card>
        </div>
        <label>
          Branch instructions
          <textarea value={draft.instructions} disabled={busy} rows={3} onChange={(event) => setDraft((current) => ({ ...current, instructions: event.target.value }))} />
        </label>
        <div className="td-facts-grid">
          <Fact label="Checkpoint"><code className="text-xs break-all">{selectedSource ? stepCheckpointRef(selectedSource) : '—'}</code></Fact>
          <Fact label="Side-effect risk">{draft.publishMode === 'none' ? 'No publish side effect requested' : 'Git publication requested'}</Fact>
          <Fact label="Run"><code className="text-xs break-all">{runId || '—'}</code></Fact>
        </div>
        <button
          type="button"
          disabled={createDisabled}
          title={createBlockedReason || undefined}
          onClick={() => selectedSource && onBranchAction({
            kind: 'create',
            draft,
            source: selectedSource,
            idempotencyKey: branchIdempotencyKey('create', workflowId, stepBranchKey(selectedSource)),
          })}
        >
          Create branch from checkpoint
        </button>
        {createBlockedReason ? <p className="notice subtle">Create branch unavailable: {createBlockedReason}</p> : null}
      </div>

      {selectedBranch ? (
        <div className="stack td-branch-preview">
          <h4>Promotion preview</h4>
          <MetricStrip
            items={[
              { label: 'State', value: formatStatusLabel(selectedBranch.state) },
              { label: 'Turns', value: String(turns.length) },
              { label: 'Publish', value: formatStatusLabel(selectedBranch.publishStatus || 'unpublished') },
              { label: 'Competing Branches', value: String(competingBranches.length) },
            ]}
          />
          <div className="td-facts-grid">
            <Fact label="Branch head"><code className="text-xs break-all">{selectedBranch.currentHeadStepExecutionId || selectedBranch.currentHeadCommit || selectedBranch.currentHeadCheckpointRef || '—'}</code></Fact>
            <Fact label="Gate verdict">{formatStatusLabel(selectedBranch.state)}</Fact>
            <Fact label="Git / PR">{selectedBranch.pullRequestUrl ? <a href={selectedBranch.pullRequestUrl} target="_blank" rel="noreferrer">Pull request</a> : <code className="text-xs break-all">{selectedBranch.currentHeadCommit || selectedBranch.gitWorkBranch || '—'}</code>}</Fact>
            <Fact label="Invalidations">Recorded during promotion</Fact>
            <Fact label="Side effects">{selectedBranch.publishStatus ? formatStatusLabel(selectedBranch.publishStatus) : 'Unpublished'}</Fact>
            <Fact label="Approvals">Policy evidence required by promote API</Fact>
          </div>
          {turnsError ? <p className="small step-tl-error">{turnsError.message}</p> : null}
          {turns.length > 0 ? (
            <ol className="step-execution-history" aria-label="Branch turns">
              {turns.map((turn, index) => (
                <li key={turn.branchTurnId}>
                  <strong>Turn {index + 1}</strong> <ExecutionStatusPill status={turn.status || 'unknown'} />
                  <div className="td-facts-grid">
                    <Fact label="Turn ID"><code className="text-xs break-all">{turn.branchTurnId}</code></Fact>
                    <Fact label="Instructions"><code className="text-xs break-all">{turn.instructionRef}</code></Fact>
                    {turn.stepExecutionManifestRef ? <Fact label="Manifest"><code className="text-xs break-all">{turn.stepExecutionManifestRef}</code></Fact> : null}
                    {turn.createdStepExecutionId ? <Fact label="Step Execution"><code className="text-xs break-all">{turn.createdStepExecutionId}</code></Fact> : null}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="small">No branch turns recorded for this branch yet.</p>
          )}
          <label>
            Branch action instructions
            <textarea value={branchInstructions} disabled={busy} rows={2} onChange={(event) => setBranchInstructions(event.target.value)} />
          </label>
          <label>
            Compare against
            <select value={againstBranchId} disabled={busy || branches.length < 2} onChange={(event) => setAgainstBranchId(event.target.value)}>
              <option value="">Select branch</option>
              {branches.filter((branch) => branch.branchId !== selectedBranch.branchId).map((branch) => (
                <option key={branch.branchId} value={branch.branchId}>{branch.label}</option>
              ))}
            </select>
          </label>
          <div className="button-row">
            <button type="button" disabled={Boolean(continueBlockedReason)} title={continueBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'continue', branch: selectedBranch, instructions: branchInstructions, idempotencyKey: branchIdempotencyKey('continue', workflowId, selectedBranch.branchId) })}>Continue branch</button>
            <button type="button" className="secondary" disabled={Boolean(forkBlockedReason)} title={forkBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'fork', branch: selectedBranch, instructions: branchInstructions, idempotencyKey: branchIdempotencyKey('fork', workflowId, selectedBranch.branchId) })}>Fork from this branch</button>
            <button type="button" className="secondary" disabled={compareDisabled} title={compareBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'compare', branch: selectedBranch, againstBranchId })}>Compare branches</button>
            <button type="button" className="secondary" disabled={promoteDisabled} title={promoteBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'promote', branch: selectedBranch, competingBranches, idempotencyKey: branchIdempotencyKey('promote', workflowId, selectedBranch.branchId) })}>Promote branch</button>
            <button type="button" className="secondary" disabled={publishDisabled} title={publishBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'publish', branch: selectedBranch, idempotencyKey: branchIdempotencyKey('publish', workflowId, selectedBranch.branchId) })}>Publish branch</button>
            <button type="button" className="secondary" disabled={Boolean(archiveBlockedReason)} title={archiveBlockedReason || undefined} onClick={() => onBranchAction({ kind: 'archive', branch: selectedBranch, idempotencyKey: branchIdempotencyKey('archive', workflowId, selectedBranch.branchId) })}>Archive branch</button>
          </div>
          {selectedActionBlockedReason ? (
            <p className="notice subtle">
              Branch action unavailable: {selectedActionBlockedReason}
            </p>
          ) : null}
          {latestCompare ? <p className="small">Latest comparison summary: <code className="text-xs break-all">{latestCompare.summaryRef}</code></p> : null}
        </div>
      ) : null}
    </section>
  );
}

function stepTimingMs(timing: StepTiming): number | null {
  const value = timing?.elapsedMs ?? timing?.durationMs ?? null;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function fallbackTimingMs(startedAt: string | null | undefined, endedAt: string | null | undefined): number | null {
  if (!startedAt || !endedAt) return null;
  const started = Date.parse(startedAt);
  const ended = Date.parse(endedAt);
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return null;
  return Math.max(0, ended - started);
}

function rowTimingValueMs(row: StepLedgerRow): number | null {
  return stepTimingMs(row.timing) ?? fallbackTimingMs(row.startedAt, row.updatedAt);
}

function stepTimingLabel(row: StepLedgerRow): string {
  const status = row.status;
  const timing = row.timing;
  const valueMs = rowTimingValueMs(row);
  if (row.preservedFrom || timing?.preserved) {
    return valueMs === null ? 'Original timing unavailable' : `Original duration: ${formatDurationMs(valueMs)}`;
  }
  if (status === 'pending') return 'Not started';
  if (status === 'ready') return 'Ready';
  if (valueMs === null || timing?.precision === 'unavailable') return 'Timing unavailable';
  if (ACTIVE_STEP_TIMING_STATUSES.has(status)) return `${formatDurationMs(valueMs)} so far`;
  return formatDurationMs(valueMs);
}

function executionTimingLabel(execution: StepExecutionProjection): string {
  const valueMs = stepTimingMs(execution.timing) ?? fallbackTimingMs(execution.startedAt, execution.updatedAt);
  if (valueMs === null || execution.timing?.precision === 'unavailable') return 'Timing unavailable';
  const suffix = ACTIVE_STEP_TIMING_STATUSES.has(execution.status) ? ' so far' : '';
  return `${formatDurationMs(valueMs)}${suffix}`;
}

function visibleStepTimingOverview(rows: StepLedgerRow[]) {
  const completed = rows.filter((row) => row.status === 'completed').length;
  const active = rows.find((row) => ACTIVE_STEP_TIMING_STATUSES.has(row.status));
  const timedRows = rows
    .map((row) => ({ row, valueMs: rowTimingValueMs(row) }))
    .filter((item): item is { row: StepLedgerRow; valueMs: number } => item.valueMs !== null);
  const longest = timedRows.reduce<{ row: StepLedgerRow; valueMs: number } | null>(
    (current, item) => (!current || item.valueMs > current.valueMs ? item : current),
    null,
  );
  return {
    completed,
    total: rows.length,
    active,
    longest,
  };
}

function StepTimingChip({ row }: { row: StepLedgerRow }) {
  return <span className="step-timing-chip">{stepTimingLabel(row)}</span>;
}

function StepTimingDetails({ row }: { row: StepLedgerRow }) {
  const timing = row.timing;
  const valueMs = rowTimingValueMs(row);
  const elapsed = row.preservedFrom || timing?.preserved
    ? stepTimingLabel(row)
    : valueMs === null
      ? 'Timing unavailable'
      : ACTIVE_STEP_TIMING_STATUSES.has(row.status)
        ? `${formatDurationMs(valueMs)} so far`
        : formatDurationMs(valueMs);
  return (
    <section className="step-tl-detail-section">
      <h4>Timing</h4>
      <ul className="step-detail-list">
        <li><strong>Started:</strong> {formatWhen(timing?.startedAt ?? row.startedAt)}</li>
        {timing?.endedAt ? <li><strong>Ended:</strong> {formatWhen(timing.endedAt)}</li> : null}
        <li><strong>Elapsed:</strong> {elapsed}</li>
        <li><strong>Last update:</strong> {formatWhen(row.updatedAt)}</li>
      </ul>
    </section>
  );
}

function StepTimingOverview({ rows }: { rows: StepLedgerRow[] }) {
  const overview = visibleStepTimingOverview(rows);
  return (
    <div className="step-timing-overview" aria-label="Step timing overview">
      <span>
        Current step{' '}
        <strong>
          {overview.active ? `${overview.active.title} · ${stepTimingLabel(overview.active)}` : 'None'}
        </strong>
      </span>
      <span>
        Longest step{' '}
        <strong>
          {overview.longest
            ? `${overview.longest.row.title} · ${formatDurationMs(overview.longest.valueMs)}`
            : 'Timing unavailable'}
        </strong>
      </span>
      <span>
        Completed steps <strong>{overview.completed} of {overview.total}</strong>
      </span>
    </div>
  );
}

// MM-831: flatten a ref map into renderable [label, ref] entries. Only string
// values are surfaced (these are artifact refs, never raw bodies).
function stepRefEntries(
  refs: Record<string, unknown> | null | undefined,
): Array<[string, string]> {
  if (!refs) return [];
  const entries: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(refs)) {
    if (typeof value === 'string' && value) {
      entries.push([key, value]);
    }
  }
  return entries;
}

function StepExecutionRefList({ entries }: { entries: Array<[string, string]> }) {
  if (entries.length === 0) return null;
  return (
    <span className="step-execution-ref-list">
      {entries.map(([label, ref], index) => (
        <span className="step-execution-ref" key={`${label}-${ref}-${index}`}>
          <span className="small">{formatStatusLabel(label)}:</span>{' '}
          <code className="text-xs break-all">{ref}</code>
        </span>
      ))}
    </span>
  );
}

// MM-831: one compact Step Execution row inside the expanded history. Surfaces
// the full enumerated evidence field set (lineage, reason, source attempt,
// runtime child refs, context bundle ref, workspace policy, git disposition,
// gate verdict, output/diagnostic/diff refs, side effects, terminal
// disposition, downstream invalidation) as refs and typed diagnostics only.
function StepExecutionHistoryRow({
  execution,
}: {
  execution: StepExecutionProjection;
}) {
  const contextBundleRef = execution.stepEvidence?.contextBundleRef?.artifactRef ?? null;
  const gateVerdict =
    execution.qualityGateVerdict ?? execution.stepEvidence?.gateSummary?.verdict ?? null;
  const diagnosticRefs = (execution.stepEvidence?.diagnosticRefs ?? []).filter(
    (item) => item.diagnosticsRef,
  );
  const sideEffect = execution.stepEvidence?.sideEffectSummary ?? null;
  const sideEffectRefs = stepRefEntries(sideEffect?.artifactRefs);
  const outputRefs = stepRefEntries(execution.outputRefs);
  const runtimeChildRefs = stepRefEntries(execution.runtimeChildRefs);
  const downstreamInvalidated = execution.reason === 'dependency_invalidated';
  const lineage = execution.lineage ?? null;

  return (
    <li className="step-execution-history-item">
      <div className="step-execution-history-head">
        <span className="step-execution-pill">Execution {execution.executionOrdinal}</span>
        <StepExecutionStatusPill status={execution.status} />
        <span className="step-timing-chip">{executionTimingLabel(execution)}</span>
        <span className="step-execution-reason">{formatStatusLabel(execution.reason)}</span>
        {downstreamInvalidated ? (
          <span
            className="step-invalidation-marker"
            title="This step re-ran because a changed upstream output invalidated it."
          >
            Downstream invalidation
          </span>
        ) : null}
      </div>
      <dl className="step-execution-history-facts">
        {execution.sourceExecutionOrdinal ? (
          <div className="step-execution-fact">
            <dt>Source attempt</dt>
            <dd>Execution {execution.sourceExecutionOrdinal}</dd>
          </div>
        ) : null}
        {lineage ? (
          <div className="step-execution-fact">
            <dt>Lineage</dt>
            <dd>
              <code className="text-xs break-all">{lineage.sourceWorkflowId}</code> run{' '}
              <code className="text-xs break-all">{lineage.sourceRunId}</code>{' '}
              {lineage.sourceLogicalStepId} execution {lineage.sourceExecutionOrdinal}
              {lineage.relationship ? ` (${formatStatusLabel(lineage.relationship)})` : ''}
            </dd>
          </div>
        ) : null}
        {execution.terminalDisposition ? (
          <div className="step-execution-fact">
            <dt>Terminal disposition</dt>
            <dd>{formatStatusLabel(execution.terminalDisposition)}</dd>
          </div>
        ) : null}
        {execution.workspacePolicy ? (
          <div className="step-execution-fact">
            <dt>Workspace policy</dt>
            <dd>{formatStatusLabel(execution.workspacePolicy)}</dd>
          </div>
        ) : null}
        {execution.gitDisposition ? (
          <div className="step-execution-fact">
            <dt>Git disposition</dt>
            <dd>{formatStatusLabel(execution.gitDisposition)}</dd>
          </div>
        ) : null}
        {gateVerdict ? (
          <div className="step-execution-fact">
            <dt>Gate verdict</dt>
            <dd>{formatStatusLabel(gateVerdict)}</dd>
          </div>
        ) : null}
        {contextBundleRef ? (
          <div className="step-execution-fact">
            <dt>Context bundle</dt>
            <dd>
              <code className="text-xs break-all">{contextBundleRef}</code>
            </dd>
          </div>
        ) : null}
        {runtimeChildRefs.length > 0 ? (
          <div className="step-execution-fact">
            <dt>Runtime child refs</dt>
            <dd>
              <StepExecutionRefList entries={runtimeChildRefs} />
            </dd>
          </div>
        ) : null}
        {outputRefs.length > 0 ? (
          <div className="step-execution-fact">
            <dt>Output &amp; diff refs</dt>
            <dd>
              <StepExecutionRefList entries={outputRefs} />
            </dd>
          </div>
        ) : null}
        {diagnosticRefs.length > 0 ? (
          <div className="step-execution-fact">
            <dt>Diagnostics refs</dt>
            <dd>
              <StepExecutionRefList
                entries={diagnosticRefs.map((item) => [item.kind, item.diagnosticsRef as string])}
              />
            </dd>
          </div>
        ) : null}
        {sideEffect ? (
          <div className="step-execution-fact">
            <dt>Side effects</dt>
            <dd>
              {formatStatusLabel(sideEffect.status || 'skipped')}
              {sideEffectRefs.length > 0 ? (
                <>
                  {' '}
                  <StepExecutionRefList entries={sideEffectRefs} />
                </>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>
      {execution.summary ? <p className="small">{execution.summary}</p> : null}
    </li>
  );
}

// MM-831: expanded Step Execution history. Consumes the step-executions LIST
// endpoint (keyed by execution_ordinal) and renders newest-first compact rows.
function StepExecutionHistory({
  apiBase,
  workflowId,
  logicalStepId,
  sourceTemporal,
  enabled,
  pollInterval,
  staleTime,
}: {
  apiBase: string;
  workflowId: string;
  logicalStepId: string;
  sourceTemporal: boolean;
  enabled: boolean;
  pollInterval: number | false;
  staleTime: number;
}) {
  const historyQuery = useQuery({
    queryKey: ['workflow-detail-step-executions', workflowId, logicalStepId, sourceTemporal],
    queryFn: async () => {
      const suffix = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(
        `${apiBase}/executions/${encodeURIComponent(workflowId)}/steps/${encodeURIComponent(
          logicalStepId,
        )}/step-executions${suffix}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        const statusText = response.statusText.trim();
        const detail = statusText ? ` ${statusText}` : '';
        throw new Error(`Step executions: ${response.status}${detail}`);
      }
      return StepExecutionListSchema.parse(await response.json());
    },
    enabled: enabled && Boolean(workflowId) && Boolean(logicalStepId),
    refetchInterval: enabled ? pollInterval : false,
    staleTime,
  });

  if (!enabled) return null;

  const executions = historyQuery.data?.stepExecutions ?? [];
  const ordered = [...executions].sort((a, b) => b.executionOrdinal - a.executionOrdinal);
  const totalMs = executions
    .map((execution) => stepTimingMs(execution.timing) ?? fallbackTimingMs(execution.startedAt, execution.updatedAt))
    .filter((value): value is number => value !== null)
    .reduce((sum, value) => sum + value, 0);

  return (
    <section className="step-tl-detail-section">
      <h4>Step Execution history</h4>
      {historyQuery.isLoading ? (
        <p className="small">Loading step executions…</p>
      ) : historyQuery.isError ? (
        <p className="small step-tl-error">{(historyQuery.error as Error).message}</p>
      ) : ordered.length > 0 ? (
        <>
          <p className="small step-execution-history-count">
            {ordered.length} step execution{ordered.length === 1 ? '' : 's'}
            {totalMs > 0 ? ` · Total across executions: ${formatDurationMs(totalMs)}` : ''}
          </p>
          <ol className="step-execution-history" aria-label="Step Execution history">
            {ordered.map((execution) => (
              <StepExecutionHistoryRow key={execution.stepExecutionId} execution={execution} />
            ))}
          </ol>
        </>
      ) : (
        <p className="small">No step executions recorded for this step yet.</p>
      )}
    </section>
  );
}

function StepLedgerRowCard({
  apiBase,
  logStreamingEnabled,
  sessionTimelineEnabled,
  structuredHistoryEnabled,
  row,
  workflowId,
  runId,
  sourceTemporal,
  historyPollInterval,
  evidenceStaleTime,
  expanded,
  onToggle,
  isLast,
  routes,
  branches,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  workflowId: string;
  runId: string;
  sourceTemporal: boolean;
  historyPollInterval: number | false;
  evidenceStaleTime: number;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
  routes: AgentRunRouteTemplates;
  branches: CheckpointBranch[];
}) {
  const lastError = formatStepLastError(row.lastError);

  return (
    <article className={`step-tl-row${expanded ? ' step-tl-expanded' : ''}${isLast ? ' step-tl-last' : ''}`}>
      <div className="step-tl-gutter">
        <StatusIcon status={row.status} domain="step" className="step-tl-icon" title={formatStatusLabel(row.status)} />
        {!isLast ? <span className="step-tl-line" /> : null}
      </div>
      <div className="step-tl-content">
        <button
          type="button"
          className="step-tl-toggle"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={expanded ? `Hide details for ${row.title}` : `Show details for ${row.title}`}
        >
          <div className="step-tl-header">
            <span className="step-tl-title">{row.title}</span>
            <span className="step-tl-right">
              <code className="step-tl-tool">{formatStepToolLabel(row.tool)}</code>
              <StepLedgerStatusPill status={row.status} />
              <StepTimingChip row={row} />
              <RemediationCadenceChip row={row} />
              <BranchStatusAffordance branches={branches} />
              <span className="sr-only">{formatStatusLabel(row.status)}</span>
              {row.executionOrdinal > 1 ? <span className="step-execution-pill">Execution {row.executionOrdinal}</span> : null}
              <StepProvenanceMarker row={row} />
              <span className={`step-tl-chevron${expanded ? ' step-tl-chevron-open' : ''}`} aria-hidden="true">›</span>
            </span>
          </div>
          {!expanded && row.summary ? (
            <p className="step-tl-summary">{row.summary}</p>
          ) : null}
          {!expanded && row.dependsOn && row.dependsOn.length > 0 ? (
            <p className="step-tl-summary">Prior step evidence: {row.dependsOn.join(', ')}</p>
          ) : null}
          {!expanded && row.checks.length > 0 ? (
            <div className="step-check-badges">
              {row.checks.map((check, index) => (
                <StepCheckBadge key={`${check.kind}-${check.status}-${index}`} check={check} />
              ))}
            </div>
          ) : null}
          {!expanded ? <StepEvidenceRefs row={row} /> : null}
        </button>
        {expanded ? (
          <div className="step-tl-details">
            <section className="step-tl-detail-section">
              <h4>Summary</h4>
              <p className="small">{row.summary || 'No step summary yet.'}</p>
              {row.waitingReason ? <p className="small">Waiting reason: {row.waitingReason}</p> : null}
              {row.preservedFrom ? (
                <p className="small">
                  Preserved from source run <code>{row.preservedFrom.workflowId}</code> run{' '}
                  <code>{row.preservedFrom.runId}</code> execution {row.preservedFrom.executionOrdinal}.
                </p>
              ) : null}
              {lastError ? <p className="small step-tl-error">Last error: {lastError}</p> : null}
            </section>
            {row.checks.length > 0 ? (
              <section className="step-tl-detail-section">
                <h4>Checks</h4>
                <ul className="step-detail-list">
                  {row.checks.map((check, index) => (
                    <li key={`${check.kind}-${check.status}-${index}`}>
                      <StepCheckBadge check={check} />
                      {check.summary ? <span className="small"> {check.summary}</span> : null}
                      <StepCheckDetails check={check} />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            <RemediationCadenceDetails row={row} />
            <StepTimingDetails row={row} />
            <section className="step-tl-detail-section step-tl-detail-section--logs">
              <h4>Logs & Diagnostics</h4>
              <StepObservabilityGroup
                apiBase={apiBase}
                logStreamingEnabled={logStreamingEnabled}
                sessionTimelineEnabled={sessionTimelineEnabled}
                structuredHistoryEnabled={structuredHistoryEnabled}
                row={row}
                workflowId={workflowId}
                routes={routes}
              />
            </section>
            <section className="step-tl-detail-section">
              <h4>Artifacts</h4>
              <StepArtifactsList artifacts={row.artifacts} />
            </section>
            <StepExecutionHistory
              apiBase={apiBase}
              workflowId={workflowId}
              logicalStepId={row.logicalStepId}
              sourceTemporal={sourceTemporal}
              enabled={expanded}
              pollInterval={historyPollInterval}
              staleTime={evidenceStaleTime}
            />
            <StepWorkloadDetails workload={row.workload} />
            <section className="step-tl-detail-section">
              <h4>Metadata</h4>
              <StepMetadataList row={row} runId={runId} />
            </section>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function LiveLogsPanel({
  apiBase,
  agentRunId,
  isTerminal,
  autoExpand = false,
  disclosure = true,
  routes,
  sessionTimelineEnabled,
  structuredHistoryEnabled,
  optimisticMessages = [],
}: {
  apiBase: string;
  agentRunId: string;
  isTerminal: boolean;
  autoExpand?: boolean;
  // When false, render the log content directly without the collapsible
  // "Live Logs" disclosure chrome (used when the panel is embedded under an
  // existing heading, e.g. the step's "Logs & Diagnostics" section).
  disclosure?: boolean;
  routes: AgentRunRouteTemplates;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
  optimisticMessages?: OptimisticChatSessionMessage[];
}) {
  const [logContent, setLogContent] = useState<TimelineRow[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  // When rendered without disclosure chrome the panel is always visible, so it
  // must start expanded; otherwise honor autoExpand. This avoids delaying the
  // initial log fetch by a render cycle and prevents the embedded toolbar from
  // being permanently hidden when autoExpand is not set.
  const [expanded, setExpanded] = useState(!disclosure || autoExpand);
  const isVisible = usePageVisibility();
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const isTerminalRef = useRef(isTerminal);
  const [sessionSnapshot, setSessionSnapshot] = useState<SessionSnapshot | null>(null);
  const [rawTimelineExpanded, setRawTimelineExpanded] = useState(false);
  const chatBlocks = useMemo(
    () => reduceTimelineRowsToChatBlocks(logContent, agentRunId, optimisticMessages),
    [agentRunId, logContent, optimisticMessages],
  );

  // Keep isTerminalRef current so the onerror handler always sees the latest value.
  useEffect(() => {
    isTerminalRef.current = isTerminal;
  }, [isTerminal]);

  // Reset log state whenever we switch to a different agent run.
  useEffect(() => {
    setLogContent([]);
    lastSeqRef.current = null;
    setViewerState('starting');
    setRawTimelineExpanded(false);
  }, [agentRunId]);

  useEffect(() => {
    if (autoExpand) {
      setExpanded(true);
    }
  }, [autoExpand]);

  // Query for observability summary
  const summaryQuery = useQuery({
    queryKey: ['observability-summary', agentRunId],
    queryFn: () => fetchObservabilitySummary(apiBase, agentRunId, routes.observabilitySummary),
    enabled: !!agentRunId && expanded,
    // The summary indicates stream availability; refetch occasionally if not terminal
    staleTime: 1000 * 10,
    refetchOnMount: 'always',
  });

  const historyQuery = useQuery({
    queryKey: ['agent-run-observability-events', agentRunId],
    queryFn: () => fetchObservabilityEvents(apiBase, agentRunId, routes.observabilityEvents),
    enabled: structuredHistoryEnabled && !!agentRunId && expanded && summaryQuery.isSuccess,
    staleTime: Infinity,
    retry: false,
  });
  const historyRows = useMemo(() => mapEventsToTimelineRows(historyQuery.data), [historyQuery.data]);
  const historyUnavailable = !structuredHistoryEnabled || historyQuery.isError || historyQuery.data === null;
  const historyEmpty = structuredHistoryEnabled && historyQuery.isSuccess && historyRows.length === 0;

  // Legacy fallback: keep merged text available for older runs or partial failures.
  const tailQuery = useQuery({
    queryKey: ['agent-run-tail', agentRunId],
    queryFn: () => fetchMergedTail(apiBase, agentRunId, routes.logsMerged),
    enabled:
      !!agentRunId &&
      expanded &&
      summaryQuery.isSuccess &&
      (!structuredHistoryEnabled || historyUnavailable || historyEmpty),
    staleTime: Infinity,
    retry: false,
  });

  // Keep viewerState in sync with query boundaries
  useEffect(() => {
    if (!expanded) {
      setViewerState('starting');
      return;
    }
    if ((structuredHistoryEnabled && historyQuery.isError && tailQuery.isError) || (!structuredHistoryEnabled && tailQuery.isError)) {
      setViewerState('error');
    } else if (
      summaryQuery.isSuccess &&
      ((structuredHistoryEnabled && historyQuery.isSuccess) || tailQuery.isSuccess)
    ) {
      const summary = summaryQuery.data;
      const runIsTerminal =
        isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
      const supportsStreaming = summary?.supportsLiveStreaming ?? false;

      if (!supportsStreaming) {
        setViewerState(
          historyRows.length > 0 || Boolean(tailQuery.data)
            ? 'ended'
            : 'not_available',
        );
      } else if (runIsTerminal) {
        setViewerState('ended');
      }
    }
  }, [
    expanded,
    structuredHistoryEnabled,
    historyQuery.data,
    historyQuery.isError,
    historyQuery.isSuccess,
    summaryQuery.data,
    summaryQuery.isSuccess,
    tailQuery.data,
    tailQuery.isError,
    tailQuery.isSuccess,
  ]);

  useEffect(() => {
    if (summaryQuery.data?.sessionSnapshot) {
      setSessionSnapshot(summaryQuery.data.sessionSnapshot);
    }
  }, [summaryQuery.data]);

  // Sync structured history into the local timeline when history fetch completes.
  useEffect(() => {
    if (!structuredHistoryEnabled) {
      return;
    }
    if (historyQuery.isSuccess) {
      const sequences = historyQuery.data?.events
        .map((event) => event.sequence)
        .filter((sequence) => Number.isFinite(sequence));
      if (lastSeqRef.current === null && historyRows.length > 0) {
        setLogContent(historyRows);
      }
      lastSeqRef.current = sequences && sequences.length > 0 ? Math.max(...sequences) : null;
      if (historyQuery.data?.sessionSnapshot) {
        setSessionSnapshot(historyQuery.data.sessionSnapshot);
      } else {
        const latestSessionEvent = (historyQuery.data?.events ?? []).findLast(
          (event) => event.session_id && typeof event.session_epoch === 'number',
        );
        if (latestSessionEvent) {
          setSessionSnapshot((prev) => deriveSessionSnapshotFromEvent(latestSessionEvent, prev));
        }
      }
    }
  }, [historyQuery.data, historyQuery.isSuccess, historyRows, structuredHistoryEnabled]);

  // Sync legacy merged-text fallback only when structured history is unavailable.
  useEffect(() => {
    if (tailQuery.isSuccess && tailQuery.data && (!structuredHistoryEnabled || historyUnavailable || historyEmpty)) {
      if (lastSeqRef.current === null) {
        setLogContent(parseArtifactToRows(tailQuery.data));
      }
    }
  }, [historyEmpty, historyUnavailable, structuredHistoryEnabled, tailQuery.data, tailQuery.isSuccess]);

  // Connect to SSE only after tail succeeds, if streaming is supported and active
  useEffect(() => {
    if (!agentRunId || !expanded || !summaryQuery.isSuccess || !isVisible) return;
    if ((structuredHistoryEnabled && !historyQuery.isSuccess && !tailQuery.isSuccess) || (!structuredHistoryEnabled && !tailQuery.isSuccess)) return;

    const summary = summaryQuery.data;
    const runIsTerminal =
      isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
    const supportsStreaming = summary?.supportsLiveStreaming ?? false;

    if (runIsTerminal || !supportsStreaming) return;

    let cancelled = false;

    const since = lastSeqRef.current != null ? `?since=${lastSeqRef.current}` : '';
    const streamUrl = agentRunRoute(
      apiBase,
      routes.logsStream,
      `/agent-runs/${encodeURIComponent(agentRunId)}/logs/stream`,
      agentRunRouteParams(agentRunId),
    );
    const url = `${streamUrl}${since}`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onopen = () => {
      if (!cancelled) setViewerState('live');
    };

    const handleLogChunk = (event: MessageEvent) => {
      if (cancelled) return;
      try {
        const data = ObservabilityEventSchema.parse(JSON.parse(event.data));
        lastSeqRef.current = data.sequence;

        setLogContent((prev) => {
          return [...prev, ...eventToTimelineRows(data)];
        });
        setSessionSnapshot((prev) => deriveSessionSnapshotFromEvent(data, prev));
      } catch {
        // ignore malformed events
      }
    };

    es.onmessage = handleLogChunk;
    es.addEventListener('log_chunk', handleLogChunk);

    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (cancelled) return;
      // Degrade gracefully
      setViewerState(isTerminalRef.current ? 'ended' : 'error');
    };

    return () => {
      cancelled = true;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [
    apiBase,
    expanded,
    historyQuery.isSuccess,
    isVisible,
    summaryQuery.data,
    summaryQuery.isSuccess,
    tailQuery.isSuccess,
    agentRunId,
  ]);

  // Close the stream once the task reaches a terminal state.
  useEffect(() => {
    if (isTerminal && esRef.current) {
      esRef.current.close();
      esRef.current = null;
      setViewerState('ended');
    }
  }, [isTerminal]);

  const statusLabel =
    viewerState === 'live'
      ? 'Connected'
      : viewerState === 'ended'
        ? 'Stream ended'
        : viewerState === 'error'
          ? 'Disconnected — showing artifact backup'
          : viewerState === 'not_available'
            ? 'Not yet available'
            : 'Loading…';

  const emptyLabel =
    viewerState === 'not_available'
      ? '(no log output available yet)'
      : '(waiting for output…)';

  const [wrapLines, setWrapLines] = useState(true);

  const handleCopy = () => {
    if (logContent.length === 0) return;
    copyTextToClipboard(logContent.map((line) => getCopyableRowText(line)).join('\n'));
  };

  const downloadUrl = agentRunRoute(
    apiBase,
    routes.logsMerged,
    `/agent-runs/${encodeURIComponent(agentRunId)}/logs/merged`,
    agentRunRouteParams(agentRunId),
  );
  const summaryErrorMessage = summaryQuery.isError ? (summaryQuery.error as Error).message : null;
  const liveStatusValue =
    summaryQuery.data?.liveStreamStatus
    ?? sessionSnapshot?.status
    ?? (viewerState === 'live' ? 'live' : viewerState);
  const sessionBadges = sessionSnapshot
    ? [
        ['Session', sessionSnapshot.sessionId],
        ['Epoch', String(sessionSnapshot.sessionEpoch)],
        ['Container', sessionSnapshot.containerId],
        ['Thread', sessionSnapshot.threadId],
        ['Active Turn', sessionSnapshot.activeTurnId ?? null],
        ['Live', liveStatusValue],
      ].filter(([, value]) => value) as Array<[string, string]>
    : [];
  const showRawTimeline = rawTimelineExpanded || (sessionTimelineEnabled && logContent.length > 0 && chatBlocks.length === 0);

  const panelBody = (
    <div className="stack live-logs-panel">
      {summaryErrorMessage ? <div className="notice error">{summaryErrorMessage}</div> : null}
      {expanded ? (
        <div className="button-group live-logs-toolbar">
          <label className="live-logs-wrap-toggle">
            <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
            <span className="small">Wrap lines</span>
          </label>
          <button className="secondary small" onClick={handleCopy}>Copy</button>
          <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
        </div>
      ) : null}
      <p className="small">
        Workflow run <code className="text-xs">{agentRunId}</code> — {statusLabel}
      </p>
      {sessionTimelineEnabled ? (
        <p className="small">
          Timeline shows what happened. Continuity artifacts remain the durable drill-down evidence.
        </p>
      ) : null}
      {sessionBadges.length > 0 ? (
        <div className="live-logs-session-badges">
          {sessionBadges.map(([label, value]) => (
            <span key={`${label}-${value}`} className="card live-logs-session-badge">
              <strong>{label}:</strong> <code className="text-xs break-all">{value}</code>
            </span>
          ))}
        </div>
      ) : null}
      <div className={`live-logs-viewer-shell ${wrapLines ? 'is-wrapped' : 'is-unwrapped'}`}>
        {logContent.length === 0 ? (
          <div className="live-logs-empty">{emptyLabel}</div>
        ) : sessionTimelineEnabled ? (
          <div data-testid="chat-session-viewer" className="chat-session-viewer">
            <ChatSessionView apiBase={apiBase} chatBlocks={chatBlocks} rows={logContent} wrapLines={wrapLines} />
            <details
              className="raw-timeline-escape-hatch"
              open={showRawTimeline}
              onToggle={(event) => setRawTimelineExpanded(event.currentTarget.open)}
            >
              <summary>Raw Timeline</summary>
              {showRawTimeline ? (
                <div data-testid="live-logs-timeline-viewer" className="live-logs-viewer">
                  <Virtuoso
                    style={{ height: 400 }}
                    data={logContent}
                    computeItemKey={(_, row) => row.id}
                    itemContent={(_, row) => renderTimelineRow(row, wrapLines, true, apiBase)}
                  />
                </div>
              ) : null}
            </details>
          </div>
        ) : (
          <div data-testid="live-logs-legacy-viewer" className="live-logs-legacy-viewer">
            {logContent.map((line) => renderTimelineRow(line, wrapLines, false, apiBase))}
          </div>
        )}
      </div>
    </div>
  );

  if (!disclosure) {
    return panelBody;
  }

  return (
    <details
      className="stack"
      open={expanded}
    >
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>Live Logs</span>
      </summary>
      {panelBody}
    </details>
  );
}

function BridgeSessionLogsPanel({
  apiBase,
  bridgeSessionId,
  isTerminal,
  projection,
  optimisticMessages,
  setOptimisticMessages,
  actionsEnabled = false,
}: {
  apiBase: string;
  bridgeSessionId: string;
  isTerminal: boolean;
  projection: BridgeSessionProjection;
  optimisticMessages: OptimisticChatSessionMessage[];
  setOptimisticMessages: Dispatch<SetStateAction<OptimisticChatSessionMessage[]>>;
  actionsEnabled?: boolean;
}) {
  const [logContent, setLogContent] = useState<TimelineRow[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  const [wrapLines, setWrapLines] = useState(true);
  const [liveAnnouncement, setLiveAnnouncement] = useState('');
  const [message, setMessage] = useState('');
  const [controlError, setControlError] = useState<string | null>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const isVisible = usePageVisibility();
  const chatBlocks = useMemo(
    () => reduceTimelineRowsToChatBlocks(logContent, bridgeSessionId, optimisticMessages),
    [bridgeSessionId, logContent, optimisticMessages],
  );

  const eventsQuery = useQuery({
    queryKey: ['omnigent-bridge-session-events', bridgeSessionId],
    queryFn: () => fetchBridgeSessionEvents(apiBase, bridgeSessionId),
    enabled: Boolean(bridgeSessionId),
    staleTime: Infinity,
    retry: false,
  });
  const resourcesQuery = useQuery({
    queryKey: ['omnigent-bridge-session-resources', bridgeSessionId],
    queryFn: () => fetchBridgeSessionResources(apiBase, bridgeSessionId),
    enabled: Boolean(bridgeSessionId),
    refetchInterval: (query) => {
      const completeness = (query.state.data as BridgeResourceProjection | undefined)?.completeness;
      return isTerminal && completeness && completeness !== 'pending'
        ? false
        : SESSION_PROJECTION_POLL_MS;
    },
    retry: false,
  });
  const historyRows = useMemo(() => {
    const rows = mapEventsToTimelineRows(eventsQuery.data);
    const envelope = eventsQuery.data && 'terminalEnvelope' in eventsQuery.data
      ? eventsQuery.data.terminalEnvelope
      : null;
    if (envelope) {
      const refs = [
        envelope.diagnosticsRef,
        envelope.captureManifestRef,
        envelope.initialSnapshotRef,
        envelope.finalSnapshotRef,
        envelope.rawEventsRef,
        envelope.normalizedEventsRef,
        envelope.externalStateRef,
      ].filter((ref): ref is string => Boolean(ref));
      const details = [
        envelope.failureClass ? `Failure class: ${envelope.failureClass}.` : '',
        envelope.failureCode ? `Reason: ${envelope.failureCode}.` : '',
        envelope.evidenceIncompleteReason ? `Evidence incomplete: ${envelope.evidenceIncompleteReason}` : '',
        envelope.cleanupState ? `Cleanup: ${envelope.cleanupState}.` : '',
        envelope.leaseReleaseState ? `Lease release: ${envelope.leaseReleaseState}.` : '',
        /configuration|profile|authori[sz]ation/i.test(`${envelope.failureClass ?? ''} ${envelope.failureCode ?? ''}`)
          ? 'Remediation: verify the provider profile, credentials, and execution authorization, then retry.'
          : '',
      ].filter(Boolean).join(' ');
      rows.push({
        id: `${bridgeSessionId}-terminal-envelope`,
        text: [envelope.summary || `Session ${envelope.status}.`, details].filter(Boolean).join(' '),
        stream: 'system',
        kind: envelope.status === 'completed' ? 'response_completed' : 'response_failed',
        sequence: Math.max(0, ...rows.map((row) => row.sequence ?? 0)) + 1,
        timestamp: null,
        sessionId: bridgeSessionId,
        sessionEpoch: null,
        containerId: null,
        threadId: null,
        turnId: null,
        activeTurnId: null,
        metadata: { terminalStatus: envelope.status, artifactRefs: refs, ...envelope },
        rowType: 'boundary',
      });
    }
    if (!eventsQuery.data?.truncated) return rows;
    return [{
      id: `${bridgeSessionId}-retention-gap`,
      text: 'Earlier bridge events are outside the retained replay window. Use diagnostic artifacts for missing evidence.',
      stream: 'system' as TimelineStream,
      kind: 'retention_gap',
      sequence: 0,
      timestamp: null,
      sessionId: bridgeSessionId,
      sessionEpoch: null,
      containerId: null,
      threadId: null,
      turnId: null,
      activeTurnId: null,
      metadata: { degradedEvidence: true },
      rowType: 'system' as const,
    }, ...rows];
  }, [bridgeSessionId, eventsQuery.data]);

  useEffect(() => {
    setLogContent([]);
    lastSeqRef.current = null;
    setViewerState('starting');
  }, [bridgeSessionId]);

  useEffect(() => {
    if (!eventsQuery.isSuccess) return;
    const sequences = eventsQuery.data?.events
      .map((event) => event.sequence)
      .filter((sequence) => Number.isFinite(sequence));
    setLogContent((prev) => {
      const rowsById = new Map(prev.map((row) => [row.id, row]));
      for (const row of historyRows) {
        rowsById.set(row.id, row);
      }
      return Array.from(rowsById.values()).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    });
    if (sequences && sequences.length > 0) {
      lastSeqRef.current = Math.max(lastSeqRef.current ?? 0, ...sequences);
    }
    setViewerState('live');
  }, [eventsQuery.data, eventsQuery.isSuccess, historyRows]);

  useEffect(() => {
    if (isTerminal) setViewerState('ended');
  }, [isTerminal]);

  useEffect(() => {
    if (!bridgeSessionId || !eventsQuery.isSuccess || isTerminal || !isVisible) return;
    let cancelled = false;
    const since = lastSeqRef.current != null ? `?since=${lastSeqRef.current}` : '';
    const es = new EventSource(`${bridgeSessionRoute(apiBase, bridgeSessionId, 'stream')}${since}`, {
      withCredentials: true,
    });
    esRef.current = es;

    const handleBridgeEvent = (event: MessageEvent) => {
      if (cancelled) return;
      try {
        const data = ObservabilityEventSchema.parse(JSON.parse(event.data));
        lastSeqRef.current = data.sequence;
        setLogContent((prev) => [...prev, ...eventToTimelineRows(data)]);
        if (data.kind !== 'assistant_message_delta') {
          setLiveAnnouncement('New session activity is available.');
        }
      } catch {
        // Ignore malformed bridge events; the fetched event index remains visible.
      }
    };

    es.onopen = () => {
      if (!cancelled) setViewerState('live');
    };
    es.onmessage = handleBridgeEvent;
    es.addEventListener('bridge_event', handleBridgeEvent);
    es.addEventListener('terminal', () => {
      if (cancelled) return;
      setViewerState('ended');
      void eventsQuery.refetch();
    });
    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (!cancelled) setViewerState(isTerminal ? 'ended' : 'error');
    };
    return () => {
      cancelled = true;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [apiBase, bridgeSessionId, eventsQuery.isSuccess, isTerminal, isVisible]);

  const statusLabel =
    viewerState === 'live'
      ? 'Connected'
      : viewerState === 'ended'
        ? 'Stream ended'
        : viewerState === 'error'
          ? 'Disconnected - showing bridge event index'
          : 'Loading...';

  const handleCopy = () => {
    if (logContent.length === 0) return;
    copyTextToClipboard(logContent.map((line) => getCopyableRowText(line)).join('\n'));
  };
  const canSend = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.sendFollowUp && !isTerminal);
  const canInterrupt = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.interruptTurn && !isTerminal);
  const canClear = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.clearSession && !isTerminal);
  const canCancel = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.cancelSession && !isTerminal);
  const canHarvest = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.harvestResources && !isTerminal);
  const canStop = Boolean(actionsEnabled && projection.providerSessionRef && projection.capabilities.stop && !isTerminal);
  const canRemove = Boolean(
    actionsEnabled
      && projection.providerSessionRef
      && (
        projection.capabilities.terminalCleanup
        || (isTerminal && projection.compatibilityProfile === 'omnigent.embedded.v1')
      ),
  );
  const canResolveElicitation = Boolean(
    actionsEnabled && projection.providerSessionRef && projection.capabilities.resolveElicitation && !isTerminal,
  );
  const pendingElicitations = useMemo(() => {
    if (!canResolveElicitation) return [];
    const resolvedIds = new Set(logContent
      .filter((row) => ['approval_resolved', 'approval_granted', 'approval_denied', 'intervention_resolved'].includes(row.kind ?? ''))
      .map((row) => String(row.metadata?.elicitationId ?? row.metadata?.requestId ?? '').trim())
      .filter(Boolean));
    const pending = new Map<string, string>();
    for (const row of logContent) {
      if (!['approval_requested', 'intervention_requested'].includes(row.kind ?? '')) continue;
      const id = String(row.metadata?.elicitationId ?? row.metadata?.requestId ?? '').trim();
      if (id && !resolvedIds.has(id)) pending.set(id, row.text || 'Operator decision requested.');
    }
    return Array.from(pending, ([id, text]) => ({ id, text }));
  }, [canResolveElicitation, logContent]);
  const submitMessage = async () => {
    const text = message.trim();
    if (!text || !projection.providerSessionRef || !canSend) return;
    const clientEventKey = crypto.randomUUID();
    const optimistic: OptimisticChatSessionMessage = {
      type: 'chat_session.message_submitted', clientEventKey, sessionId: bridgeSessionId,
      sessionEpoch: 0, message: text, status: 'pending',
    };
    setOptimisticMessages((items) => [...items, optimistic]);
    setMessage(''); setControlError(null); setControlBusy(true);
    try {
      await postBridgeSessionControl(apiBase, projection.providerSessionRef, {
        type: 'message', message: text, clientEventKey,
      });
      setOptimisticMessages((items) => items.map((item) => item.clientEventKey === clientEventKey
        ? { ...item, status: 'delivery_unknown' } : item));
    } catch (error) {
      const detail = (error as Error).message;
      setControlError(detail);
      setOptimisticMessages((items) => items.map((item) => item.clientEventKey === clientEventKey
        ? { ...item, status: 'failed', error: detail } : item));
    } finally { setControlBusy(false); }
  };
  const interrupt = async () => {
    if (!projection.providerSessionRef || !canInterrupt) return;
    setControlError(null); setControlBusy(true);
    try { await postBridgeSessionControl(apiBase, projection.providerSessionRef, { type: 'session.interrupt' }); }
    catch (error) { setControlError((error as Error).message); }
    finally { setControlBusy(false); }
  };
  const runControl = async (payload: Record<string, unknown>) => {
    if (!projection.providerSessionRef) return;
    setControlError(null); setControlBusy(true);
    try { await postBridgeSessionControl(apiBase, projection.providerSessionRef, payload); }
    catch (error) { setControlError((error as Error).message); }
    finally { setControlBusy(false); }
  };
  const durableControlPayload = (type: 'stop_session' | 'cleanup_session') => ({
    type,
    clientEventKey: crypto.randomUUID(),
    idempotencyKey: crypto.randomUUID(),
    expectedWorkflowId: projection.workflowId,
    expectedRunId: projection.runId,
    expectedStepExecutionId: projection.stepExecutionId,
    expectedAgentRunId: projection.agentRunId,
    expectedBridgeSessionId: projection.bridgeSessionId,
    expectedSessionId: projection.providerSessionRef,
    expectedHostId: projection.omnigentHostRef,
    expectedRunnerId: projection.omnigentRunnerRef,
    expectedTurnState: projection.firstMessageState,
    expectedTerminalState: projection.status,
  });
  const removeSession = async () => {
    if (!projection.providerSessionRef || !canRemove) return;
    setControlError(null); setControlBusy(true);
    try { await postBridgeSessionControl(apiBase, projection.providerSessionRef, durableControlPayload('cleanup_session')); }
    catch (error) { setControlError((error as Error).message); }
    finally { setControlBusy(false); }
  };
  const resolveElicitation = async (elicitationId: string, decision: 'approved' | 'rejected') => {
    if (!projection.providerSessionRef || !canResolveElicitation) return;
    setControlError(null); setControlBusy(true);
    try { await resolveBridgeElicitation(apiBase, projection.providerSessionRef, elicitationId, decision); }
    catch (error) { setControlError((error as Error).message); }
    finally { setControlBusy(false); }
  };

  return (
    <div className="stack live-logs-panel">
      {eventsQuery.isError ? <div className="notice error">{(eventsQuery.error as Error).message}</div> : null}
      <div className="button-group live-logs-toolbar">
        <label className="live-logs-wrap-toggle">
          <input type="checkbox" checked={wrapLines} onChange={(event) => setWrapLines(event.target.checked)} />
          <span className="small">Wrap lines</span>
        </label>
        <button className="secondary small" onClick={handleCopy}>Copy</button>
      </div>
      <p className="small">
        Bridge session <code className="text-xs">{bridgeSessionId}</code> - {statusLabel}
      </p>
      <section className="card stack" aria-label="Omnigent runtime identity">
        <h3>Codex via Omnigent</h3>
        <dl className="details-grid">
          {([
            ['Provider Profile', projection.providerProfileId],
            ['Execution profile', projection.executionProfileRef],
            ['Launch policy', projection.launchPolicyRef],
            ['Host mode', projection.hostMode],
            ['Launch snapshot', projection.effectiveLaunchSnapshotRef],
            ['Source mode', projection.compatibilityProfile],
            ['Workflow', projection.workflowId],
            ['Agent run', projection.agentRunId],
            ['Bridge session', bridgeSessionId],
            ['Provider session', projection.providerSessionRef],
            ['Omnigent host', projection.omnigentHostRef],
            ['Omnigent runner', projection.omnigentRunnerRef],
            ['Credential generation', projection.credentialGeneration],
            ['Provider lease', projection.providerLeaseRef],
            ['Host binding', projection.hostBindingRef],
            ['Host lease', projection.hostLeaseRef],
          ] as Array<[string, string | number | undefined]>).filter(([, value]) => value !== undefined && value !== '').map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd><code className="text-xs break-all">{value}</code></dd></div>
          ))}
        </dl>
      </section>
      {eventsQuery.data && 'terminalEnvelope' in eventsQuery.data && eventsQuery.data.terminalEnvelope
        ? <BridgeTerminalEvidence apiBase={apiBase} envelope={eventsQuery.data.terminalEnvelope} />
        : null}
      <details className="card bridge-resource-evidence" open={isTerminal}>
        <summary>Resource evidence — {resourcesQuery.data?.completeness || 'harvesting'}</summary>
        {resourcesQuery.isError ? <div className="notice error">{(resourcesQuery.error as Error).message}</div> : null}
        {resourcesQuery.data?.groups.some((group) => group.resources.length > 0) ? (
          <div className="stack" style={{ marginTop: '0.75rem' }}>
            {resourcesQuery.data.groups.filter((group) => group.resources.length > 0).map((group) => (
              <section key={group.groupKey} aria-label={group.title}>
                <h4>{group.title}</h4>
                <ul className="stack gap-1">
                  {group.resources.slice(0, 25).map((resource, index) => {
                    const href = resource.artifactRef ? artifactRefHref(apiBase, resource.artifactRef) : null;
                    return (
                      <li key={`${group.groupKey}-${resource.label}-${index}`}>
                        <span>{resource.label}</span>
                        {resource.path ? <code className="text-xs break-all"> — {resource.path}</code> : null}
                        {resource.sourceEventSequence != null ? <span className="small"> (event {resource.sourceEventSequence})</span> : null}
                        {href && resource.previewAvailable ? (
                          <a className="button secondary small" href={href} target="_blank" rel="noreferrer" aria-label={`Open ${resource.label}`}>Open</a>
                        ) : null}
                        {href && resource.downloadAvailable ? (
                          <a className="button secondary small" href={href} download aria-label={`Download ${resource.label}`}>Download</a>
                        ) : null}
                        {resource.relatedArtifactRefs?.map((ref, relatedIndex) => {
                          const relatedHref = artifactRefHref(apiBase, ref);
                          return relatedHref ? <a key={ref} className="button secondary small" href={relatedHref} target="_blank" rel="noreferrer" aria-label={`Open related evidence ${relatedIndex + 1} for ${resource.label}`}>Related evidence</a> : null;
                        })}
                        {resource.unavailableReason ? <div className="small">Unavailable: {resource.unavailableReason}</div> : null}
                      </li>
                    );
                  })}
                  {group.resources.length > 25 ? <li className="small">Showing 25 of {group.resources.length} resources. Use the capture manifest for the complete bounded index.</li> : null}
                </ul>
              </section>
            ))}
          </div>
        ) : <p className="small">{isTerminal ? 'No harvested resource evidence is available.' : 'Harvesting resource evidence…'}</p>}
      </details>
      <div className={`live-logs-viewer-shell ${wrapLines ? 'is-wrapped' : 'is-unwrapped'}`}>
        {logContent.length === 0 ? (
          <div className="live-logs-empty">(waiting for bridge session events...)</div>
        ) : (
          <div data-testid="chat-session-viewer" className="chat-session-viewer">
            <ChatSessionView
              apiBase={apiBase}
              chatBlocks={chatBlocks}
              rows={logContent}
              wrapLines={wrapLines}
              resources={resourcesQuery.data?.groups.flatMap((group) => group.resources) || []}
              liveAnnouncement={liveAnnouncement}
            />
            <details className="raw-timeline-escape-hatch">
              <summary>Raw Timeline</summary>
              <div data-testid="live-logs-timeline-viewer" className="live-logs-viewer">
                <Virtuoso
                  style={{ height: 400 }}
                  data={logContent}
                  computeItemKey={(_, row) => row.id}
                  itemContent={(_, row) => renderTimelineRow(row, wrapLines, true, apiBase)}
                />
              </div>
            </details>
          </div>
        )}
      </div>
      {(canSend || canInterrupt || canClear || canCancel || canHarvest || canStop || canRemove || pendingElicitations.length > 0 || optimisticMessages.length > 0) ? (
        <section className="stack chat-session-controls" aria-label="Bridge session controls">
          <h3>Session Controls</h3>
          {controlError ? <div className="notice error">{controlError}</div> : null}
          {optimisticMessages.map((item) => (
            <div key={item.clientEventKey} role="status" className={`chat-session-message chat-session-message-${item.status}`}>
              Operator message · {item.status === 'pending' ? 'Sending' : item.status === 'failed' ? 'Failed' : 'Delivery confirmation pending'}
              {item.error ? `: ${item.error}` : null}
            </div>
          ))}
          {canSend ? <><label htmlFor="bridge-follow-up">Follow-up message</label><textarea id="bridge-follow-up" value={message} onChange={(event) => setMessage(event.target.value)} disabled={controlBusy} rows={3} /><button type="button" onClick={() => void submitMessage()} disabled={controlBusy || !message.trim()}>Send follow-up</button></> : null}
          {pendingElicitations.map((elicitation) => (
            <section key={elicitation.id} className="stack" aria-label={`Pending operator request ${elicitation.id}`}>
              <p>{elicitation.text}</p>
              <div className="button-group">
                <button type="button" onClick={() => void resolveElicitation(elicitation.id, 'approved')} disabled={controlBusy}>Approve</button>
                <button type="button" className="secondary" onClick={() => void resolveElicitation(elicitation.id, 'rejected')} disabled={controlBusy}>Reject</button>
              </div>
            </section>
          ))}
          {canInterrupt ? <button type="button" className="secondary" onClick={() => void interrupt()} disabled={controlBusy}>Interrupt turn</button> : null}
          {canHarvest ? <button type="button" className="secondary" onClick={() => void runControl({ type: 'harvest_session', clientEventKey: crypto.randomUUID() }).then(() => resourcesQuery.refetch())} disabled={controlBusy}>Harvest evidence</button> : null}
          {canStop ? <button type="button" className="danger" onClick={() => { if (window.confirm('Stop this Omnigent session?')) void runControl(durableControlPayload('stop_session')); }} disabled={controlBusy}>Stop session</button> : null}
          {canClear ? <button type="button" className="secondary" onClick={() => { if (window.confirm('Clear this bridge session?')) void runControl({ type: 'clear_session' }); }} disabled={controlBusy}>Clear session</button> : null}
          {canCancel ? <button type="button" className="danger" onClick={() => { if (window.confirm('Cancel this bridge session?')) void runControl({ type: 'session.cancel' }); }} disabled={controlBusy}>Cancel session</button> : null}
          {canRemove ? <button type="button" className="danger" onClick={() => { if (window.confirm('Remove the owned Omnigent session after evidence harvest?')) void removeSession(); }} disabled={controlBusy}>Remove owned session</button> : null}
        </section>
      ) : null}
    </div>
  );
}

function InterventionPanel({
  audit,
}: {
  audit: Array<{
    action: string;
    transport: string;
    summary: string;
    detail?: string | null | undefined;
    createdAt: string;
  }>;
}) {
  return (
    <section className="stack">
      <div>
        <h3>Intervention</h3>
        <p className="small">
          Intervention history is shown here. Current workflow operations are available from the workflow actions menu.
        </p>
      </div>

      <div className="stack">
        <h4>Intervention History</h4>
        {audit.length === 0 ? (
          <p className="small">No intervention actions recorded yet.</p>
        ) : (
          <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {audit.map((entry, index) => (
              <li key={`${entry.createdAt}-${entry.action}-${index}`} className="card">
                <strong>{entry.summary}</strong>
                <div className="small">{formatWhen(entry.createdAt)}</div>
                <div className="small">
                  <code>{entry.transport}</code>
                </div>
                {entry.detail ? <p className="small">{entry.detail}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function StaticLogPanel({
  apiBase,
  agentRunId,
  stream,
  routes,
}: {
  apiBase: string;
  agentRunId: string;
  stream: 'stdout' | 'stderr';
  routes: AgentRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);

  const streamQuery = useQuery({
    queryKey: ['agent-run-stream', agentRunId, stream],
    queryFn: () =>
      fetchStream(
        apiBase,
        agentRunId,
        stream,
        stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
      ),
    enabled: !!agentRunId && expanded,
    retry: false,
  });

  const title = stream === 'stdout' ? 'Stdout' : 'Stderr';

  const downloadUrl = agentRunRoute(
    apiBase,
    stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
    `/agent-runs/${encodeURIComponent(agentRunId)}/logs/${stream}`,
    agentRunRouteParams(agentRunId),
  );

  return (
    <LogPanel
      title={title}
      text={streamQuery.data}
      isLoading={streamQuery.isLoading}
      isError={streamQuery.isError}
      errorMessage={`Error loading ${stream}`}
      emptyMessage={`(no ${stream} output)`}
      downloadUrl={downloadUrl}
      onExpandedChange={setExpanded}
      ariaLabel={`${title} output`}
    />
  );
}

function DiagnosticsPanel({
  apiBase,
  agentRunId,
  routes,
}: {
  apiBase: string;
  agentRunId: string;
  routes: AgentRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);

  const diagQuery = useQuery({
    queryKey: ['agent-run-diagnostics', agentRunId],
    queryFn: () => fetchDiagnostics(apiBase, agentRunId, routes.diagnostics),
    enabled: !!agentRunId && expanded,
    retry: false,
  });

  const downloadUrl = agentRunRoute(
    apiBase,
    routes.diagnostics,
    `/agent-runs/${encodeURIComponent(agentRunId)}/diagnostics`,
    agentRunRouteParams(agentRunId),
  );

  return (
    <LogPanel
      title="Diagnostics"
      text={diagQuery.data}
      isLoading={diagQuery.isLoading}
      isError={diagQuery.isError}
      errorMessage="Error loading diagnostics"
      emptyMessage="(no diagnostics output)"
      downloadUrl={downloadUrl}
      onExpandedChange={setExpanded}
      ariaLabel="Diagnostics output"
    />
  );
}

function TargetDiagnosticsPanel({
  diagnostics,
}: {
  diagnostics: z.infer<typeof TargetDiagnosticsSchema> | null | undefined;
}) {
  if (!diagnostics) return null;
  const hasTargets = diagnostics.targets.length > 0;
  const recovery = diagnostics.recovery;

  if (!hasTargets && !recovery && !diagnostics.degradedReason) return null;

  return (
    <section className="stack td-evidence-region">
      <h3>Target Diagnostics</h3>
      {diagnostics.degradedReason ? (
        <p className="small">Degraded: {formatStatusLabel(diagnostics.degradedReason)}</p>
      ) : null}
      {hasTargets ? (
        <div className="grid-2">
          {diagnostics.targets.map((target, index) => (
            <div
              className="card"
              key={`${target.targetKind}-${target.stepId || index}`}
            >
              <h4>{target.label}</h4>
              <p className="small">
                {target.targetKind === 'objective' ? 'Workflow objective' : `Step ${formatOptionalValue(target.stepId)}`}
              </p>
              {target.attachments.length > 0 ? (
                <ul className="step-detail-list">
                  {target.attachments.map((attachment, attachmentIndex) => (
                    <li key={`${attachment.artifactRef || attachment.filename || attachmentIndex}`}>
                      <strong>{attachment.filename || 'Attachment'}</strong>
                      {attachment.contentType ? <span className="small"> {attachment.contentType}</span> : null}
                      {attachment.artifactRef ? (
                        <>
                          {' '}
                          <code className="text-xs break-all">{attachment.artifactRef}</code>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="small">No attachments recorded for this target.</p>
              )}
              {target.refs.length > 0 ? (
                <div>
                  <h5>Evidence</h5>
                  <ul className="step-detail-list">
                    {target.refs.map((ref, refIndex) => (
                      <li key={`${ref.refKind}-${ref.artifactRef || ref.path || refIndex}`}>
                        <span>{formatStatusLabel(ref.refKind)}</span>{' '}
                        <code className="text-xs break-all">{ref.artifactRef || ref.path}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {target.failures.length > 0 ? (
                <div>
                  <h5>Failures</h5>
                  <ul className="step-detail-list">
                    {target.failures.map((failure, failureIndex) => (
                      <li key={`${failure.phase}-${failureIndex}`}>
                        <strong>{formatStatusLabel(failure.phase)}:</strong> {failure.message}
                        {failure.evidenceRef ? (
                          <>
                            {' '}
                            <code className="text-xs break-all">{failure.evidenceRef}</code>
                          </>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="small">No target diagnostics were recorded.</p>
      )}
      {recovery ? (
        <div>
          <h4>Recovery</h4>
          {recovery.resumed && recovery.sourceWorkflowId ? (
            <p>Resumed from {recovery.sourceWorkflowId}</p>
          ) : recovery.resumed ? (
            <p>Resumed from a previous run.</p>
          ) : null}
          <ul className="step-detail-list">
            {recovery.sourceRunId ? (
              <li><strong>Source run:</strong> <code className="text-xs break-all">{recovery.sourceRunId}</code></li>
            ) : null}
            {recovery.checkpointRef ? (
              <li><strong>Checkpoint:</strong> <code className="text-xs break-all">{recovery.checkpointRef}</code></li>
            ) : null}
            {recovery.failedRecoveryPhase ? (
              <li><strong>Failed phase:</strong> {formatStatusLabel(recovery.failedRecoveryPhase)}</li>
            ) : null}
          </ul>
          {recovery.preservedSteps.length > 0 ? (
            <div>
              <h5>Preserved Steps</h5>
              <ul className="step-detail-list">
                {recovery.preservedSteps.map((step) => (
                  <li key={`${step.logicalStepId}-${step.sourceRunId || ''}`}>
                    <strong>{step.title || step.logicalStepId}</strong>
                    {step.sourceExecutionOrdinal ? <span className="small"> execution {step.sourceExecutionOrdinal}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function RecoveryEvidencePanel({
  recovery,
  resume,
  diagnostics,
  onResumeFromFailedStep,
  onRerun,
  busy,
  taskEditingOn,
}: {
  recovery: z.infer<typeof RecoveryEligibilitySchema> | null | undefined;
  resume: ExecutionDetail['resume'];
  diagnostics: z.infer<typeof TargetDiagnosticsSchema> | null | undefined;
  onResumeFromFailedStep: () => void;
  onRerun: () => void;
  busy: boolean;
  taskEditingOn: boolean;
}) {
  const diagnosticsRecovery = diagnostics?.recovery ?? null;
  if (!recovery && !diagnosticsRecovery && !resume?.checkpointRef) return null;
  const checkpointRef = recovery?.checkpointRef || resume?.checkpointRef || diagnostics?.recovery?.checkpointRef || null;
  const requiredBoundary = recovery?.checkpointBoundary || recovery?.requiredBoundary || 'before_execution';
  const disabledReason = recovery?.disabledReasonCode || resume?.disabledReason || null;
  const sourceWorkflowId = recovery?.sourceWorkflowId || diagnostics?.recovery?.sourceWorkflowId || null;
  const sourceRunId = recovery?.sourceRunId || resume?.sourceRunId || diagnostics?.recovery?.sourceRunId || null;
  const diagnosticsEvidence = recovery?.evidence?.filter((item) =>
    ['environment', 'provider_lease', 'preflight', 'sidecar', 'ghcr', 'diagnostics'].includes(item.category),
  ) || [];
  const preservedSteps = diagnosticsRecovery?.preservedSteps || [];
  const disabledGuidance = (() => {
    const runtime = recovery?.targetRuntimeId || 'the selected runtime';
    const kind = recovery?.checkpointKind || 'the selected checkpoint kind';
    const route = recovery?.restoreActivity || 'a registered restore route';
    switch (disabledReason) {
      case 'CHECKPOINT_RESTORE_UNSUPPORTED': return `${runtime} does not support workspace checkpoint restore. Retry from source.`;
      case 'CHECKPOINT_RESTORE_ROUTE_MISSING': return `${runtime} has no registered restore route (${route}). Retry from source.`;
      case 'CHECKPOINT_KIND_INCOMPATIBLE': return `${runtime} cannot restore ${kind}. Retry from source.`;
      case 'CHECKPOINT_DESTINATION_IDENTITY_MISMATCH': return 'The selected destination runtime changed. Refresh recovery evidence before retrying.';
      case 'CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING': return 'The immutable runtime capability snapshot is missing. Refresh recovery evidence.';
      case 'CHECKPOINT_CAPABILITY_DIGEST_MISMATCH': return `${runtime} capabilities changed. Refresh recovery evidence before retrying.`;
      case 'CHECKPOINT_ARTIFACT_INVALID': return 'The checkpoint artifact or its source identity is invalid. Retry from source.';
      case 'RECOVERY_TARGET_UNAVAILABLE': return 'The checkpoint is valid, but this run has no supported recovery target. Use Edit for rerun or Full retry.';
      case 'CHECKPOINT_SIDE_EFFECT_UNSAFE': return 'Prior side effects make checkpoint restoration unsafe. Resolve them or retry from source.';
      case 'CHECKPOINT_BOUNDARY_INCOMPATIBLE': return 'This checkpoint boundary has no legal continuation phase. Retry from source.';
      case 'CHECKPOINT_CAPTURE_UNSUPPORTED': return `${runtime} cannot capture a restorable workspace checkpoint. Retry from source.`;
      case 'SAME_SESSION_UNREACHABLE': return 'The prior session is no longer reachable. Retry from source.';
      case 'SAME_SESSION_CONTINUATION_UNSUPPORTED': return `${runtime} does not support same-session continuation. Retry from source.`;
      default: return null;
    }
  })();

  return (
    <section className="detail-section">
      <h3>Recovery evidence</h3>
      {recovery?.operatorGuidance === 'fix_environment' ? (
        <p className="small">
          Fix the runtime environment before retrying. Check the diagnostic refs below for sidecar, registry,
          preflight, or provider lease failures.
        </p>
      ) : recovery?.eligible ? (
        <p className="small">
          Resume from checkpoint is the default recovery action for boundary {formatStatusLabel(requiredBoundary)}.
        </p>
      ) : disabledReason ? (
        <p className="small">Resume from checkpoint unavailable: {disabledGuidance || formatStatusLabel(disabledReason)}</p>
      ) : null}

      <div className="action-row">
        {taskEditingOn && recovery?.eligible ? (
          <button type="button" className="queue-action" disabled={busy} onClick={onResumeFromFailedStep}>
            Resume from checkpoint
          </button>
        ) : null}
        {taskEditingOn ? (
          <button type="button" className="secondary" disabled={busy} onClick={onRerun}>
            Retry from source
          </button>
        ) : null}
      </div>

      <ul className="step-detail-list">
        {checkpointRef ? (
          <li><strong>Checkpoint:</strong> <code className="text-xs break-all">{checkpointRef}</code></li>
        ) : null}
        {requiredBoundary ? (
          <li><strong>Required boundary:</strong> {formatStatusLabel(requiredBoundary)}</li>
        ) : null}
        {sourceWorkflowId ? (
          <li><strong>Source workflow:</strong> <code className="text-xs break-all">{sourceWorkflowId}</code></li>
        ) : null}
        {sourceRunId ? (
          <li><strong>Source run:</strong> <code className="text-xs break-all">{sourceRunId}</code></li>
        ) : null}
        {recovery?.checkpointKind ? (
          <li><strong>Checkpoint kind:</strong> {formatStatusLabel(recovery.checkpointKind)}</li>
        ) : null}
        {recovery?.sessionRecoverable != null ? (
          <li><strong>Session reattach:</strong> {recovery.sessionRecoverable ? 'supported' : 'unavailable'}</li>
        ) : null}
        {recovery?.workspaceRecoverable != null ? (
          <li><strong>Workspace restore:</strong> {recovery.workspaceRecoverable ? 'supported' : 'unavailable'}</li>
        ) : null}
        {recovery?.authoritativeWorkspaceCheckpointKind ? (
          <li><strong>Authoritative workspace checkpoint:</strong> {formatStatusLabel(recovery.authoritativeWorkspaceCheckpointKind)}</li>
        ) : null}
        {recovery?.partialRecoveryReason ? (
          <li><strong>Partial recovery:</strong> {recovery.partialRecoveryReason}</li>
        ) : null}
        {recovery?.runtimeId ? (
          <li><strong>Runtime:</strong> {formatStatusLabel(recovery.runtimeId)}</li>
        ) : null}
        {recovery?.deploymentGeneration ? (
          <li><strong>Deployment generation:</strong> <code>{recovery.deploymentGeneration}</code></li>
        ) : null}
        {recovery?.promotionState ? (
          <li><strong>Promotion state:</strong> {formatStatusLabel(recovery.promotionState)}</li>
        ) : null}
        {recovery?.capabilitySetVersion ? (
          <li><strong>Capability set:</strong> <code>{recovery.capabilitySetVersion}</code></li>
        ) : null}
        {recovery?.capabilityDigest ? (
          <li><strong>Capability digest:</strong> <code className="text-xs break-all">{recovery.capabilityDigest}</code></li>
        ) : null}
        {diagnosticsEvidence.map((item, index) => (
          <li key={`${item.category}-${item.artifactRef || item.reasonCode || index}`}>
            <strong>{formatStatusLabel(item.category)}:</strong>{' '}
            {item.artifactRef ? <code className="text-xs break-all">{item.artifactRef}</code> : formatStatusLabel(item.status)}
            {item.reasonCode ? <span className="small"> {formatStatusLabel(item.reasonCode)}</span> : null}
          </li>
        ))}
      </ul>
      {preservedSteps.length > 0 ? (
        <div>
          <h4>Preserved provenance</h4>
          <ul className="step-detail-list">
            {preservedSteps.map((step) => (
              <li key={`${step.logicalStepId}-${step.sourceExecutionOrdinal || ''}`}>
                Preserved step: {step.title || step.logicalStepId}
                {step.sourceWorkflowId ? <span className="small"> from <code>{step.sourceWorkflowId}</code></span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function SessionContinuityPanel({
  apiBase,
  agentRunId,
  targetRuntime,
  isTerminal,
  invalidateWorkflowDetail,
  routes,
  optimisticMessages,
  setOptimisticMessages,
  compact = false,
}: {
  apiBase: string;
  agentRunId: string;
  targetRuntime: string | null | undefined;
  isTerminal: boolean;
  invalidateWorkflowDetail: () => void;
  routes: AgentRunRouteTemplates;
  optimisticMessages: OptimisticChatSessionMessage[];
  setOptimisticMessages: Dispatch<SetStateAction<OptimisticChatSessionMessage[]>>;
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const canPollSessionCapabilities = isCodexManagedRuntime(targetRuntime);
  const summaryQuery = useQuery({
    queryKey: ['observability-summary', agentRunId],
    queryFn: () => fetchObservabilitySummary(apiBase, agentRunId, routes.observabilitySummary),
    enabled: !!agentRunId,
    staleTime: 1000 * 10,
    refetchInterval: (query) => {
      if (!canPollSessionCapabilities || query.state.error) {
        return false;
      }
      if (!query.state.data?.sessionSnapshot) {
        return getSessionCapabilityRefetchInterval(isTerminal, false, false);
      }
      if (isTerminal) {
        return false;
      }
      return SESSION_PROJECTION_POLL_MS;
    },
    retry: false,
  });
  const summary = summaryQuery.data;
  const sessionSnapshot = summary?.sessionSnapshot ?? null;
  const interventionCapabilities = summary?.interventionCapabilities ?? InterventionCapabilitiesSchema.parse({});
  const sessionId = sessionSnapshot?.sessionId ?? null;
  const [followUpMessage, setFollowUpMessage] = useState('');
  const [panelError, setPanelError] = useState<string | null>(null);
  const optimisticMessageSequenceRef = useRef(0);

  const projectionQuery = useQuery({
    queryKey: ['agent-run-session-projection', agentRunId, sessionId],
    queryFn: () => {
      if (!sessionId) return Promise.resolve(null);
      return fetchArtifactSessionProjection(apiBase, agentRunId, sessionId, routes.artifactSession);
    },
    enabled: Boolean(agentRunId && sessionId && summaryQuery.isSuccess),
    refetchInterval: (query) => {
      return getSessionProjectionRefetchInterval(
        isTerminal,
        Boolean(query.state.data),
        Boolean(query.state.error),
      );
    },
    retry: false,
  });

  const resourcesQuery = useQuery({
    queryKey: ['session-resources', sessionId],
    queryFn: () => {
      if (!sessionId) return Promise.resolve(null);
      return fetchSessionResources(apiBase, agentRunId, sessionId, routes.sessionResources);
    },
    enabled: Boolean(agentRunId && sessionId && summaryQuery.isSuccess),
    refetchInterval: (query) => {
      return getSessionProjectionRefetchInterval(
        isTerminal,
        Boolean(query.state.data),
        Boolean(query.state.error),
      );
    },
    retry: false,
  });

  const controlMutation = useMutation({
    mutationFn: async (body: ArtifactSessionControlRequest) => {
      if (!sessionId) throw new Error('Managed session is unavailable.');
      return controlArtifactSession(apiBase, agentRunId, sessionId, body, routes.artifactSessionControl);
    },
    onSuccess: (result) => {
      setPanelError(null);
      void queryClient.setQueryData(
        ['agent-run-session-projection', agentRunId, sessionId],
        result.projection,
      );
      void queryClient.invalidateQueries({ queryKey: ['observability-summary', agentRunId] });
      invalidateWorkflowDetail();
      if (result.action === 'continue_same_session') {
        setFollowUpMessage('');
      }
    },
    onError: (error: Error) => setPanelError(error.message),
  });

  const followUpMutation = useMutation({
    mutationFn: async (event: ChatSessionMessageEvent) => {
      if (!sessionId) throw new Error('Managed session is unavailable.');
      return controlArtifactSession(
        apiBase,
        agentRunId,
        sessionId,
        chatSessionMessageEventToControlRequest(event),
        routes.artifactSessionControl,
      );
    },
    onSuccess: (result, event) => {
      setPanelError(null);
      setOptimisticMessages((messages) =>
        messages.filter((message) => message.clientEventKey !== event.clientEventKey),
      );
      void queryClient.setQueryData(
        ['agent-run-session-projection', agentRunId, sessionId],
        result.projection,
      );
      void queryClient.invalidateQueries({ queryKey: ['observability-summary', agentRunId] });
      invalidateWorkflowDetail();
      setFollowUpMessage('');
    },
    onError: (error: Error, event) => {
      setPanelError(error.message);
      setOptimisticMessages((messages) =>
        messages.map((message) =>
          message.clientEventKey === event.clientEventKey
            ? { ...message, status: 'failed', error: error.message }
            : message,
        ),
      );
    },
  });

  if (summaryQuery.isLoading) {
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <p className="small">Loading session capabilities...</p>
      </section>
    );
  }
  if (summaryQuery.isError) {
    return null;
  }
  if (!sessionId) {
    return null;
  }
  if (projectionQuery.isLoading) {
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <p className="small">Loading session continuity...</p>
      </section>
    );
  }
  if (projectionQuery.isError) {
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <div className="notice error">{(projectionQuery.error as Error).message}</div>
      </section>
    );
  }
  if (!projectionQuery.data) {
    if (isTerminal) {
      return null;
    }
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <p className="small">Waiting for session continuity artifacts...</p>
      </section>
    );
  }

  const projection = projectionQuery.data;
  const sessionResources = resourcesQuery.data?.resources ?? [];
  const sessionResourceGroups = Array.from(
    sessionResources.reduce((groups, resource) => {
      const key = resource.groupKey || 'resources';
      const existing = groups.get(key) ?? { title: resource.groupTitle, resources: [] as typeof sessionResources };
      existing.resources.push(resource);
      groups.set(key, existing);
      return groups;
    }, new Map<string, { title: string; resources: typeof sessionResources }>()),
  );
  const latestBadges = [
    ['Latest Summary', projection.latest_summary_ref?.artifact_id ?? null],
    ['Latest Checkpoint', projection.latest_checkpoint_ref?.artifact_id ?? null],
    ['Latest Control', projection.latest_control_event_ref?.artifact_id ?? null],
    ['Latest Reset', projection.latest_reset_boundary_ref?.artifact_id ?? null],
  ].filter(([, artifactId]) => artifactId !== null) as Array<[string, string]>;
  const busy = controlMutation.isPending || followUpMutation.isPending;
  const canSendFollowUp = Boolean(sessionId && interventionCapabilities.sendFollowUp && !isTerminal);
  const canClearSession = Boolean(sessionId && interventionCapabilities.clearSession && !isTerminal);
  const canInterruptTurn = Boolean(sessionId && interventionCapabilities.interruptTurn && !isTerminal);
  const canCancelSession = Boolean(sessionId && interventionCapabilities.cancelSession && !isTerminal);
  const unavailableReason = (capabilityAvailable: boolean, actionLabel: string) => {
    if (busy) return 'Session control request in progress.';
    if (isTerminal) return `${actionLabel} unavailable because this workflow is terminal.`;
    if (!sessionId) return `${actionLabel} unavailable because the managed session is unavailable.`;
    if (!capabilityAvailable) return `${actionLabel} is not supported for this session.`;
    return null;
  };
  const sendDisabledReason = followUpMessage.trim()
    ? unavailableReason(canSendFollowUp, 'Follow-up')
    : (canSendFollowUp ? 'Enter a message to send a follow-up.' : unavailableReason(canSendFollowUp, 'Follow-up'));
  const clearDisabledReason = unavailableReason(canClearSession, 'Clear / Reset');
  const interruptDisabledReason = unavailableReason(canInterruptTurn, 'Interrupt turn');
  const cancelDisabledReason = unavailableReason(canCancelSession, 'Cancel session');

  const submitFollowUp = () => {
    const message = followUpMessage.trim();
    if (!message || !sessionId) return;
    const event: ChatSessionMessageEvent = {
      type: 'chat_session.message_submitted',
      clientEventKey: `MM-1015:MM-977:${sessionId}:${sessionSnapshot?.sessionEpoch ?? projection.session_epoch}:${optimisticMessageSequenceRef.current++}`,
      sessionId,
      sessionEpoch: sessionSnapshot?.sessionEpoch ?? projection.session_epoch,
      message,
    };
    setPanelError(null);
    setOptimisticMessages((messages) => [
      ...messages.filter((pending) => pending.clientEventKey !== event.clientEventKey),
      { ...event, status: 'pending' },
    ]);
    followUpMutation.mutate(event);
  };

  const clearSession = () => {
    if (!window.confirm('Clear this managed session and start a new context boundary?')) return;
    const requestId = crypto.randomUUID();
    setPanelError(null);
    controlMutation.mutate({
      schemaVersion: 1,
      controlRequestId: requestId,
      idempotencyKey: requestId,
      action: 'clear_session',
      expectedSessionEpoch: sessionSnapshot?.sessionEpoch ?? projection.session_epoch,
      reason: 'Operator confirmed clear session',
    });
  };

  const interruptTurn = () => {
    const activeTurnId = sessionSnapshot?.activeTurnId;
    if (!activeTurnId) return;
    const requestId = crypto.randomUUID();
    setPanelError(null);
    controlMutation.mutate({
      schemaVersion: 1,
      controlRequestId: requestId,
      idempotencyKey: requestId,
      action: 'interrupt_turn',
      expectedSessionEpoch: sessionSnapshot?.sessionEpoch ?? projection.session_epoch,
      expectedTurnId: activeTurnId,
    });
  };

  const cancelSession = () => {
    if (!window.confirm('Stop this managed session?')) return;
    const requestId = crypto.randomUUID();
    setPanelError(null);
    controlMutation.mutate({
      schemaVersion: 1,
      controlRequestId: requestId,
      idempotencyKey: requestId,
      action: 'cancel_session',
      expectedSessionEpoch: sessionSnapshot?.sessionEpoch ?? projection.session_epoch,
      reason: 'Operator confirmed stop session',
    });
  };

  const stopActiveTurn = () => {
    if (busy || isTerminal) return;
    if (canInterruptTurn) {
      interruptTurn();
      return;
    }
    if (canCancelSession) {
      cancelSession();
    }
  };

  const handleFollowUpKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Escape') return;
    if (!canInterruptTurn && !canCancelSession) return;
    event.preventDefault();
    stopActiveTurn();
  };

  const renderControlButton = ({
    label,
    className,
    disabledReason,
    onClick,
    hiddenWhenUnavailable = false,
  }: {
    label: string;
    className: string;
    disabledReason: string | null;
    onClick: () => void;
    hiddenWhenUnavailable?: boolean;
  }) => {
    const disabled = Boolean(disabledReason);
    if (!compact && hiddenWhenUnavailable && disabled) return null;
    return (
      <span className="chat-session-control-action">
        <button
          type="button"
          className={className}
          disabled={disabled}
          onClick={onClick}
          aria-describedby={disabled ? `session-control-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-reason` : undefined}
        >
          {label}
        </button>
        {disabled && compact ? (
          <span
            id={`session-control-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-reason`}
            className="chat-session-disabled-reason"
          >
            {disabledReason}
          </span>
        ) : null}
      </span>
    );
  };

  return (
    <section className={`stack ${compact ? 'chat-session-controls' : ''}`}>
      <div>
        <h3>{compact ? 'Session Controls' : 'Session Continuity'}</h3>
        {!compact ? (
          <p className="small">
            Continuity artifacts are durable evidence and drill-down for this session.
          </p>
        ) : null}
        <p className="small">
          Session <code>{projection.session_id}</code> — Epoch {projection.session_epoch}
        </p>
      </div>

      {panelError ? <div className="notice error">{panelError}</div> : null}

      {!compact ? <div className="grid-2">
        <Card label="Session ID">
          <code className="text-xs break-all">{projection.session_id}</code>
        </Card>
        <Card label="Current Epoch">{projection.session_epoch}</Card>
      </div> : null}

      {!compact && latestBadges.length > 0 ? (
        <div className="actions">
          {latestBadges.map(([label, artifactId]) => (
            <span key={`${label}-${artifactId}`} className="card">
              <strong>{label}:</strong> <code className="text-xs">{artifactId}</code>
            </span>
          ))}
        </div>
      ) : null}

      {!compact && sessionResources.length > 0 ? (
        <div className="stack">
          <h4>Resource Evidence</h4>
          {sessionResourceGroups.map(([groupKey, group]) => (
          <details key={groupKey} className="card" open={group.resources.length <= 8}>
            <summary>{group.title} ({group.resources.length})</summary>
            <div className="grid-2" style={{ marginTop: '0.5rem' }}>
            {group.resources.map((resource) => {
              const label = resource.label || resource.artifactId;
              const contentHref = resource.contentUrl
                ? resolveApiBaseTemplate(apiBase, resource.contentUrl)
                : buildArtifactDownloadHref(apiBase, resource.artifactId);
              const downloadHref = resource.downloadUrl
                ? resolveApiBaseTemplate(apiBase, resource.downloadUrl)
                : buildArtifactDownloadHref(apiBase, resource.artifactId);
              return (
                <div key={resource.resourceId || resource.artifactId} className="card">
                  <strong>{label}</strong>
                  <div className="small">{resource.completenessStatus === 'pending' ? 'Harvesting…' : resource.completenessStatus}</div>
                  <code className="text-xs break-all">{resource.artifactId}</code>
                  {resource.unavailableReason ? <p className="small notice warning">{resource.unavailableReason}</p> : null}
                  <div className="actions" style={{ marginTop: '0.5rem' }}>
                    {resource.previewAvailable ? <a aria-label={`Open ${label}`} className="button secondary small" href={contentHref} target="_blank" rel="noreferrer">
                      Open
                    </a> : null}
                    {resource.downloadAvailable ? <a aria-label={`Download ${label}`} className="button secondary small" href={downloadHref} target="_blank" rel="noreferrer">
                      Download
                    </a> : null}
                  </div>
                </div>
              );
            })}
            </div>
          </details>
          ))}
        </div>
      ) : null}

      {!compact ? <div className="stack">
        {projection.grouped_artifacts.map((group) => (
          <div key={group.group_key} className="card">
            <strong>{group.title}</strong>
            <div className="stack gap-1" style={{ marginTop: '0.5rem' }}>
              {group.artifacts.length === 0 ? (
                <span className="small">No artifacts.</span>
              ) : (
                group.artifacts.map((artifact) => (
                  <code key={artifact.artifactId} className="text-xs break-all">
                    {artifact.artifactId}
                  </code>
                ))
              )}
            </div>
          </div>
        ))}
      </div> : null}

      <div className="stack">
        {optimisticMessages.length > 0 ? (
          <div className="chat-session-message-list" aria-label="Pending session messages">
            {optimisticMessages.map((message) => (
              <div
                key={message.clientEventKey}
                className={`chat-session-message chat-session-message-${message.status}`}
                data-client-event-key={message.clientEventKey}
              >
                <div className="chat-session-message-meta">
                  Operator message · {message.status === 'pending' ? 'Sending' : 'Failed'}
                </div>
                <div className="chat-session-message-text">{message.message}</div>
                {message.error ? (
                  <div className="chat-session-message-error">{message.error}</div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
        <label htmlFor="session-follow-up">Follow-up message</label>
        <textarea
          id="session-follow-up"
          value={followUpMessage}
          onChange={(event) => setFollowUpMessage(event.target.value)}
          onKeyDown={handleFollowUpKeyDown}
          rows={3}
          placeholder="Send a follow-up turn to the managed Codex session."
          disabled={busy || !canSendFollowUp}
          aria-describedby={sendDisabledReason && (busy || !canSendFollowUp) ? 'session-follow-up-disabled-reason' : undefined}
        />
        {sendDisabledReason && (busy || !canSendFollowUp) ? (
          <p id="session-follow-up-disabled-reason" className="chat-session-disabled-reason">
            {sendDisabledReason}
          </p>
        ) : null}
        <div className="actions chat-session-control-actions">
          {renderControlButton({
            label: 'Continue session',
            className: 'secondary',
            disabledReason: busy || !followUpMessage.trim() || !canSendFollowUp ? sendDisabledReason : null,
            onClick: submitFollowUp,
            hiddenWhenUnavailable: true,
          })}
          {renderControlButton({
            label: 'Clear / Reset',
            className: 'secondary',
            disabledReason: clearDisabledReason,
            onClick: clearSession,
            hiddenWhenUnavailable: true,
          })}
          {renderControlButton({
            label: 'Interrupt turn',
            className: 'secondary',
            disabledReason: interruptDisabledReason,
            onClick: interruptTurn,
            hiddenWhenUnavailable: true,
          })}
          {renderControlButton({
            label: 'Cancel session',
            className: 'queue-action queue-action-danger',
            disabledReason: cancelDisabledReason,
            onClick: cancelSession,
            hiddenWhenUnavailable: true,
          })}
        </div>
      </div>
    </section>
  );
}

type MissingAgentRunState = 'waiting_for_launch' | 'binding_missing' | 'launch_failed';

function inferMissingAgentRunState(execution: z.infer<typeof ExecutionDetailSchema>): MissingAgentRunState {
  const lifecycleState = (execution.rawState || execution.state || execution.status || '').toLowerCase();
  const temporalStatus = (execution.temporalStatus || execution.closeStatus || '').toLowerCase();
  const hasProgress = Boolean(
    execution.startedAt ||
      (execution.updatedAt && execution.createdAt && execution.updatedAt !== execution.createdAt),
  );

  if (
    execution.closedAt ||
    TERMINAL_STATES.has(lifecycleState) ||
    TERMINAL_RUN_STATUSES.has(lifecycleState) ||
    TERMINAL_RUN_STATUSES.has(temporalStatus)
  ) {
    return 'launch_failed';
  }

  if (lifecycleState === 'executing' || lifecycleState === 'running') {
    return hasProgress ? 'binding_missing' : 'waiting_for_launch';
  }

  return 'waiting_for_launch';
}

function renderMissingAgentRunCopy(state: MissingAgentRunState): string {
  if (state === 'launch_failed') {
    return 'This execution ended before a managed runtime observability record was created.';
  }
  if (state === 'binding_missing') {
    return 'This execution is running but has not received its managed runtime binding yet.';
  }
  return 'Waiting for managed runtime launch to create live logs.';
}

function remediationArtifactType(artifact: z.infer<typeof ArtifactSummarySchema>): string | null {
  const metadataType = metadataString(artifact.metadata, 'artifact_type', 'artifactType');
  if (metadataType.startsWith('remediation.')) return metadataType;
  return artifact.links.find((link) => link.linkType.startsWith('remediation.'))?.linkType ?? null;
}

function remediationArtifactLabel(type: string): string {
  const labels: Record<string, string> = {
    'remediation.context': 'Context',
    'remediation.plan': 'Plan',
    'remediation.attempt': 'Attempt',
    'remediation.decision_log': 'Decision Log',
    'remediation.action_request': 'Action Request',
    'remediation.action_result': 'Action Result',
    'remediation.verification': 'Verification',
    'remediation.summary': 'Summary',
  };
  return labels[type] ?? type.replace(/^remediation\./, '').replaceAll('_', ' ');
}

function remediationListValue(items: string[] | null | undefined): string {
  return items && items.length > 0 ? items.join(', ') : '—';
}

function RemediationCheckpointBranches({
  branches,
}: {
  branches: z.infer<typeof RemediationLinkSchema>['checkpointBranches'];
}) {
  if (!branches || branches.length === 0) return null;
  return (
    <div className="td-remediation-live">
      <strong>Checkpoint branches</strong>
      <ul className="td-remediation-list">
        {branches.map((branch) => (
          <li key={`${branch.workflowId}:${branch.branchId}`} className="card">
            <a href={dependencyHref(branch.workflowId)}>
              <code className="text-xs break-all">{branch.branchId}</code>
            </a>
            <div className="grid-2">
              <Card label="Target Workflow"><code className="text-xs break-all">{branch.workflowId}</code></Card>
              <Card label="Turn">{branch.branchTurnId || '—'}</Card>
              <Card label="Checkpoint">{branch.checkpointRef || '—'}</Card>
              <Card label="Context">{branch.contextArtifactRef || '—'}</Card>
              <Card label="Head status">{branch.headStatus ? formatStatusLabel(branch.headStatus) : '—'}</Card>
              <Card label="Attempt / version">{branch.headAttemptOrdinal != null && branch.headVersion != null ? `${branch.headAttemptOrdinal} / ${branch.headVersion}` : '—'}</Card>
              <Card label="Root candidate"><code className="text-xs break-all">{branch.rootCheckpointRef || '—'}</code></Card>
              <Card label="Current candidate"><code className="text-xs break-all">{branch.headCheckpointRef || '—'}</code></Card>
              <Card label="Candidate digest"><code className="text-xs break-all">{branch.headWorkspaceDigest || '—'}</code></Card>
              <Card label="Latest verification">{branch.latestVerificationVerdict || '—'}</Card>
              <Card label="Next attempt baseline"><code className="text-xs break-all">{branch.nextActionBaseline ? `${branch.nextActionBaseline.checkpointRef} @ v${branch.nextActionBaseline.headVersion}` : '—'}</code></Card>
              <Card label="Remaining work"><code className="text-xs break-all">{branch.remainingWorkRef || '—'}</code></Card>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RemediationApprovalSummary({
  approval,
}: {
  approval: z.infer<typeof RemediationApprovalStateSchema>;
}) {
  const hasDetails = Boolean(
    approval.actionKind ||
      approval.riskTier ||
      approval.preconditions ||
      approval.blastRadius ||
      approval.auditRef ||
      approval.decision,
  );

  if (!hasDetails) return null;

  return (
    <div className="td-remediation-approval">
      <div className="grid-2">
        <Card label="Action">{approval.actionKind || '—'}</Card>
        <Card label="Risk">{approval.riskTier || '—'}</Card>
        <Card label="Decision">{approval.decision || 'not_required'}</Card>
        <Card label="Audit">{approval.auditRef || '—'}</Card>
        <Card label="Preconditions">{approval.preconditions || '—'}</Card>
        <Card label="Blast Radius">{approval.blastRadius || '—'}</Card>
      </div>
      {approval.requestId && !approval.canDecide && approval.decision === 'pending' ? (
        <p className="notice subtle">Approval is read-only for this operator.</p>
      ) : null}
    </div>
  );
}

function RemediationRelationshipsPanel({
  inbound,
  outbound,
  inboundError,
  outboundError,
  onApprovalDecision,
  approvalBusy,
  showEmpty,
}: {
  inbound: z.infer<typeof RemediationLinksSchema> | undefined;
  outbound: z.infer<typeof RemediationLinksSchema> | undefined;
  inboundError: Error | null;
  outboundError: Error | null;
  onApprovalDecision: (workflowId: string, requestId: string, decision: 'approved' | 'rejected') => void;
  approvalBusy: boolean;
  showEmpty: boolean;
}) {
  const inboundItems = inbound?.items ?? [];
  const outboundItems = outbound?.items ?? [];
  const hasData = inboundItems.length > 0 || outboundItems.length > 0;

  if (!hasData && !inboundError && !outboundError && !showEmpty) return null;

  return (
    <section className="stack td-remediation-region td-evidence-region">
      <div>
        <h3>Remediation</h3>
        <p className="small">Target and remediator relationships are shown from bounded remediation link metadata.</p>
      </div>
      {inboundError || outboundError ? (
        <div className="notice error">
          Remediation relationship data is degraded. Existing workflow detail remains available.
        </div>
      ) : null}
      {inboundItems.length > 0 ? (
        <div className="stack">
          <h4>Remediation Workflows</h4>
          <ul className="td-remediation-list">
            {inboundItems.map((item) => (
              <li key={item.remediationWorkflowId} className="card">
                <a href={dependencyHref(item.remediationWorkflowId)}>
                  <code className="text-xs break-all">{item.remediationWorkflowId}</code>
                </a>
                <div className="grid-2">
                  <Card label="Status">{formatStatusLabel(item.status)}</Card>
                  <Card label="Authority">{item.authorityMode || '—'}</Card>
                  <Card label="Latest Action">{item.latestActionSummary || '—'}</Card>
                  <Card label="Resolution">{item.resolution || '—'}</Card>
                  <Card label="Lock">{item.activeLockScope || 'None'}</Card>
                  {item.activeLockHolder && item.activeLockHolder !== item.remediationWorkflowId ? (
                    <Card label="Lock Holder">{item.activeLockHolder}</Card>
                  ) : null}
                  <Card label="Updated">{formatWhen(item.updatedAt)}</Card>
                </div>
                <RemediationCheckpointBranches branches={item.checkpointBranches} />
                {item.approvalState ? <RemediationApprovalSummary approval={item.approvalState} /> : null}
                {item.approvalState?.canDecide && item.approvalState.requestId ? (
                  <div className="actions">
                    <button
                      type="button"
                      className="secondary"
                      disabled={approvalBusy}
                      onClick={() => onApprovalDecision(item.remediationWorkflowId, item.approvalState!.requestId!, 'approved')}
                    >
                      Approve remediation action
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={approvalBusy}
                      onClick={() => onApprovalDecision(item.remediationWorkflowId, item.approvalState!.requestId!, 'rejected')}
                    >
                      Reject remediation action
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : showEmpty && !inboundError ? (
        <p className="notice subtle">No inbound remediation workflows linked yet.</p>
      ) : null}
      {outboundItems.length > 0 ? (
        <div className="stack">
          <h4>Remediation Target</h4>
          <ul className="td-remediation-list">
            {outboundItems.map((item) => (
              <li key={item.targetWorkflowId} className="card">
                <a href={dependencyHref(item.targetWorkflowId)}>
                  <code className="text-xs break-all">{item.targetWorkflowId}</code>
                </a>
                <div className="grid-2">
                  <Card label="Pinned Run"><code className="text-xs break-all">{item.targetRunId || '—'}</code></Card>
                  <Card label="Mode">{item.mode || '—'}</Card>
                  <Card label="Authority">{item.authorityMode || '—'}</Card>
                  <Card label="Status">{formatStatusLabel(item.status)}</Card>
                  <Card label="Evidence Bundle">{item.contextArtifactRef || 'Missing'}</Card>
                  <Card label="Approval">{item.approvalState?.decision || 'not_required'}</Card>
                  <Card label="Selected Steps">{remediationListValue(item.selectedSteps)}</Card>
                  <Card label="Current Target">{item.currentTargetState || '—'}</Card>
                  <Card label="Allowed Actions">{remediationListValue(item.allowedActions)}</Card>
                  <Card label="Lock">{item.activeLockScope || 'None'}</Card>
                  <Card label="Lock Holder">{item.activeLockHolder || item.lockOutcome?.holder || '—'}</Card>
                  <Card label="Lock Outcome">{item.lockOutcome?.state || '—'}</Card>
                </div>
                {item.evidenceDegraded ? (
                  <p className="notice subtle">
                    Evidence is degraded. Unavailable: {remediationListValue(item.unavailableEvidenceClasses)}.
                  </p>
                ) : null}
                {item.liveObservation ? (
                  <div className="td-remediation-live">
                    <strong>{item.liveObservation.label || 'Live observation'}</strong>
                    <div className="grid-2">
                      <Card label="Cursor">{item.liveObservation.sequenceCursor || '—'}</Card>
                      <Card label="Reconnect">{item.liveObservation.reconnectState || '—'}</Card>
                      <Card label="Epoch">{item.liveObservation.epoch || '—'}</Card>
                      <Card label="Live Status">{item.liveObservation.status || '—'}</Card>
                    </div>
                    {item.liveObservation.fallbackReason ? (
                      <p className="small">{item.liveObservation.fallbackReason}</p>
                    ) : null}
                  </div>
                ) : null}
                {!item.contextArtifactRef ? (
                  <p className="notice subtle">Evidence bundle is missing.</p>
                ) : null}
                <RemediationCheckpointBranches branches={item.checkpointBranches} />
                {item.mode?.includes('follow') && !item.contextArtifactRef ? (
                  <p className="notice subtle">
                    Live follow is unavailable; durable remediation artifacts remain authoritative.
                  </p>
                ) : null}
                {item.approvalState ? <RemediationApprovalSummary approval={item.approvalState} /> : null}
                {item.approvalState?.canDecide && item.approvalState.requestId ? (
                  <div className="actions">
                    <button
                      type="button"
                      className="secondary"
                      disabled={approvalBusy}
                      onClick={() => onApprovalDecision(item.remediationWorkflowId, item.approvalState!.requestId!, 'approved')}
                    >
                      Approve remediation action
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={approvalBusy}
                      onClick={() => onApprovalDecision(item.remediationWorkflowId, item.approvalState!.requestId!, 'rejected')}
                    >
                      Reject remediation action
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : showEmpty && !outboundError ? (
        <p className="notice subtle">No outbound remediation target linked yet.</p>
      ) : null}
    </section>
  );
}

function RemediationEvidencePanel({
  artifacts,
  apiBase,
  showEmpty,
}: {
  artifacts: z.infer<typeof ArtifactSummarySchema>[];
  apiBase: string;
  showEmpty: boolean;
}) {
  const remediationArtifacts = artifacts
    .map((artifact) => ({ artifact, type: remediationArtifactType(artifact) }))
    .filter((item): item is { artifact: z.infer<typeof ArtifactSummarySchema>; type: string } => Boolean(item.type));

  if (remediationArtifacts.length === 0) {
    if (!showEmpty) return null;
    return (
      <section className="stack td-remediation-region td-evidence-region">
        <h3>Remediation Evidence</h3>
        <p className="notice subtle">No remediation evidence artifacts linked yet.</p>
      </section>
    );
  }

  return (
    <section className="stack td-remediation-region td-evidence-region">
      <h3>Remediation Evidence</h3>
      <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
        <table>
          <thead>
            <tr>
              <th>Evidence</th>
              <th>Artifact</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {remediationArtifacts.map(({ artifact, type }) => (
              <tr key={artifact.artifactId}>
                <td>{remediationArtifactLabel(type)}</td>
                <td><code>{artifact.artifactId}</code></td>
                <td>
                  <a className="button secondary" href={artifactDownloadHref(apiBase, artifact)}>
                    Open Evidence
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type ArtifactCategory = 'files' | 'logs' | 'patches' | 'reports' | 'other';

function artifactBrowserCategory(artifact: z.infer<typeof ArtifactSummarySchema>): ArtifactCategory {
  const metadata = artifact.metadata || {};
  const searchText = [
    artifact.artifactId,
    artifact.contentType,
    metadataStrings(metadata, 'filename', 'name', 'label', 'artifact_type', 'artifactType', 'render_hint', 'renderHint'),
    artifact.links.map((link) => `${link.linkType} ${link.label || ''}`).join(' '),
  ]
    .join(' ')
    .toLowerCase();
  if (searchText.includes('report')) return 'reports';
  if (/\b(log|stdout|stderr|diagnostic|trace)\b/.test(searchText)) return 'logs';
  if (/\b(patch|diff|apply_output)\b/.test(searchText)) return 'patches';
  if (/\b(file|attachment|input|context|summary|checkpoint)\b/.test(searchText)) return 'files';
  return 'other';
}

function ArtifactBrowserPanel({
  artifacts,
  apiBase,
  isLoading,
  error,
}: {
  artifacts: z.infer<typeof ArtifactSummarySchema>[];
  apiBase: string;
  isLoading: boolean;
  error: Error | null;
}) {
  const grouped = artifacts.reduce<Record<ArtifactCategory, z.infer<typeof ArtifactSummarySchema>[]>>(
    (acc, artifact) => {
      acc[artifactBrowserCategory(artifact)].push(artifact);
      return acc;
    },
    { files: [], logs: [], patches: [], reports: [], other: [] },
  );
  const categoryRows = (Object.keys(grouped) as ArtifactCategory[]).filter(
    (category) => grouped[category].length > 0,
  );

  return (
    <section className="stack td-artifacts-region td-evidence-region">
      <div className="step-tl-section-header">
        <h3>Workflow Artifacts</h3>
        <span className="step-tl-count">
          Artifact Browser | {artifacts.length} artifact{artifacts.length === 1 ? '' : 's'}
        </span>
      </div>
      {isLoading ? (
        <p className="loading">Loading artifacts...</p>
      ) : error ? (
        <div className="notice error">{error.message}</div>
      ) : artifacts.length === 0 ? (
        <p className="small">No artifacts.</p>
      ) : (
        <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
          <table>
            <thead>
              <tr>
                <th>Group</th>
                <th>Artifact</th>
                <th>Size</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {categoryRows.flatMap((category) =>
                grouped[category].map((artifact) => (
                  <tr key={`${category}-${artifact.artifactId}`}>
                    <td>{formatStatusLabel(category)}</td>
                    <td>
                      <code>{metadataString(artifact.metadata, 'filename', 'name', 'label') || artifact.artifactId}</code>
                      {artifact.contentType ? <div className="small">{artifact.contentType}</div> : null}
                    </td>
                    <td>{artifact.sizeBytes ?? '—'}</td>
                    <td>{formatStatusLabel(artifact.status)}</td>
                    <td>
                      <div className="actions">
                        <a
                          className="button secondary"
                          href={artifactDownloadHref(apiBase, artifact)}
                          title="Download artifact"
                        >
                          Download
                        </a>
                        <a
                          className="button secondary"
                          href={reportOpenHref(apiBase, artifact)}
                          title="Open readable artifact content"
                        >
                          Open
                        </a>
                      </div>
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function InterventionMonitorPanel({
  execution,
}: {
  execution: z.infer<typeof ExecutionDetailSchema>;
}) {
  const state = execution.rawState || execution.state || execution.status;
  const requested = execution.attentionRequired || state === 'intervention_requested';
  const auditCount = execution.interventionAudit?.length ?? 0;
  if (!requested && auditCount === 0 && !execution.waitingReason) {
    return null;
  }
  return (
    <section className="stack td-intervention-monitor-region td-evidence-region">
      <h3>Intervention Monitor</h3>
      <div className="grid-2">
        <Card label="Request State">{requested ? 'Intervention requested' : 'No active request'}</Card>
        <Card label="Workflow State">{formatStatusLabel(state)}</Card>
        <Card label="Audit Entries">{String(auditCount)}</Card>
        <Card label="Waiting Reason">{execution.waitingReason || '—'}</Card>
      </div>
    </section>
  );
}

function AuditTrailPanel({
  execution,
  steps,
}: {
  execution: z.infer<typeof ExecutionDetailSchema>;
  steps: z.infer<typeof StepLedgerSnapshotSchema> | null | undefined;
}) {
  const rows: Array<{ stage: string; timestamp: string | null | undefined; detail: ReactNode }> = [
    { stage: 'Started', timestamp: execution.startedAt, detail: 'Execution created.' },
    {
      stage: 'Last update',
      timestamp: execution.updatedAt,
      detail: <>State: {formatStatusLabel(execution.state, '')}</>,
    },
  ];
  if (execution.waitingReason || execution.attentionRequired) {
    rows.push({
      stage: 'Waiting',
      timestamp: execution.updatedAt,
      detail: `${execution.waitingReason || 'Awaiting external input.'}${execution.attentionRequired ? ' Attention required.' : ''}`,
    });
  }
  for (const step of steps?.steps ?? []) {
    rows.push({
      stage: `Step ${step.order}`,
      timestamp: step.updatedAt || step.startedAt,
      detail: (
        <>
          {step.title}: {formatStatusLabel(step.status)}
        </>
      ),
    });
  }
  for (const entry of execution.interventionAudit || []) {
    rows.push({
      stage: 'Intervention',
      timestamp: entry.createdAt,
      detail: (
        <>
          {entry.summary} <code className="text-xs">{entry.transport}</code>
        </>
      ),
    });
  }
  if (execution.closedAt) {
    rows.push({
      stage: 'Closed',
      timestamp: execution.closedAt,
      detail: <>Close status: {formatStatusLabel(execution.closeStatus || execution.temporalStatus)}</>,
    });
  }

  return (
    <section className="stack td-timeline-region td-evidence-region">
      <h3>Timeline</h3>
      <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
        <table>
          <thead>
            <tr>
              <th>Stage</th>
              <th>Timestamp</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.stage}-${row.timestamp || ''}-${index}`}>
                <td>{row.stage}</td>
                <td>{formatWhen(row.timestamp)}</td>
                <td>{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const DEBUG_ICON = (
  <svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    focusable="false"
    width="14"
    height="14"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M9 9v-1a3 3 0 0 1 6 0v1" />
    <path d="M8 9h8a6 6 0 0 1 1 3v3a5 5 0 0 1 -10 0v-3a6 6 0 0 1 1 -3" />
    <path d="M3 13h4" />
    <path d="M17 13h4" />
    <path d="M12 20v-6" />
    <path d="M4 19l3.35 -2" />
    <path d="M20 19l-3.35 -2" />
    <path d="M4 7l3.75 2.4" />
    <path d="M20 7l-3.75 2.4" />
  </svg>
);

function WorkflowDetailSubrouteNav({
  workflowId,
  current,
  search,
  onNavigate,
  stepCount,
  artifactCount,
  runCount,
}: {
  workflowId: string;
  current: WorkflowDetailSubroute;
  search: URLSearchParams;
  onNavigate: (next: WorkflowDetailSubroute, href: string) => void;
  stepCount?: number | null;
  artifactCount?: number | null;
  runCount?: number | null;
}) {
  const items: Array<SegmentedNavItem<WorkflowDetailSubroute>> = [
    { value: 'chat', label: 'Chat', href: workflowDetailSubrouteHref(workflowId, 'chat', search) },
    { value: 'overview', label: 'Overview', href: workflowDetailSubrouteHref(workflowId, 'overview', search) },
    {
      value: 'execution',
      label: 'Execution',
      href: workflowDetailSubrouteHref(workflowId, 'execution', search),
      badge: stepCount ?? runCount ?? null,
    },
    {
      value: 'evidence',
      label: 'Evidence',
      href: workflowDetailSubrouteHref(workflowId, 'evidence', search),
      badge: artifactCount ?? null,
    },
    {
      // MM-964: Debug is a first-class-but-quiet diagnostic tab. It rides the
      // same segmented control (one click, scrolls with the other tabs on
      // narrow viewports) rather than hiding behind a single-item overflow menu.
      value: 'debug',
      label: 'Debug',
      href: workflowDetailSubrouteHref(workflowId, 'debug', search),
      icon: DEBUG_ICON,
      tone: 'quiet',
    },
  ];
  return (
    <SegmentedNav
      items={items}
      active={current}
      ariaLabel="Workflow detail sections"
      onNavigate={onNavigate}
    />
  );
}

function ExecutionHistoryPanel({
  execution,
}: {
  execution: z.infer<typeof ExecutionDetailSchema>;
}) {
  const currentStatus = execution.rawState || execution.state || execution.status;
  const currentRunId = execution.runId || execution.temporalRunId || '—';
  const rows = [
    {
      relationship: 'current',
      workflowId: execution.workflowId || execution.taskId || '—',
      runId: currentRunId,
      status: currentStatus,
      href: '',
    },
    ...(execution.relatedRuns || []).map((run) => ({
      relationship: run.relationship,
      workflowId: run.workflowId,
      runId: run.runId || '—',
      status: run.status || 'unknown',
      href: run.href,
    })),
  ];
  return (
    <section className="stack td-runs-region td-evidence-region" data-mm-issue="MM-772">
      <h3>Execution History</h3>
      <div className="grid-2">
        <Card label="Workflow ID">
          <code className="text-xs break-all">{execution.workflowId || execution.taskId || '—'}</code>
        </Card>
        <Card label="Current Run ID">
          <code className="text-xs break-all">{currentRunId}</code>
        </Card>
        <Card label="Workflow State">{formatStatusLabel(currentStatus)}</Card>
        <Card label="Related Runs">{String(execution.relatedRuns?.length ?? 0)}</Card>
      </div>
      <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
        <table>
          <thead>
            <tr>
              <th>Relation</th>
              <th>Workflow</th>
              <th>Run</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.relationship}-${row.workflowId}-${row.runId}-${index}`}>
                <td>{formatStatusLabel(row.relationship)}</td>
                <td>
                  {row.href ? (
                    <a href={row.href}><code>{row.workflowId}</code></a>
                  ) : (
                    <code>{row.workflowId}</code>
                  )}
                </td>
                <td><code>{row.runId}</code></td>
                <td>{formatStatusLabel(row.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RunComparisonPanel({
  execution,
}: {
  execution: z.infer<typeof ExecutionDetailSchema>;
}) {
  if (!execution.relatedRuns || execution.relatedRuns.length === 0) {
    return null;
  }
  const currentStatus = execution.rawState || execution.state || execution.status;
  return (
    <section className="stack td-comparison-region td-evidence-region">
      <h3>Run Comparison</h3>
      <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
        <table>
          <thead>
            <tr>
              <th>Relation</th>
              <th>Workflow</th>
              <th>Run</th>
              <th>Status</th>
              <th>Runtime</th>
              <th>Model</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>current</td>
              <td><code>{execution.workflowId || execution.taskId}</code></td>
              <td><code>{execution.runId || execution.temporalRunId || '—'}</code></td>
              <td>{formatStatusLabel(currentStatus)}</td>
              <td>{formatRuntimeLabel(execution.targetRuntime)}</td>
              <td>{execution.model || execution.resolvedModel || execution.requestedModel || '—'}</td>
            </tr>
            {execution.relatedRuns.map((run, index) => (
              <tr key={`${run.relationship}-${run.workflowId}-${run.runId || ''}-${index}`}>
                <td>{formatStatusLabel(run.relationship)}</td>
                <td><a href={run.href}><code>{run.workflowId}</code></a></td>
                <td><code>{run.runId || '—'}</code></td>
                <td>{formatStatusLabel(run.status || 'unknown')}</td>
                <td>{formatRuntimeLabel(run.targetRuntime)}</td>
                <td>{run.model || run.resolvedModel || run.requestedModel || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function WorkflowDetailPageContent({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const toast = useDashboardToast();
  const cfg = readDashboardConfig(payload);
  const agentRunRoutes = readAgentRunRouteTemplates(cfg);
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const actionsOn = Boolean(cfg?.features?.temporalDashboard?.actionsEnabled);
  const taskEditingOn = Boolean(
    cfg?.features?.temporalDashboard?.temporalWorkflowEditing ??
      cfg?.features?.temporalDashboard?.temporalTaskEditing,
  );
  const debugOn = Boolean(cfg?.features?.temporalDashboard?.debugFieldsEnabled);
  // MM-964: operators can hide the diagnostic Debug tab via a local preference.
  // The Debug tab is available by default regardless of server config; the
  // preference (default visible) lets an operator collapse it. The extra
  // server-gated Debug Metadata region additionally requires `debugOn`.
  const [debugFieldsPref, setDebugFieldsPref] = useState(
    () => readDashboardPreferences().debugFieldsVisible,
  );
  const debugVisible = debugFieldsPref;
  const logStreamingEnabled = cfg?.features?.logStreamingEnabled !== false;
  const structuredHistoryEnabled = shouldUseStructuredHistory(cfg);
  const currentPathname = window.location.pathname;
  const currentSearch = window.location.search;
  const taskId = decodeWorkflowIdFromPath(currentPathname);
  const encodedTaskId = taskId ? encodeURIComponent(taskId) : null;
  const search = useMemo(() => new URLSearchParams(currentSearch), [currentSearch]);
  const [detailSubroute, setDetailSubroute] = useWorkflowDetailSubroute(currentPathname);
  const sourceTemporal = search.get('source') === 'temporal';

  const [actionError, setActionError] = useState<string | null>(null);
  const [activeWorkflowDialog, setActiveWorkflowDialog] = useState<WorkflowDialogKind | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(() => {
    try {
      const message = window.sessionStorage.getItem(
        'moonmind.temporalTaskEditing.notice',
      );
      if (message) {
        window.sessionStorage.removeItem('moonmind.temporalTaskEditing.notice');
      }
      return message;
    } catch {
      return null;
    }
  });
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});
  const [chatOptimisticMessages, setChatOptimisticMessages] = useState<OptimisticChatSessionMessage[]>([]);
  const [instructionsExpanded, setInstructionsExpanded] = useState(false);
  const [remediationMode, setRemediationMode] = useState(DEFAULT_REMEDIATION_MODE);
  const [remediationAuthority, setRemediationAuthority] = useState(DEFAULT_REMEDIATION_AUTHORITY);
  const [remediationActionPolicy, setRemediationActionPolicy] = useState(
    DEFAULT_REMEDIATION_ACTION_POLICY,
  );
  const [selectedRecoveryStepId, setSelectedRecoveryStepId] = useState('');
  const [selectedBranchId, setSelectedBranchId] = useState('');
  const [latestBranchCompare, setLatestBranchCompare] = useState<z.infer<typeof CheckpointBranchCompareSchema> | null>(null);

  const detailQuery = useQuery(
    workflowDetailQueryOptions({
      apiBase: payload.apiBase,
      workflowId: taskId,
      sourceTemporal,
      detailPoll,
    }),
  );

  const execution = detailQuery.data;
  const isTerminalExecution = isExecutionTerminal(execution);
  const evidenceStaleTime = workflowEvidenceStaleTime({
    isTerminal: isTerminalExecution,
    detailPoll,
  });
  const workflowId = execution?.workflowId || execution?.taskId || taskId || '';
  const runId = execution?.temporalRunId || execution?.runId || '';
  const namespace = execution?.namespace || '';
  const summaryArtifactRef = execution?.summaryArtifactRef || execution?.summary_artifact_ref || '';
  const explicitAgentRunId = execution?.agentRunId || execution?.agent_run_id || '';
  const resolvedAgentRunId = explicitAgentRunId;
  const explicitBridgeSessionId =
    execution?.bridgeSessionId ||
    execution?.bridge_session_id ||
    execution?.omnigentBridgeSessionId ||
    execution?.omnigent_bridge_session_id ||
    '';
  const executionIdempotencyKey = execution?.idempotencyKey || execution?.idempotency_key || '';
  const shouldResolveBridgeSession = Boolean(
    execution &&
      detailSubroute === 'chat' &&
      !resolvedAgentRunId &&
      !explicitBridgeSessionId &&
      workflowId,
  );
  const bridgeResolutionQuery = useQuery({
    queryKey: ['omnigent-bridge-session-projection', workflowId, executionIdempotencyKey],
    queryFn: () =>
      resolveBridgeSessionProjection({
        apiBase: payload.apiBase,
        workflowId,
        idempotencyKey: executionIdempotencyKey,
      }),
    enabled: shouldResolveBridgeSession,
    staleTime: isTerminalExecution ? Infinity : detailPoll,
    retry: false,
  });
  const resolvedBridgeSessionId =
    explicitBridgeSessionId || bridgeResolutionQuery.data?.bridgeSessionId || '';
  const resolvedBridgeProjection: BridgeSessionProjection = (
    bridgeResolutionQuery.data?.bridgeSessionId === resolvedBridgeSessionId
      ? bridgeResolutionQuery.data
      : undefined
  ) ?? {
    bridgeSessionId: resolvedBridgeSessionId,
    status: execution?.status ?? undefined,
    capabilities: {},
  };
  const shouldFetchRemediationLinks = Boolean(execution && workflowId);
  const sessionTimelineEnabled = shouldEnableSessionTimelineViewer({
    config: cfg,
    targetRuntime: execution?.targetRuntime,
    agentRunId: resolvedAgentRunId,
  });
  const agentRunTrackingInitializedRef = useRef(false);
  const previousAgentRunIdRef = useRef(resolvedAgentRunId);
  const [showAgentRunAttachNotice, setShowAgentRunAttachNotice] = useState(false);

  useEffect(() => {
    if (!execution) {
      return undefined;
    }

    if (!agentRunTrackingInitializedRef.current) {
      agentRunTrackingInitializedRef.current = true;
      previousAgentRunIdRef.current = resolvedAgentRunId;
      setShowAgentRunAttachNotice(false);
      return undefined;
    }

    if (!resolvedAgentRunId) {
      previousAgentRunIdRef.current = '';
      setShowAgentRunAttachNotice(false);
      return;
    }

    if (previousAgentRunIdRef.current === resolvedAgentRunId) {
      return undefined;
    }

    if (!previousAgentRunIdRef.current) {
      previousAgentRunIdRef.current = resolvedAgentRunId;
      setShowAgentRunAttachNotice(true);
      const timeout = window.setTimeout(() => {
        setShowAgentRunAttachNotice(false);
      }, 250);
      return () => window.clearTimeout(timeout);
    }

    previousAgentRunIdRef.current = resolvedAgentRunId;
    setShowAgentRunAttachNotice(false);
    return undefined;
  }, [execution, resolvedAgentRunId]);

  const missingAgentRunState =
    execution && !resolvedAgentRunId && !resolvedBridgeSessionId && !bridgeResolutionQuery.isLoading
      ? inferMissingAgentRunState(execution)
      : null;

  const chatTabActive = detailSubroute === 'chat';
  const executionTabActive = detailSubroute === 'execution';
  const evidenceTabActive = detailSubroute === 'evidence';
  const stepsTabActive = executionTabActive;
  const artifactsTabActive = evidenceTabActive;
  const overviewTabActive = detailSubroute === 'overview';
  const runsTabActive = executionTabActive;
  const debugTabActive = detailSubroute === 'debug';
  const shouldFetchStepLedger = (stepsTabActive || artifactsTabActive) && Boolean(execution?.stepsHref);

  const stepsQuery = useQuery({
    queryKey: ['workflow-detail-steps', workflowId, execution?.stepsHref],
    queryFn: () => fetchStepLedger(String(execution?.stepsHref || '')),
    enabled: shouldFetchStepLedger,
    refetchInterval: shouldFetchStepLedger && !isTerminalExecution ? detailPoll : false,
    staleTime: evidenceStaleTime,
  });
  const branchesQuery = useQuery({
    queryKey: ['workflow-detail-checkpoint-branches', workflowId],
    queryFn: () => fetchCheckpointBranches(payload.apiBase, workflowId),
    enabled: stepsTabActive && Boolean(execution && workflowId),
    refetchInterval: stepsTabActive && !isTerminalExecution ? detailPoll : false,
    staleTime: evidenceStaleTime,
  });
  const branches = branchesQuery.data?.items ?? [];
  const selectedBranch = branches.find((branch) => branch.branchId === selectedBranchId) || branches[0] || null;
  useEffect(() => {
    const firstBranch = branches[0];
    if (firstBranch) {
      const exists = branches.some((branch) => branch.branchId === selectedBranchId);
      if (!selectedBranchId || !exists) {
        setSelectedBranchId(firstBranch.branchId);
      }
    } else if (selectedBranchId) {
      setSelectedBranchId('');
    }
  }, [branches, selectedBranchId]);
  const selectedBranchTurnsQuery = useQuery({
    queryKey: ['workflow-detail-checkpoint-branch-turns', workflowId, selectedBranch?.branchId],
    queryFn: () => fetchCheckpointBranchTurns(payload.apiBase, workflowId, String(selectedBranch?.branchId || '')),
    enabled: stepsTabActive && Boolean(workflowId && selectedBranch?.branchId),
    refetchInterval: stepsTabActive && !isTerminalExecution ? detailPoll : false,
    staleTime: evidenceStaleTime,
  });
  const latestRunId = stepsQuery.data?.runId || runId;
  const artifactRunId = execution?.stepsHref ? stepsQuery.data?.runId : runId;
  const selectedRecoveryOptions = useMemo(() => {
    const failedStepId = execution?.resume?.failedStepId || '';
    const rows = stepsQuery.data?.steps || [];
    if (!failedStepId || rows.length === 0) {
      return [];
    }
    const failedRow = rows.find((row) => row.logicalStepId === failedStepId);
    const failedOrder = failedRow?.order ?? Number.POSITIVE_INFINITY;
    return rows.map((row) => {
      const preservation = row.recoveryPreservation;
      const isFailedStep = row.logicalStepId === failedStepId;
      const eligible = isFailedStep || Boolean(preservation?.eligible);
      let reason = '';
      if (!eligible) {
        if (row.order > failedOrder) {
          reason = 'after failed step';
        } else if (preservation?.message) {
          reason = preservation.message;
        } else if (preservation?.reason) {
          reason = formatStatusLabel(preservation.reason);
        } else {
          reason = 'checkpoint evidence missing';
        }
      }
      return {
        logicalStepId: row.logicalStepId,
        title: row.title,
        executionOrdinal: row.executionOrdinal,
        eligible,
        reason,
        isFailedStep,
      };
    });
  }, [execution?.resume?.failedStepId, stepsQuery.data?.steps]);
  const selectedRecoveryStep =
    selectedRecoveryOptions.find((option) => option.logicalStepId === selectedRecoveryStepId) ||
    selectedRecoveryOptions.find((option) => option.isFailedStep) ||
    selectedRecoveryOptions.find((option) => option.eligible) ||
    null;

  const stepRecoveryQuery = useQuery({
    queryKey: [
      'workflow-detail-step-execution',
      workflowId,
      selectedRecoveryStep?.logicalStepId,
      selectedRecoveryStep?.executionOrdinal,
      sourceTemporal,
    ],
    queryFn: async () => {
      if (!workflowId || !selectedRecoveryStep?.logicalStepId || !selectedRecoveryStep.executionOrdinal) {
        throw new Error('Step execution recovery evidence requires a selected step.');
      }
      const suffix = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/steps/${encodeURIComponent(
          selectedRecoveryStep.logicalStepId,
        )}/step-executions/${encodeURIComponent(String(selectedRecoveryStep.executionOrdinal))}${suffix}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        const statusText = response.statusText.trim();
        const detail = statusText ? ` ${statusText}` : '';
        throw new Error(`Step execution: ${response.status}${detail}`);
      }
      return StepExecutionDetailSchema.parse(await response.json());
    },
    enabled: Boolean(
      stepsTabActive &&
        workflowId &&
        selectedRecoveryStep?.logicalStepId &&
        selectedRecoveryStep.executionOrdinal > 0,
    ),
    refetchInterval: stepsTabActive && !isTerminalExecution ? detailPoll : false,
    staleTime: evidenceStaleTime,
  });
  const recoveryEligibility =
    execution?.recoveryEligibility ?? stepRecoveryQuery.data?.recoveryEligibility ?? null;

  const artifactsQuery = useQuery({
    queryKey: ['workflow-detail-artifacts', namespace, workflowId, artifactRunId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(artifactRunId || '')}/artifacts`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Artifacts: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled:
      artifactsTabActive &&
      Boolean(namespace && workflowId && artifactRunId),
    refetchInterval: namespace && workflowId && artifactRunId && !isTerminalExecution
      ? detailPoll
      : false,
    staleTime: evidenceStaleTime,
  });

  const latestReportQuery = useQuery({
    queryKey: ['workflow-detail-latest-report', namespace, workflowId, artifactRunId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(artifactRunId || '')}/artifacts?link_type=report.primary&latest_only=true`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Report: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled:
      artifactsTabActive &&
      Boolean(namespace && workflowId && artifactRunId),
    refetchInterval: namespace && workflowId && artifactRunId && !isTerminalExecution
      ? detailPoll
      : false,
    staleTime: evidenceStaleTime,
  });

  const runSummaryQuery = useQuery({
    queryKey: ['workflow-detail-run-summary', summaryArtifactRef],
    queryFn: () => fetchRunSummaryArtifact(payload.apiBase, summaryArtifactRef),
    enabled: overviewTabActive && Boolean(summaryArtifactRef),
    refetchInterval: overviewTabActive && summaryArtifactRef && !isTerminalExecution ? detailPoll : false,
    staleTime: evidenceStaleTime,
  });
  const inboundRemediationsQuery = useQuery({
    queryKey: ['workflow-detail-remediations', workflowId, 'inbound'],
    queryFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/remediations?direction=inbound`,
      );
      if (!response.ok) throw new Error(`Remediations: ${response.statusText}`);
      return RemediationLinksSchema.parse(await response.json());
    },
    enabled: artifactsTabActive && shouldFetchRemediationLinks,
    staleTime: evidenceStaleTime,
  });
  const outboundRemediationsQuery = useQuery({
    queryKey: ['workflow-detail-remediations', workflowId, 'outbound'],
    queryFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/remediations?direction=outbound`,
      );
      if (!response.ok) throw new Error(`Remediations: ${response.statusText}`);
      return RemediationLinksSchema.parse(await response.json());
    },
    enabled: artifactsTabActive && shouldFetchRemediationLinks,
    staleTime: evidenceStaleTime,
  });
  const executionRunSummary = RunSummaryArtifactSchema.safeParse(
    execution?.finishSummary,
  );
  const runSummary =
    runSummaryQuery.data ||
    (executionRunSummary.success ? executionRunSummary.data : null);
  const displayedMergeAutomation =
    execution?.mergeAutomation || runSummary?.mergeAutomation || null;
  const displayedSummary = runSummary?.operatorSummary || execution?.summary || '—';
  const proposalSummary = runSummary?.proposals || execution?.proposalSummary || null;
  const proposalOutcomes = execution?.proposalOutcomes || [];
  const prUrl =
    normalizeGitHubPullRequestUrl(execution?.prUrl) ||
    normalizeGitHubPullRequestUrl(runSummary?.publishContext?.pullRequestUrl);
  const dependencyOutcomesById = useMemo(() => {
    const entries = (execution?.dependencyOutcomes || []).map((item) => [item.workflowId, item] as const);
    return new Map(entries);
  }, [execution?.dependencyOutcomes]);
  const prerequisiteRows = useMemo(() => {
    const ids = execution?.dependsOn || [];
    if (!execution) {
      return [];
    }
    const uniqueByWorkflowId = <T extends { workflowId: string }>(items: T[]): T[] => {
      const seen = new Set<string>();
      return items.filter((item) => {
        if (seen.has(item.workflowId)) return false;
        seen.add(item.workflowId);
        return true;
      });
    };
    if (execution.prerequisites.length > 0) {
      return uniqueByWorkflowId(execution.prerequisites);
    }
    return uniqueByWorkflowId(ids.map((workflowId) => ({
      workflowId,
      title: workflowId,
      summary: null,
      state: null,
      closeStatus: null,
      workflowType: 'MoonMind.UserWorkflow',
    })));
  }, [execution]);
  const dependentRows = useMemo(() => {
    const seen = new Set<string>();
    return (execution?.dependents || []).filter((item) => {
      if (seen.has(item.workflowId)) return false;
      seen.add(item.workflowId);
      return true;
    });
  }, [execution?.dependents]);
  const hasDependencySection = Boolean(
    execution &&
      (execution.hasDependencies ||
        execution.dependsOn.length > 0 ||
        execution.prerequisites.length > 0 ||
        execution.dependents.length > 0),
  );
  const hasStepsEndpoint = Boolean(execution?.stepsHref);
  const showExecutionObservationFallback =
    !hasStepsEndpoint || (!stepsQuery.isLoading && (stepsQuery.isError || !stepsQuery.data));
  const branchesByStep = useMemo(() => buildBranchGroups(branches), [branches]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-steps', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-artifacts', namespace, workflowId, artifactRunId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-latest-report', namespace, workflowId, artifactRunId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-run-summary', summaryArtifactRef] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-remediations', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-checkpoint-branches', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-detail-checkpoint-branch-turns', workflowId] });
  };

  const updateMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const signalMutation = useMutation({
    mutationFn: async ({
      signalName,
      payload: signalPayload,
    }: {
      signalName: string;
      payload?: Record<string, unknown>;
    }) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          signalName,
          payload: signalPayload ?? {},
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const cancelMutation = useMutation({
    mutationFn: async ({
      action = 'cancel',
      graceful = true,
    }: {
      action?: 'cancel' | 'reject';
      graceful?: boolean;
    }) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          action,
          graceful,
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const failedStepResumeMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/recover-from-failed-step`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({
            idempotencyKey: `resume-${workflowId}-${latestRunId || runId || 'latest'}`,
            ...(execution?.resume?.checkpointRef
              ? { recoveryCheckpointRef: execution.resume.checkpointRef }
              : {}),
            operatorMetadata: { requestedFrom: 'workflow-detail' },
          }),
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: () => {
      setActionNotice('Resumed from failed step.');
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const retryPublicationMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/retry-publication`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json' },
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: () => {
      setActionNotice('Publication-only recovery started.');
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const selectedStepRecoveryMutation = useMutation({
    mutationFn: async () => {
      const selectedStepId = selectedRecoveryStep?.logicalStepId || '';
      const sourceRunId = execution?.resume?.sourceRunId || latestRunId || runId || '';
      if (!selectedStepId || !sourceRunId) {
        throw new Error('Selected-step recovery requires a source run and start step.');
      }
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/recover-from-selected-step`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({
            idempotencyKey: `selected-step-recovery-${workflowId}-${sourceRunId}-${selectedStepId}`,
            sourceWorkflowId: workflowId,
            sourceRunId,
            selectedStartStepId: selectedStepId,
            ...(execution?.resume?.checkpointRef
              ? { recoveryCheckpointRef: execution.resume.checkpointRef }
              : {}),
            operatorMetadata: { requestedFrom: 'workflow-detail', mode: 'selected-step' },
          }),
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: () => {
      setActionNotice('Recovery started from selected step.');
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const createRemediationMutation = useMutation({
    mutationFn: async () => {
      if (!execution) {
        throw new Error('Workflow detail is required before remediation can be drafted.');
      }
      const draft = buildRemediationCreateDraft(execution, {
        mode: remediationMode,
        authorityMode: remediationAuthority,
        actionPolicyRef: remediationActionPolicy,
        runId: latestRunId || runId,
      });
      const draftId = storeRemediationCreateDraft(draft);
      navigateToDashboardRoute(remediationCreateDraftHref(draftId));
      return { draft };
    },
    onSuccess: () => {
      setActionNotice('Remediation draft opened in Create.');
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const remediationApprovalMutation = useMutation({
    mutationFn: async ({
      remediationWorkflowId,
      requestId,
      decision,
    }: {
      remediationWorkflowId: string;
      requestId: string;
      decision: 'approved' | 'rejected';
    }) => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(remediationWorkflowId)}/remediation/approvals/${encodeURIComponent(requestId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ decision }),
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: () => {
      setActionNotice('Remediation approval decision recorded.');
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const checkpointBranchMutation = useMutation({
    mutationFn: async (request: BranchMutationRequest) => {
      const branchBase = `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/checkpoint-branches`;
      if (request.kind === 'compare') {
        const response = await fetch(
          `${branchBase}/${encodeURIComponent(request.branch.branchId)}/compare?against=${encodeURIComponent(request.againstBranchId)}`,
          { credentials: 'include' },
        );
        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || response.statusText);
        }
        return { kind: request.kind, body: CheckpointBranchCompareSchema.parse(await response.json()) };
      }

      let url = branchBase;
      let body: Record<string, unknown>;
      if (request.kind === 'create') {
        const budget = Number.parseFloat(request.draft.maxBudgetUsd);
        body = {
          source: {
            workflowId,
            runId: latestRunId || runId || request.source.refs.childRunId || 'latest',
            logicalStepId: request.source.logicalStepId,
            executionOrdinal: request.source.executionOrdinal || null,
            checkpointBoundary: 'after_execution',
            checkpointRef: stepCheckpointRef(request.source),
            checkpointDigest: null,
          },
          label: request.draft.label.trim(),
          instructions: { text: request.draft.instructions.trim() },
          workspacePolicy: request.draft.workspacePolicy,
          runtimeContextPolicy: request.draft.runtimeContextPolicy,
          publishMode: request.draft.publishMode,
          idempotencyKey: request.idempotencyKey,
          gitWorkBranch: request.draft.gitWorkBranch.trim() || null,
          maxBudgetUsd: Number.isFinite(budget) ? budget : null,
        };
      } else if (request.kind === 'continue') {
        url = `${branchBase}/${encodeURIComponent(request.branch.branchId)}/continue`;
        body = {
          label: request.branch.label,
          instructions: { text: request.instructions.trim() || 'Continue this checkpoint branch.' },
          workspacePolicy: 'continue_from_previous_execution',
          runtimeContextPolicy: 'reuse_session_new_epoch',
          idempotencyKey: request.idempotencyKey,
          maxBudgetUsd: null,
        };
      } else if (request.kind === 'fork') {
        url = `${branchBase}/${encodeURIComponent(request.branch.branchId)}/fork`;
        body = {
          label: `Fork of ${request.branch.label}`,
          instructions: { text: request.instructions.trim() || 'Fork this checkpoint branch.' },
          workspacePolicy: 'apply_previous_execution_diff_to_clean_baseline',
          runtimeContextPolicy: 'fresh_agent_run',
          idempotencyKey: request.idempotencyKey,
          maxBudgetUsd: null,
        };
      } else if (request.kind === 'promote') {
        url = `${branchBase}/${encodeURIComponent(request.branch.branchId)}/promote`;
        body = {
          expectedHeadStepExecutionId: request.branch.currentHeadStepExecutionId,
          expectedHeadCommit: request.branch.currentHeadCommit || null,
          acceptedOutputRefs: request.branch.artifactRefs || {},
          gateEvidence: {
            verdict: branchPromotionGateVerdict(request.branch),
            checkpointRef: request.branch.currentHeadCheckpointRef || request.branch.sourceCheckpointRef,
          },
          sideEffectDisposition: {
            status: branchPromotionSideEffectStatus(request.branch),
            publishStatus: request.branch.publishStatus || 'unpublished',
            pullRequestUrl: request.branch.pullRequestUrl || null,
          },
          downstreamInvalidation: {
            competingBranchIds: request.competingBranches.map((branch) => branch.branchId),
          },
          approvalEvidence: null,
          policyEvidence: { requestedFrom: 'workflow-detail', freshHeadValidated: true },
          policyRequiresApproval: false,
          idempotencyKey: request.idempotencyKey,
        };
      } else if (request.kind === 'publish') {
        url = `${branchBase}/${encodeURIComponent(request.branch.branchId)}/publish`;
        body = {
          mode: 'pull_request',
          repository: request.branch.gitRepository,
          baseBranch: request.branch.gitBaseBranch,
          headBranch: request.branch.gitWorkBranch,
          provider: 'github',
          idempotencyKey: request.idempotencyKey,
        };
      } else {
        url = `${branchBase}/${encodeURIComponent(request.branch.branchId)}/archive`;
        body = {
          reason: 'Archived from workflow detail Branch Explorer.',
          idempotencyKey: request.idempotencyKey,
        };
      }

      const response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return { kind: request.kind, body: await response.json() };
    },
    onSuccess: (result) => {
      if (result.kind === 'compare') {
        setLatestBranchCompare(result.body as z.infer<typeof CheckpointBranchCompareSchema>);
        setActionNotice('Branch comparison evidence refreshed.');
      } else {
        setLatestBranchCompare(null);
        setActionNotice(`Checkpoint branch ${result.kind} submitted.`);
      }
      invalidate();
    },
    onError: (error: Error) => setActionError(error.message),
  });

  const onRename = () => {
    setActionError(null);
    setActiveWorkflowDialog('rename');
  };

  const onPause = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Pause', payload: {} });
  };

  const onResume = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Resume', payload: {} });
  };

  const onResumeFromFailedStep = () => {
    setActionError(null);
    failedStepResumeMutation.mutate();
  };

  const onRecoverFromSelectedStep = () => {
    setActionError(null);
    selectedStepRecoveryMutation.mutate();
  };

  const onApprove = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Approve', payload: {} });
  };

  const onBypassDependencies = () => {
    setActionError(null);
    setActionNotice(null);
    signalMutation.mutate(
      {
        signalName: 'BypassDependencies',
        payload: { reason: 'Dependency wait bypassed by operator from the dashboard.' },
      },
      {
        onSuccess: () => {
          setActionNotice('Dependency wait bypass was requested.');
        },
      },
    );
  };

  const onCancel = () => {
    setActionError(null);
    cancelMutation.mutate({ action: 'cancel', graceful: true });
  };

  const onForceCancel = () => {
    setActionError(null);
    cancelMutation.mutate({ action: 'cancel', graceful: false });
  };

  const onReject = () => {
    setActionError(null);
    cancelMutation.mutate({ action: 'reject', graceful: true });
  };

  const actions = execution?.actions;
  const busy =
    updateMutation.isPending ||
    signalMutation.isPending ||
    cancelMutation.isPending ||
    failedStepResumeMutation.isPending ||
    retryPublicationMutation.isPending ||
    selectedStepRecoveryMutation.isPending ||
    createRemediationMutation.isPending ||
    remediationApprovalMutation.isPending ||
    checkpointBranchMutation.isPending;
  const editHref = workflowId
    ? actions?.canEditForRerun
      ? taskEditForRerunHref(workflowId)
      : taskEditHref(workflowId)
    : '';
  const compareHref =
    workflowId && actions?.canEditForRerun ? taskCompareHref(workflowId) : '';
  const detailHref =
    workflowId && typeof window !== 'undefined'
      ? `${window.location.pathname}${window.location.search}`
      : '/workflows';
  const workflowSubject = execution?.title?.trim() || taskId || workflowId || 'Workflow';
  const onRerun = () => {
    setActionError(null);
    if (busy || !workflowId) return;
    recordTemporalTaskEditingClientEvent({
      event: 'detail_rerun_click',
      mode: 'detail',
      workflowId,
    });
    updateMutation.mutate(
      { updateName: 'RequestRerun' },
      {
        onSuccess: (result) => {
          toast.success({
            title: 'Rerun requested',
            message: `${workflowSubject} has been queued.`,
            action: {
              label: 'View workflow',
              href: workflowActionResultHref(result, detailHref),
            },
          });
        },
      },
    );
  };
  const canCreateRemediation = Boolean(execution && isRemediationEligibleTarget(execution));
  // The remediation mode/authority/action-policy controls only render on the Artifacts
  // tab, so only expose the Remediate action there. Surfacing it on other tabs would let
  // the operator submit with default remediation settings without seeing those controls.
  const remediationActionAvailable = canCreateRemediation && artifactsTabActive;
  const canShowEditWorkflow = Boolean(actions?.canUpdateInputs || actions?.canEditForRerun);
  const editTaskUnavailableReason = canShowEditWorkflow
    ? null
    : actions?.disabledReasons?.canEditForRerun ||
      actions?.disabledReasons?.canUpdateInputs ||
      null;
  const rerunUnavailableReason = actions?.disabledReasons?.canRerun || null;
  const taskInstructions = execution?.taskInstructions?.trim() || '';
  const hasTaskInstructions = taskInstructions.length > 0;
  const runtimeCommand = runtimeCommandFromExecution(execution);
  const hasRuntimeCommand = Object.keys(runtimeCommand).length > 0;
  const shouldShowRuntimeCommand =
    hasRuntimeCommand ||
    taskInstructions.startsWith('/') ||
    taskInstructions.startsWith('\\/');
  const hasInterventionSection = Boolean(
    actions &&
      (
        actions.canPause ||
        actions.canResume ||
        actions.canApprove ||
        actions.canCancel ||
        actions.canReject ||
        actions.canSendMessage ||
        (execution?.interventionAudit?.length ?? 0) > 0
      ),
  );
  const actionDisabledReason = (key: string): string | null => {
    const reason = actions?.disabledReasons?.[key];
    return reason ? formatStatusLabel(reason) : null;
  };
  const workflowActionMenuItems: WorkflowActionMenuItem[] = buildWorkflowActionMenuItems({
    actionsOn,
    actions,
    busy,
    taskEditingOn,
    disabledReason: actionDisabledReason,
    editHref,
    compareHref,
    canShowEditWorkflow,
    editTaskDisabledReason: editTaskUnavailableReason
      ? formatStatusLabel(editTaskUnavailableReason)
      : null,
    rerunDisabledReason: rerunUnavailableReason ? formatStatusLabel(rerunUnavailableReason) : null,
    selectedRecoveryOptionCount: selectedRecoveryOptions.length,
    selectedRecoveryStepEligible: Boolean(selectedRecoveryStep?.eligible),
    selectedRecoveryStepDisabledReason: selectedRecoveryStep?.reason
      ? formatStatusLabel(selectedRecoveryStep.reason)
      : 'selected step is not eligible',
    canCreateRemediation: remediationActionAvailable,
    handlers: {
      onRename,
      onEditTask: () => {
        if (busy) return;
        recordTemporalTaskEditingClientEvent({
          event: 'detail_edit_click',
          mode: 'detail',
          workflowId,
        });
      },
      onCompareRun: () => {
        if (busy) return;
        recordTemporalTaskEditingClientEvent({
          event: 'detail_compare_click',
          mode: 'detail',
          workflowId,
        });
      },
      onRerun,
      onResumeFromFailedStep,
      onRecoverFromSelectedStep,
      onRetryPublication: () => {
        setActionError(null);
        retryPublicationMutation.mutate();
      },
      onPause,
      onResume,
      onApprove,
      onReject,
      onCancel,
      onForceCancel,
      onSendMessage: () => {
        setActionError(null);
        setActiveWorkflowDialog('send-message');
      },
      onBypassDependencies,
      onCreateRemediation: () => {
        setActionError(null);
        createRemediationMutation.mutate();
      },
    },
  });
  const toolbarMenuItems: WorkflowActionMenuItem[] = [
    {
      id: 'view-debug-details',
      label: debugFieldsPref ? 'View: Hide debug details' : 'View: Show debug details',
      onSelect: () => {
        const next = !debugFieldsPref;
        setDebugFieldsPref(next);
        updateDashboardPreferences({ debugFieldsVisible: next });
      },
    },
    ...workflowActionMenuItems,
  ];
  const toggleStep = (logicalStepId: string) => {
    setExpandedSteps((prev) => ({
      ...prev,
      [logicalStepId]: !prev[logicalStepId],
    }));
  };
  const workflowDialogSubject = execution?.title?.trim() || taskId || 'Workflow';
  const workflowDialogId = taskId || workflowId || '';
  const closeWorkflowDialog = () => {
    setActiveWorkflowDialog(null);
    setActionError(null);
  };
  const confirmWorkflowDialog = (value: string) => {
    const closeOnSuccess = { onSuccess: () => setActiveWorkflowDialog(null) };
    switch (activeWorkflowDialog) {
      case 'rename':
        updateMutation.mutate(
          { updateName: 'SetTitle', title: value },
          closeOnSuccess,
        );
        break;
      case 'send-message':
        signalMutation.mutate(
          { signalName: 'SendMessage', payload: { message: value } },
          closeOnSuccess,
        );
        break;
      default:
        break;
    }
  };
  const primaryReport = latestReportQuery.data?.artifacts[0] ?? null;
  const relatedReports = relatedReportArtifacts(artifactsQuery.data?.artifacts || []);
  const stepTabCount = stepsQuery.data?.steps.length ?? null;
  const artifactTabCount = artifactsQuery.data?.artifacts.length ?? null;
  const runTabCount = execution ? (execution.relatedRuns?.length ?? 0) + 1 : null;
  return (
    <div className="stack workflow-detail-page">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Workflow Detail</h2>
          <div className="toolbar-identity-row">
            <p className="page-meta">Workflow {taskId || '—'}</p>
            {execution ? (
              <WorkflowLifecycleStatusPill
                status={resolveWorkflowDisplayStatus(
                  execution.rawState,
                  execution.state,
                  execution.status,
                )}
              />
            ) : null}
          </div>
        </div>
        <div className="toolbar-controls">
          {taskId ? (
            <IconMenuButton
              items={toolbarMenuItems}
              ariaLabel="Workflow actions"
            />
          ) : null}
        </div>
      </div>

      {taskId ? (
        <div className="td-subroute-nav-row">
          <WorkflowDetailSubrouteNav
            workflowId={taskId}
            current={detailSubroute}
            search={search}
            onNavigate={setDetailSubroute}
            stepCount={stepTabCount}
            artifactCount={artifactTabCount}
            runCount={runTabCount}
          />
        </div>
      ) : null}

      {execution?.recurrence?.definitionId ? (
        <div className="page-meta">
          Created by schedule{' '}
          <a
            href={
              execution.recurrence?.href ||
              `/schedules/${encodeURIComponent(execution.recurrence?.definitionId || '')}`
            }
          >
            {execution.recurrence?.definitionId}
          </a>
        </div>
      ) : null}

      <DashboardActionDialog
        open={activeWorkflowDialog === 'rename'}
        title="Rename workflow"
        subject={workflowDialogSubject}
        compactId={workflowDialogId}
        consequence="Set a dashboard title for this workflow. Execution history, artifacts, and workflow identity stay unchanged."
        valueLabel="Workflow title"
        valueRequired
        initialValue={execution?.title || ''}
        confirmLabel={updateMutation.isPending ? 'Renaming' : 'Rename workflow'}
        confirmPending={updateMutation.isPending}
        disabledReason={actionDisabledReason('canSetTitle')}
        error={activeWorkflowDialog === 'rename' ? actionError : null}
        onCancel={closeWorkflowDialog}
        onConfirm={confirmWorkflowDialog}
      />
      <DashboardActionDialog
        open={activeWorkflowDialog === 'send-message'}
        title="Send operator message"
        subject={workflowDialogSubject}
        compactId={workflowDialogId}
        consequence="Send a message into the workflow's operator intervention channel."
        valueLabel="Message"
        valueRequired
        valueMultiline
        confirmLabel={signalMutation.isPending ? 'Sending' : 'Send message'}
        confirmPending={signalMutation.isPending}
        disabledReason={actionDisabledReason('canSendMessage')}
        error={activeWorkflowDialog === 'send-message' ? actionError : null}
        onCancel={closeWorkflowDialog}
        onConfirm={confirmWorkflowDialog}
      />

      {actionError ? <div className="notice error">{actionError}</div> : null}
      {actionNotice ? (
        <div className="notice" role="status">
          {actionNotice}
          <button
            type="button"
            className="secondary"
            onClick={() => setActionNotice(null)}
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {detailQuery.isLoading ? (
        <LoadingPlaceholder
          surface="workflow-detail"
          region="summary"
          variant="detail"
          density="detail-heavy"
          preserveContext
        />
      ) : detailQuery.isError ? (
        <div className="notice error">{(detailQuery.error as Error).message}</div>
      ) : execution ? (
        <>
          <div className="td-hero">
            <div className="td-hero-body">
              <div className="td-hero-headline">
                <h3 className="td-title-text">{execution.title}</h3>
                {workflowId ? (
                  <span className="meta-inline td-hero-workflow-id">
                    <code className="text-xs break-all">{workflowId}</code>
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                className="td-instructions-toggle"
                aria-expanded={instructionsExpanded}
                aria-controls="workflow-inputs-panel"
                onClick={() => setInstructionsExpanded((current) => !current)}
              >
                {instructionsExpanded ? 'Hide Workflow Inputs' : 'Show Workflow Inputs'}
              </button>
            </div>
            {instructionsExpanded ? (
              <div id="workflow-inputs-panel" className="td-instructions-panel">
                {hasTaskInstructions ? (
                  <pre>{taskInstructions}</pre>
                ) : (
                  <p className="small">Full workflow inputs are not available for this workflow.</p>
                )}
              </div>
            ) : null}
          </div>

          {chatTabActive ? (
            <section className="stack td-chat-region td-evidence-region" aria-label="Workflow chat">
              <div>
                <h3>Workflow Chat</h3>
                <p className="small">
                  Session transcript, live runtime events, and eligible operator controls for this workflow.
                </p>
              </div>
              {logStreamingEnabled ? (
                resolvedAgentRunId ? (
                  <>
                    {showAgentRunAttachNotice ? (
                      <p className="small">Waiting for managed runtime launch to create live logs.</p>
                    ) : null}
                    <LiveLogsPanel
                      apiBase={payload.apiBase}
                      agentRunId={resolvedAgentRunId}
                      isTerminal={isTerminalExecution}
                      autoExpand
                      disclosure={false}
                      routes={agentRunRoutes}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                      structuredHistoryEnabled={structuredHistoryEnabled}
                      optimisticMessages={chatOptimisticMessages}
                    />
                  </>
                ) : resolvedBridgeSessionId ? (
                  <BridgeSessionLogsPanel
                    apiBase={payload.apiBase}
                    bridgeSessionId={resolvedBridgeSessionId}
                    isTerminal={isTerminalExecution}
                    projection={resolvedBridgeProjection}
                    optimisticMessages={chatOptimisticMessages}
                    setOptimisticMessages={setChatOptimisticMessages}
                    actionsEnabled={actionsOn}
                  />
                ) : bridgeResolutionQuery.isLoading ? (
                  <p className="small">Checking bridge session evidence.</p>
                ) : (
                  <p className="small">{missingAgentRunState ? renderMissingAgentRunCopy(missingAgentRunState) : 'Waiting for managed runtime launch to create live logs.'}</p>
                )
              ) : (
                <p className="small">Live log streaming is disabled for this dashboard.</p>
              )}
              {resolvedAgentRunId && actionsOn ? (
                <SessionContinuityPanel
                  apiBase={payload.apiBase}
                  agentRunId={resolvedAgentRunId}
                  targetRuntime={execution.targetRuntime}
                  isTerminal={isTerminalExecution}
                  invalidateWorkflowDetail={invalidate}
                  routes={agentRunRoutes}
                  optimisticMessages={chatOptimisticMessages}
                  setOptimisticMessages={setChatOptimisticMessages}
                  compact
                />
              ) : null}
            </section>
          ) : null}

          {overviewTabActive && shouldShowRuntimeCommand ? (
            <RuntimeCommandDetail command={runtimeCommand} />
          ) : null}

          {runsTabActive ? (
            <>
              <ExecutionHistoryPanel execution={execution} />
              <RunComparisonPanel execution={execution} />
            </>
          ) : null}

          {overviewTabActive ? (
            <>
          <div className="td-summary-block">
            <h4>Summary</h4>
            <p className="whitespace-pre-wrap">{displayedSummary}</p>
            {runSummary?.finishOutcome?.reason && runSummary.finishOutcome.reason !== displayedSummary ? (
              <p className="small" style={{ marginTop: '0.4rem' }}>
                Outcome: {runSummary.finishOutcome.reason}
              </p>
            ) : null}
          </div>

          <MetricStrip
            items={[
              {
                label: 'Report',
                value: summaryArtifactRef ? 'Summary available' : 'No canonical report',
              },
              {
                label: 'Run scope',
                value: runTabCount && runTabCount > 1 ? `${runTabCount} runs` : 'Current run only',
              },
            ]}
          />
            </>
          ) : null}

          {overviewTabActive ? (
            <div className="td-facts-region">
              <SkillProvenanceBadge
                resolvedSkillsetRef={execution.resolvedSkillsetRef}
                taskSkills={execution.taskSkills}
                targetSkill={execution.targetSkill}
                skillRuntime={execution.skillRuntime}
              />

              <FactGroup title="Runtime">
                {execution.targetRuntime ? <Fact label="Runtime">{formatRuntimeLabel(execution.targetRuntime)}</Fact> : null}
                {execution.model ? (
                  <Fact label="Model">
                    <code className="text-xs">{execution.model}</code>
                  </Fact>
                ) : null}
                {execution.profileId ? (
                  <Fact label="Provider Profile">{renderProviderProfileSummary(execution)}</Fact>
                ) : null}
                {execution.effort ? <Fact label="Effort">{execution.effort}</Fact> : null}
                {execution.priority !== null && execution.priority !== undefined ? (
                  <Fact label="Priority">{execution.priority}</Fact>
                ) : null}
              </FactGroup>

              <FactGroup title="Git & Publish">
                {execution.repository ? (
                  <Fact label="Repo">
                    <RepositoryFact repository={execution.repository} />
                  </Fact>
                ) : null}
                {execution.publishMode ? (
                  <Fact label="Publish Mode">
                    {formatPublishModeLabel(execution.publishMode)}
                  </Fact>
                ) : null}
                {execution.startingBranch ? (
                  <Fact label="Starting Branch">
                    <code className="text-xs break-all">{execution.startingBranch}</code>
                  </Fact>
                ) : null}
                {execution.targetBranch ? (
                  <Fact label="Target Branch">
                    <code className="text-xs break-all">{execution.targetBranch}</code>
                  </Fact>
                ) : null}
                {execution.outputBranch ? (
                  <Fact label={execution.outputBranch.intent === 'terminal_checkpoint' ? 'Saved Work Branch' : 'Published Branch'}>
                    {execution.outputBranch.url ? (
                      <a className="text-xs break-all" href={execution.outputBranch.url} target="_blank" rel="noreferrer">
                        <code>{execution.outputBranch.name}</code>
                      </a>
                    ) : (
                      <code className="text-xs break-all">{execution.outputBranch.name}</code>
                    )}
                  </Fact>
                ) : null}
                {prUrl ? (
                  <Fact label="PR Link">
                    <a className="text-xs break-all" href={prUrl} target="_blank" rel="noreferrer">
                      {prUrl}
                    </a>
                  </Fact>
                ) : null}
              </FactGroup>

              <FactGroup title="Lifecycle">
                <Fact label="Created">{formatWhen(execution.createdAt)}</Fact>
                <Fact label="Started">{formatWhen(execution.startedAt)}</Fact>
                <Fact label="Duration">{workflowDuration(execution.startedAt, execution.closedAt)}</Fact>
                <Fact label="Updated">{formatWhen(execution.updatedAt)}</Fact>
                <Fact label="Closed">{formatWhen(execution.closedAt)}</Fact>
                {execution.scheduledFor ? <Fact label="Scheduled For">{formatWhen(execution.scheduledFor)}</Fact> : null}
                {execution.waitingReason ? <Fact label="Waiting Reason">{execution.waitingReason}</Fact> : null}
              </FactGroup>
            </div>
          ) : null}

          {debugTabActive ? (
            <section className="stack td-debug-region" aria-label="Workflow debug details">
              <div>
                <h3>Debug</h3>
                <p className="small">
                  {debugVisible
                    ? 'Raw Temporal and runtime identifiers for this workflow. These details are diagnostic only and do not affect the default overview.'
                    : 'Debug details are hidden by the current view preference.'}
                </p>
              </div>
              {debugVisible ? <div className="td-facts-region">
                <FactGroup title="Temporal">
                  <Fact label="Temporal Status">{formatStatusLabel(execution.temporalStatus)}</Fact>
                  <Fact label="Workflow State">{formatStatusLabel(execution.rawState || execution.state)}</Fact>
                  {execution.closeStatus ? <Fact label="Close Status">{formatStatusLabel(execution.closeStatus)}</Fact> : null}
                  <Fact label="Source">Temporal</Fact>
                  <Fact label="Workflow Type">{execution.workflowType || '—'}</Fact>
                  <Fact label="Entry">{execution.entry || '—'}</Fact>
                  <Fact label="Current Run ID">
                    <code className="text-xs break-all">{latestRunId || '—'}</code>
                  </Fact>
                  {resolvedAgentRunId ? (
                    <Fact label="Workflow Run">
                      <code className="text-xs break-all">{resolvedAgentRunId}</code>
                    </Fact>
                  ) : null}
                  <Fact label="Workflow ID">
                    <code className="text-xs break-all">{workflowId}</code>
                  </Fact>
                </FactGroup>
                {actions ? (
                  <FactGroup title="Action Capability Map">
                    <Fact label="Raw Actions">
                      <pre className="text-xs break-all">{JSON.stringify(actions, null, 2)}</pre>
                    </Fact>
                  </FactGroup>
                ) : null}
              </div> : null}
            </section>
          ) : null}

          {overviewTabActive && runSummary ? (
            <section className="stack td-run-summary-region td-evidence-region">
              <h3>Run Summary</h3>
              {runSummary.controlStop?.kind === 'workflow_gate' ? (
                <div className="stack" aria-label="Quality gate outcome">
                  <h4>Quality gate stopped this workflow</h4>
                  <p>
                    Every Step Execution completed as an operation, but the semantic quality gate
                    did not accept the candidate. No failed Step Execution was fabricated.
                  </p>
                  <FlatFactGrid>
                    <Fact label="Gate verdict">{runSummary.controlStop.verdict || '—'}</Fact>
                    <Fact label="Stop reason">{runSummary.controlStop.reasonCode || '—'}</Fact>
                    <Fact label="Publication feasible">
                      {runSummary.controlStop.publicationFeasible ? 'Yes' : 'No'}
                    </Fact>
                    <Fact label="Publication outcome">
                      {runSummary.controlStop.publicationAttempted ? 'Attempted' : 'Not attempted'}
                    </Fact>
                    <Fact label="Preserved candidate">
                      <code className="text-xs break-all">{runSummary.controlStop.workspaceHeadRef || '—'}</code>
                    </Fact>
                    <Fact label="Authoritative remaining work">
                      <code className="text-xs break-all">{runSummary.controlStop.remainingWorkRef || '—'}</code>
                    </Fact>
                  </FlatFactGrid>
                  <p className="small">
                    Edit for rerun and Full retry reuse the original task input. Continue remediation,
                    when admitted, consumes the preserved candidate and remaining-work evidence.
                    Publication retry only retries the publication handoff; it does not accept the work.
                  </p>
                </div>
              ) : null}
              <FlatFactGrid>
                {runSummary.finishOutcome ? (
                  <>
                    <Fact label="Outcome Code">{runSummary.finishOutcome.code || '—'}</Fact>
                    <Fact label="Outcome Stage">{runSummary.finishOutcome.stage || '—'}</Fact>
                  </>
                ) : null}
                {runSummary.publish ? (
                  <>
                    <Fact label="Publish Status">{formatStatusLabel(runSummary.publish.status)}</Fact>
                    <Fact label="Publish Mode">{formatPublishModeLabel(runSummary.publish.mode) || '—'}</Fact>
                  </>
                ) : null}
              </FlatFactGrid>
              {runSummary.publish?.reason ? (
                <p className="whitespace-pre-wrap">{runSummary.publish.reason}</p>
              ) : null}
              {runSummary.failure?.failureCode ? (
                <FlatFactGrid>
                  <Fact label="Failure Code">{runSummary.failure.failureCode}</Fact>
                  <Fact label="Queued Children">
                    {runSummary.failure.queuedChildCount ?? runSummary.failure.queuedChildren?.length ?? 0}
                  </Fact>
                </FlatFactGrid>
              ) : null}
              {runSummary.failure?.queuedChildren?.length ? (
                <FactGroup title="Queued Child Workflows">
                  {runSummary.failure.queuedChildren.map((child, index) => (
                    <Fact key={child.workflowId || child.executionId || `${child.ref || 'child'}-${index}`} label={child.ref || `Child ${index + 1}`}>
                      <code className="text-xs break-all">{child.workflowId || child.executionId || '—'}</code>
                    </Fact>
                  ))}
                </FactGroup>
              ) : null}
              {runSummary.publishContext ? (
                <FlatFactGrid>
                  {runSummary.publishContext.branch ? (
                    <Fact label="Publish Branch">
                      <code className="text-xs break-all">{runSummary.publishContext.branch}</code>
                    </Fact>
                  ) : null}
                  {runSummary.publishContext.baseRef ? (
                    <Fact label="Base Ref">
                      <code className="text-xs break-all">{runSummary.publishContext.baseRef}</code>
                    </Fact>
                  ) : null}
                  {runSummary.publishContext.commitCount !== undefined &&
                  runSummary.publishContext.commitCount !== null ? (
                    <Fact label="Commit Count">{String(runSummary.publishContext.commitCount)}</Fact>
                  ) : null}
                </FlatFactGrid>
              ) : null}
              {runSummary.publishContext?.boundedStoryLoop?.continuationDecision ? (() => {
                const continuation = runSummary.publishContext.boundedStoryLoop.continuationDecision;
                const progress = continuation.gate?.progressVector;
                const consumed = continuation.budget?.consumed ?? {};
                const attemptsUsed = consumed.attempts ?? 0;
                const attemptsMaximum = continuation.budget?.maxAttempts;
                const unresolvedGaps = progress?.gaps?.filter((gap) => !['passed', 'resolved', 'satisfied'].includes(gap.status)).length;
                return (
                  <section className="stack" aria-label="Remediation progress and budgets">
                    <h4>Remediation progress</h4>
                    <FlatFactGrid>
                      <Fact label="Classification">{formatStatusLabel(progress?.classification) || '—'}</Fact>
                      <Fact label="Gap trend">{progress ? `${unresolvedGaps ?? '—'} unresolved · score ${progress.unresolvedGapScore}${progress.priorUnresolvedGapScore === undefined || progress.priorUnresolvedGapScore === null ? '' : ` (${progress.unresolvedGapScore - progress.priorUnresolvedGapScore >= 0 ? '+' : ''}${progress.unresolvedGapScore - progress.priorUnresolvedGapScore})`}` : '—'}</Fact>
                      <Fact label="Required checks">
                        {progress ? `Passed ${progress.requiredChecks.passed ?? 0}${progress.priorRequiredChecks ? ` (${(progress.requiredChecks.passed ?? 0) - (progress.priorRequiredChecks.passed ?? 0) >= 0 ? '+' : ''}${(progress.requiredChecks.passed ?? 0) - (progress.priorRequiredChecks.passed ?? 0)})` : ''} · Failed ${progress.requiredChecks.failed ?? 0} · Not run ${progress.requiredChecks.not_run ?? 0}` : '—'}
                      </Fact>
                      <Fact label="Semantic no-progress cycles">{consumed.consecutiveNoProgressAttempts ?? 0}</Fact>
                      <Fact label="Hard attempts used / remaining">
                        {attemptsMaximum === undefined ? `${attemptsUsed} / —` : `${attemptsUsed} / ${Math.max(0, attemptsMaximum - attemptsUsed)}`}
                      </Fact>
                      <Fact label="Repeated failure signatures">{progress?.repeatedFailureSignatures?.length ?? 0}</Fact>
                      <Fact label="Resource budgets">
                        {`Provider ${consumed.provider ?? 0}/${continuation.budget?.providerBudget ?? '∞'} · Tokens ${consumed.tokens ?? 0}/${continuation.budget?.tokenBudget ?? '∞'} · Cost ${consumed.cost ?? 0}/${continuation.budget?.costBudget ?? '∞'} · Wall clock ${consumed.elapsedSeconds ?? 0}/${continuation.budget?.maxElapsedSeconds ?? '∞'}`}
                      </Fact>
                      <Fact label="Latest meaningful progress evidence">
                        {progress?.classification === 'meaningful_progress'
                          ? progress.newAuthoritativeEvidenceDigest || progress.relevantDiffDigest || 'Structured gap/check progress'
                          : '—'}
                      </Fact>
                      <Fact label="Exact stop dimension">{continuation.continueLoop ? 'None' : formatStatusLabel(continuation.reason)}</Fact>
                    </FlatFactGrid>
                    {progress?.regressions?.length ? (
                      <p className="small">Regressions: {progress.regressions.map((regression) => formatStatusLabel(regression)).join(', ')}</p>
                    ) : null}
                  </section>
                );
              })() : null}
              {runSummary.lastStep?.summary && runSummary.lastStep.summary !== displayedSummary ? (
                <div>
                  <strong>Last Step</strong>
                  <p className="whitespace-pre-wrap">{runSummary.lastStep.summary}</p>
                </div>
              ) : null}
              {runSummary.nextAction ? <p className="small">{runSummary.nextAction}</p> : null}
            </section>
          ) : null}

          {overviewTabActive && proposalSummary ? (
            <section className="stack td-run-summary-region td-evidence-region">
              <h3>Proposal Outcomes</h3>
              <MetricStrip
                items={[
                  { label: 'Requested', value: proposalSummary.requested ? 'Yes' : 'No' },
                  { label: 'Generated', value: String(proposalSummary.generatedCount ?? 0) },
                  { label: 'Submitted', value: String(proposalSummary.submittedCount ?? 0) },
                  { label: 'Delivered', value: String(proposalSummary.deliveredCount ?? 0) },
                  { label: 'Updated', value: String(proposalSummary.dedupUpdates?.length ?? 0) },
                  {
                    label: 'Failed',
                    value: String(
                      (proposalSummary.validationErrors?.length ?? 0) +
                        (proposalSummary.deliveryFailures?.length ?? 0),
                    ),
                  },
                ]}
              />
              {proposalSummary.externalLinks.length > 0 ? (
                <div className="stack">
                  <strong>External Review Links</strong>
                  {proposalSummary.externalLinks.map((item, index) => {
                    const externalUrl = typeof item.externalUrl === 'string' ? item.externalUrl : '';
                    const externalKey = typeof item.externalKey === 'string' ? item.externalKey : externalUrl || `Link ${index + 1}`;
                    const provider = typeof item.provider === 'string' ? item.provider : 'tracker';
                    return externalUrl ? (
                      <a key={`${provider}-${externalKey}-${index}`} href={externalUrl}>
                        {provider}: {externalKey}
                      </a>
                    ) : (
                      <span key={`${provider}-${externalKey}-${index}`} className="small">
                        {provider}: {externalKey}
                  </span>
                    );
                  })}
                </div>
              ) : null}
              {(proposalOutcomes?.length ?? 0) > 0 ? (
                <div className="stack">
                  <strong>Delivery Status</strong>
                  {proposalOutcomes?.map((item, index) => (
                    <ProposalDeliveryCard
                      key={`${String(item['provider'] || 'tracker')}-${String(item['externalKey'] || item['externalUrl'] || index)}`}
                      outcome={item}
                      index={index}
                    />
                  ))}
                </div>
              ) : null}
              {(proposalSummary.dedupUpdates?.length ?? 0) > 0 ? (
                <p className="small">{proposalSummary.dedupUpdates?.length ?? 0} dedup update(s)</p>
              ) : null}
              {(proposalSummary.validationErrors?.length ?? 0) > 0 ? (
                <div className="stack">
                  <strong>Validation Errors</strong>
                  {proposalSummary.validationErrors?.map((item, index) => {
                    const message = proposalErrorText(item);
                    return (
                      <p key={`validation-error-${index}`} className="small whitespace-pre-wrap">
                        {message || `${proposalSummary.validationErrors?.length ?? 0} validation error(s)`}
                      </p>
                    );
                  })}
                </div>
              ) : null}
              {(proposalSummary.deliveryFailures?.length ?? 0) > 0 ? (
                <div className="stack">
                  <strong>Delivery Failures</strong>
                  {proposalSummary.deliveryFailures?.map((item, index) => {
                    const provider = proposalFieldText(item, 'provider') || 'tracker';
                    const message = proposalErrorText(item);
                    return (
                      <p key={`delivery-failure-${index}`} className="small whitespace-pre-wrap">
                        {provider}: {message || 'delivery failed'}
                      </p>
                    );
                  })}
                </div>
              ) : null}
              {(proposalOutcomes?.length ?? 0) > 0 && !runSummary?.proposals ? (
                <p className="small">{proposalOutcomes?.length ?? 0} proposal outcome(s) available from execution detail.</p>
              ) : null}
            </section>
          ) : null}

          {overviewTabActive && displayedMergeAutomation ? (
            <MergeAutomationPanel mergeAutomation={displayedMergeAutomation} />
          ) : null}

          {stepsTabActive ? (
            hasStepsEndpoint ? (
            <section className="stack td-steps-region td-evidence-region">
              <div className="step-tl-section-header">
                <h3>Workflow Steps</h3>
                <span className="step-tl-header-meta">
                  <code className="text-xs">{latestRunId || '—'}</code>
                  {stepsQuery.data ? (
                    <span className="step-tl-count">
                      {stepsQuery.data.steps.length} step{stepsQuery.data.steps.length === 1 ? '' : 's'}
                    </span>
                  ) : null}
                </span>
              </div>
              {stepsQuery.isLoading ? (
                <LoadingPlaceholder
                  surface="workflow-detail"
                  region="steps"
                  variant="list"
                  density="compact"
                  preserveContext
                />
              ) : stepsQuery.isError ? (
                <div className="notice error">{(stepsQuery.error as Error).message}</div>
              ) : stepsQuery.data ? (
                <>
                  <StepTimingOverview rows={stepsQuery.data.steps} />
                  <div className="step-tl-list">
                    {stepsQuery.data.steps.map((row, idx) => (
                      <StepLedgerRowCard
                        key={row.logicalStepId}
                        apiBase={payload.apiBase}
                        logStreamingEnabled={logStreamingEnabled}
                        sessionTimelineEnabled={sessionTimelineEnabled}
                        structuredHistoryEnabled={structuredHistoryEnabled}
                        row={row}
                        workflowId={workflowId}
                        runId={latestRunId}
                        sourceTemporal={sourceTemporal}
                        historyPollInterval={!isTerminalExecution ? detailPoll : false}
                        evidenceStaleTime={evidenceStaleTime}
                        expanded={Boolean(expandedSteps[row.logicalStepId])}
                        onToggle={() => toggleStep(row.logicalStepId)}
                        isLast={idx === stepsQuery.data.steps.length - 1}
                        routes={agentRunRoutes}
                        branches={branchesByStep.get(stepBranchKey(row)) ?? []}
                      />
                    ))}
                  </div>
                  <BranchExplorerPanel
                    apiBase={payload.apiBase}
                    workflowId={workflowId}
                    runId={latestRunId || runId}
                    rows={stepsQuery.data.steps}
                    branches={branches}
                    selectedBranch={selectedBranch}
                    turns={selectedBranchTurnsQuery.data?.items ?? []}
                    isLoading={branchesQuery.isLoading}
                    error={branchesQuery.isError ? (branchesQuery.error as Error) : null}
                    turnsError={selectedBranchTurnsQuery.isError ? (selectedBranchTurnsQuery.error as Error) : null}
                    actionsEnabled={actionsOn}
                    busy={busy}
                    latestCompare={latestBranchCompare}
                    onSelectBranch={setSelectedBranchId}
                    onBranchAction={(request) => {
                      setActionError(null);
                      checkpointBranchMutation.mutate(request);
                    }}
                  />
                </>
              ) : (
                <p className="small">No step ledger available for this execution.</p>
              )}
            </section>
            ) : (
            <section className="stack td-steps-region td-evidence-region">
              <div className="step-tl-section-header">
                <h3>Workflow Steps</h3>
                <span className="step-tl-header-meta">
                  <code className="text-xs">{latestRunId || '—'}</code>
                </span>
              </div>
              <p className="small">No step ledger endpoint is available for this execution.</p>
            </section>
            )
          ) : null}

          {stepsTabActive ? <InterventionMonitorPanel execution={execution} /> : null}

          {stepsTabActive && execution.attentionRequired ? (
            <section className="notice">
              <strong>Attention required.</strong> This workflow is waiting for external input before it can continue.
            </section>
          ) : null}

          {overviewTabActive && hasDependencySection ? (
            <section className="stack">
              <div>
                <h3>Dependencies</h3>
                <p className="small">
                  Direct prerequisite runs gate this execution before planning or execution begins.
                </p>
              </div>
              <MetricStrip
                items={[
                  { label: 'Declared Prerequisites', value: String(prerequisiteRows.length) },
                  { label: 'Blocked On Dependencies', value: execution.blockedOnDependencies ? 'Yes' : 'No' },
                  { label: 'Dependency Resolution', value: formatDependencyResolution(execution.dependencyResolution) },
                  {
                    label: 'Dependency Wait Duration',
                    value: formatDurationMs(execution.dependencyWaitDurationMs ?? null),
                  },
                ]}
              />
              <FlatFactGrid>
                {execution.failedDependencyId ? (
                  <Fact label="Failed Dependency">
                    <code className="text-xs break-all">{execution.failedDependencyId}</code>
                  </Fact>
                ) : null}
              </FlatFactGrid>
              {execution.blockedOnDependencies ? (
                <div className="notice">
                  <strong>Blocked on prerequisites.</strong> This run keeps waiting until every prerequisite reaches <code>completed</code>. Failed, canceled, terminated, or timed-out prerequisites do not fail this run automatically — rerun the prerequisite, cancel this run, or bypass the dependency.
                </div>
              ) : null}
              <div className="stack">
                <h4>Prerequisites</h4>
                {prerequisiteRows.length === 0 ? (
                  <p className="small">No prerequisites declared.</p>
                ) : (
                  <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {prerequisiteRows.map((item) => {
                      const outcome = dependencyOutcomesById.get(item.workflowId);
                      const isWaitingForRerun =
                        outcome?.resolution === 'waiting_for_successful_rerun';
                      const stateLabel = isWaitingForRerun
                        ? 'waiting_for_successful_rerun'
                        : outcome?.terminalState || item.state || 'unknown';
                      const failureCount = outcome?.failureCount ?? null;
                      const lastFailedAt = outcome?.lastFailedAt ?? null;
                      return (
                        <li key={item.workflowId} className="card">
                          <div className="stack gap-1">
                            <a href={dependencyHref(item.workflowId)}>
                              <strong>{item.title || item.workflowId}</strong>
                            </a>
                            <code className="text-xs break-all">{item.workflowId}</code>
                            <ExecutionStatusPill status={stateLabel} />
                            {isWaitingForRerun ? (
                              <p className="small">
                                <strong>Prerequisite failed; waiting for successful rerun.</strong>{' '}
                                {failureCount && failureCount > 0
                                  ? `Failure count: ${failureCount}.`
                                  : null}
                                {lastFailedAt ? ` Last failed at ${lastFailedAt}.` : null}
                              </p>
                            ) : null}
                            {item.summary ? <p className="small">{item.summary}</p> : null}
                            {outcome?.message ? <p className="small">{outcome.message}</p> : null}
                            {outcome?.failureCategory ? (
                              <p className="small">Failure category: <code>{outcome.failureCategory}</code></p>
                            ) : null}
                            {(outcome?.closeStatus || item.closeStatus) ? (
                              <p className="small">Close status: {formatStatusLabel(outcome?.closeStatus || item.closeStatus)}</p>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              <div className="stack">
                <h4>Dependents</h4>
                {dependentRows.length === 0 ? (
                  <p className="small">No downstream dependents reference this run.</p>
                ) : (
                  <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {dependentRows.map((item) => (
                      <li key={item.workflowId} className="card">
                        <div className="stack gap-1">
                          <a href={dependencyHref(item.workflowId)}>
                            <strong>{item.title || item.workflowId}</strong>
                          </a>
                          <code className="text-xs break-all">{item.workflowId}</code>
                          <ExecutionStatusPill status={item.state || 'unknown'} />
                          {item.summary ? <p className="small">{item.summary}</p> : null}
                          {item.closeStatus ? <p className="small">Close status: {formatStatusLabel(item.closeStatus)}</p> : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}

          {artifactsTabActive ? (
            <>
              <ReportPresentationSection
                primaryReport={primaryReport}
                relatedArtifacts={relatedReports}
                apiBase={payload.apiBase}
              />

              <InputImagesSection
                artifacts={artifactsQuery.data?.artifacts || []}
                apiBase={payload.apiBase}
              />

              <RemediationEvidencePanel
                artifacts={artifactsQuery.data?.artifacts || []}
                apiBase={payload.apiBase}
                showEmpty={shouldFetchRemediationLinks && artifactsQuery.isSuccess}
              />

              <ArtifactBrowserPanel
                artifacts={artifactsQuery.data?.artifacts || []}
                apiBase={payload.apiBase}
                isLoading={artifactsQuery.isLoading}
                error={artifactsQuery.isError ? (artifactsQuery.error as Error) : null}
              />
            </>
          ) : null}

          {artifactsTabActive ? (
            <RemediationRelationshipsPanel
              inbound={inboundRemediationsQuery.data}
              outbound={outboundRemediationsQuery.data}
              inboundError={inboundRemediationsQuery.isError ? (inboundRemediationsQuery.error as Error) : null}
              outboundError={outboundRemediationsQuery.isError ? (outboundRemediationsQuery.error as Error) : null}
              approvalBusy={remediationApprovalMutation.isPending}
              showEmpty={shouldFetchRemediationLinks && (inboundRemediationsQuery.isSuccess || outboundRemediationsQuery.isSuccess)}
              onApprovalDecision={(remediationWorkflowId, requestId, decision) => {
                setActionError(null);
                remediationApprovalMutation.mutate({ remediationWorkflowId, requestId, decision });
              }}
            />
          ) : null}

          {actionsOn && actions && remediationActionAvailable ? (
            <section className="stack td-actions-region">
              <div className="stack td-remediation-create-preview">
                <h4>Remediation create preview</h4>
                <div className="grid-2">
                  <label>
                    Remediation mode
                    <select
                      value={remediationMode}
                      onChange={(event) => setRemediationMode(event.target.value)}
                    >
                      <option value="snapshot_then_follow">Snapshot then follow</option>
                      <option value="snapshot">Snapshot only</option>
                      <option value="live_follow">Live follow</option>
                    </select>
                  </label>
                  <label>
                    Remediation authority
                    <select
                      value={remediationAuthority}
                      onChange={(event) => setRemediationAuthority(event.target.value)}
                    >
                      <option value="approval_gated">Approval-gated admin remediation</option>
                      <option value="observe_only">Troubleshooting only</option>
                      <option value="admin_auto">Admin remediation</option>
                    </select>
                  </label>
                  <label>
                    Remediation action policy
                    <input
                      value={remediationActionPolicy}
                      onChange={(event) => setRemediationActionPolicy(event.target.value)}
                    />
                  </label>
                  <Card label="Pinned Run"><code className="text-xs break-all">{latestRunId || runId || '—'}</code></Card>
                </div>
                <p className="small">
                  Evidence preview: step ledger, diagnostics, and 2000 log lines.
                </p>
              </div>
            </section>
          ) : null}

          {stepsTabActive &&
          actionsOn &&
          actions &&
          taskEditingOn &&
          actions.canResumeFromFailedStep &&
          selectedRecoveryOptions.length > 0 ? (
            <section className="stack td-actions-region">
              <div className="stack">
                <label className="field-label" htmlFor="selected-recovery-step">
                  Recovery start step
                </label>
                <select
                  id="selected-recovery-step"
                  value={selectedRecoveryStep?.logicalStepId || ''}
                  disabled={busy}
                  onChange={(event) => setSelectedRecoveryStepId(event.target.value)}
                >
                  {selectedRecoveryOptions.map((option) => (
                    <option
                      key={option.logicalStepId}
                      value={option.logicalStepId}
                      disabled={!option.eligible}
                    >
                      {option.title || option.logicalStepId}
                      {option.isFailedStep ? ' (failed step)' : ''}
                      {!option.eligible && option.reason ? ` - ${option.reason}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            </section>
          ) : null}

          {stepsTabActive && actionsOn && actions && hasInterventionSection ? (
            <InterventionPanel
              audit={execution.interventionAudit || []}
            />
          ) : null}

          {stepsTabActive ? (
            <>
              <TargetDiagnosticsPanel diagnostics={execution.targetDiagnostics} />
              <RecoveryEvidencePanel
                recovery={recoveryEligibility}
                resume={execution.resume}
                diagnostics={execution.targetDiagnostics}
                onResumeFromFailedStep={onResumeFromFailedStep}
                onRerun={onRerun}
                busy={busy}
                taskEditingOn={taskEditingOn}
              />
            </>
          ) : null}

          {stepsTabActive && actionsOn && resolvedAgentRunId ? (
            <SessionContinuityPanel
              apiBase={payload.apiBase}
              agentRunId={resolvedAgentRunId}
              targetRuntime={execution.targetRuntime}
              isTerminal={isTerminalExecution}
              invalidateWorkflowDetail={invalidate}
              routes={agentRunRoutes}
              optimisticMessages={chatOptimisticMessages}
              setOptimisticMessages={setChatOptimisticMessages}
            />
          ) : null}

          {stepsTabActive ? (
            <AuditTrailPanel execution={execution} steps={stepsQuery.data} />
          ) : null}

          {stepsTabActive && showExecutionObservationFallback ? (
            <section className="stack td-observation-region td-evidence-region">
              <div>
                <h3>Observation</h3>
                <p className="small">
                  Live logs are passive observation only. Use the Intervention panel for control actions.
                </p>
              </div>
              {logStreamingEnabled ? (
                resolvedAgentRunId ? (
                  <>
                    {showAgentRunAttachNotice ? (
                      <p className="small">Waiting for managed runtime launch to create live logs.</p>
                    ) : null}
                    <LiveLogsPanel
                      apiBase={payload.apiBase}
                      agentRunId={resolvedAgentRunId}
                      isTerminal={isTerminalExecution}
                      autoExpand={showAgentRunAttachNotice}
                      routes={agentRunRoutes}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                      structuredHistoryEnabled={structuredHistoryEnabled}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      agentRunId={resolvedAgentRunId}
                      stream="stdout"
                      routes={agentRunRoutes}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      agentRunId={resolvedAgentRunId}
                      stream="stderr"
                      routes={agentRunRoutes}
                    />
                    <DiagnosticsPanel
                      apiBase={payload.apiBase}
                      agentRunId={resolvedAgentRunId}
                      routes={agentRunRoutes}
                    />
                  </>
                ) : (
                  <>
                    <h3>Live Logs</h3>
                    <p className="small">{missingAgentRunState ? renderMissingAgentRunCopy(missingAgentRunState) : 'Waiting for workflow details...'}</p>
                  </>
                )
              ) : (
                <>
                  <h3>Live Logs</h3>
                  <p className="small">Live log streaming is disabled in the server dashboard config.</p>
                </>
              )}
            </section>
          ) : null}

          {debugTabActive && debugOn && debugVisible && execution.debugFields ? (
            <section className="stack">
              <h3>Debug Metadata</h3>
              <div className="grid-2">
                {buildDebugFieldEntries(execution).map(([key, value]) => (
                  <Card key={key} label={key}>
                    {formatDebugValue(value)}
                  </Card>
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : (
        <p>No workflow details.</p>
      )}
    </div>
  );
}

export function WorkflowDetailPage({ payload }: { payload: BootPayload }) {
  return (
    <DashboardToastProvider>
      <EntityDetailFrame entity="workflow" main={<WorkflowDetailPageContent payload={payload} />} />
    </DashboardToastProvider>
  );
}

export function WorkflowDetailEntrypoint({ payload }: { payload: BootPayload }) {
  const cfg = readDashboardConfig(payload);
  const isDesktop = useWorkflowWorkspaceDesktop();
  const workflowId = typeof window !== 'undefined'
    ? decodeWorkflowIdFromPath(window.location.pathname)
    : null;
  const search = useMemo(() => new URLSearchParams(typeof window !== 'undefined' ? window.location.search : ''), []);
  const workspaceShellEnabled = cfg?.features?.temporalDashboard?.workspaceShellEnabled !== false;
  const listEnabled = cfg?.features?.temporalDashboard?.listEnabled !== false;
  const displayMode = readWorkflowListDisplayMode(payload);

  if (workspaceShellEnabled && listEnabled && isDesktop && workflowId) {
    return (
      <WorkflowWorkspaceShell
        payload={payload}
        workflowId={workflowId}
        search={search}
        displayMode={displayMode}
      />
    );
  }

  return <WorkflowDetailPage payload={payload} />;
}

export default WorkflowDetailEntrypoint;
