import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Anser from 'anser';
import { Virtuoso } from 'react-virtuoso';
import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { executionStatusPillProps } from '../utils/executionStatusPillClasses';
import { SkillProvenanceBadge } from '../components/skills/SkillProvenanceBadge';
import { formatRuntimeLabel } from '../utils/formatters';
import {
  recordTemporalTaskEditingClientEvent,
  taskEditHref,
  taskRerunHref,
} from '../lib/temporalTaskEditing';

type DashboardConfig = {
  pollIntervalsMs?: { list?: number; detail?: number; events?: number };
  features?: {
    temporalDashboard?: {
      actionsEnabled?: boolean;
      temporalTaskEditing?: boolean;
      debugFieldsEnabled?: boolean;
    };
    logStreamingEnabled?: boolean;
    liveLogsSessionTimelineEnabled?: boolean;
    liveLogsSessionTimelineRollout?: string;
    liveLogsStructuredHistoryEnabled?: boolean;
  };
  sources?: {
    temporal?: Record<string, string>;
    taskRuns?: Record<string, string>;
  };
};

type LiveLogsSessionTimelineRollout = 'off' | 'internal' | 'codex_managed' | 'all_managed';

const GITHUB_PULL_REQUEST_PATH_PATTERN = /^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/i;
const SESSION_PROJECTION_POLL_MS = 5000;

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
  taskRunId,
}: {
  config: DashboardConfig | undefined;
  targetRuntime: string | null | undefined;
  taskRunId: string | null | undefined;
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
    return Boolean(String(taskRunId || '').trim());
  }
  return config?.features?.liveLogsSessionTimelineEnabled === true;
}

function shouldUseStructuredHistory(config: DashboardConfig | undefined): boolean {
  return config?.features?.liveLogsStructuredHistoryEnabled !== false;
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
    selectedVersions: z
      .array(
        z
          .object({
            name: z.string(),
            version: z.string().nullable().optional(),
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
            taskRunId: z.string().nullable().optional(),
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

const ExecutionDetailSchema = z
  .object({
    taskId: z.string(),
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
    prerequisites: z.array(DependencySummarySchema).default([]),
    dependents: z.array(DependencySummarySchema).default([]),
    attentionRequired: z.boolean().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    model: z.string().nullable().optional(),
    profileId: z.string().nullable().optional(),
    providerId: z.string().nullable().optional(),
    providerLabel: z.string().nullable().optional(),
    effort: z.string().nullable().optional(),
    startingBranch: z.string().nullable().optional(),
    targetBranch: z.string().nullable().optional(),
    repository: z.string().nullable().optional(),
    prUrl: z.string().nullable().optional(),
    resolvedSkillsetRef: z.string().nullable().optional(),
    taskSkills: z.array(z.string()).nullable().optional(),
    skillRuntime: SkillRuntimeSchema.nullable().optional(),
    publishMode: z.string().nullable().optional(),
    mergeAutomationSelected: z.boolean().optional().default(false),
    mergeAutomation: MergeAutomationSchema.nullable().optional(),
    summaryArtifactRef: z.string().nullable().optional(),
    summary_artifact_ref: z.string().nullable().optional(),
    scheduledFor: z.string().nullable().optional(),
    createdAt: z.string(),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().optional(),
    closedAt: z.string().nullable().optional(),
    taskRunId: z.string().nullable().optional(),
    task_run_id: z.string().nullable().optional(),
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
    actions: z
      .object({
        canSetTitle: z.boolean().optional(),
        canUpdateInputs: z.boolean().optional(),
        canRerun: z.boolean().optional(),
        canApprove: z.boolean().optional(),
        canPause: z.boolean().optional(),
        canResume: z.boolean().optional(),
        canCancel: z.boolean().optional(),
        canReject: z.boolean().optional(),
        canSendMessage: z.boolean().optional(),
        canBypassDependencies: z.boolean().optional(),
        disabledReasons: z.record(z.string(), z.string()).optional(),
      })
      .passthrough()
      .optional(),
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
    approvalState: RemediationApprovalStateSchema.nullable().optional(),
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
  task_run_id: z.string(),
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
  action: z.enum(['send_follow_up', 'clear_session']),
  projection: ArtifactSessionProjectionSchema,
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

const ObservabilityEventsResponseSchema = z.object({
  events: z.array(ObservabilityEventSchema).default([]),
  truncated: z.boolean().default(false),
  sessionSnapshot: SessionSnapshotSchema.nullable().optional(),
});

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
    taskRunId: z.string().nullable().optional(),
  })
  .default({
    childWorkflowId: null,
    childRunId: null,
    taskRunId: null,
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
  })
  .default({
    outputSummary: null,
    outputPrimary: null,
    runtimeStdout: null,
    runtimeStderr: null,
    runtimeMergedLogs: null,
    runtimeDiagnostics: null,
    providerSnapshot: null,
  });

const StepLedgerWorkloadSchema = z
  .object({
    taskRunId: z.string().nullable().optional(),
    stepId: z.string().nullable().optional(),
    attempt: z.number().nullable().optional(),
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
    title: z.string(),
    tool: StepLedgerToolSchema.default({}),
    dependsOn: z.array(z.string()).default([]),
    status: z.string(),
    waitingReason: z.string().nullable().optional(),
    attentionRequired: z.boolean().optional(),
    attempt: z.number().default(0),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    checks: z.array(StepLedgerCheckSchema).default([]),
    refs: StepLedgerRefsSchema,
    artifacts: StepLedgerArtifactsSchema,
    workload: StepLedgerWorkloadSchema.nullable().optional(),
    lastError: z.unknown().nullable().optional(),
  })
  .passthrough();

const StepLedgerSnapshotSchema = z.object({
  workflowId: z.string(),
  runId: z.string(),
  runScope: z.string().default('latest'),
  steps: z.array(StepLedgerRowSchema).default([]),
});

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
    publishContext: z
      .object({
        branch: z.string().nullable().optional(),
        baseRef: z.string().nullable().optional(),
        commitCount: z.union([z.number(), z.string()]).nullable().optional(),
        pullRequestUrl: z.string().nullable().optional(),
      })
      .passthrough()
      .optional(),
    mergeAutomation: MergeAutomationSchema.optional(),
  })
  .passthrough();

function readDashboardConfig(payload: BootPayload): DashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

function decodeTaskPathSegment(segment: string | null | undefined): string | null {
  if (!segment) return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
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

function taskRunRoute(
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

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
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

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function FactGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="td-group">
      <h4>{title}</h4>
      <dl>{children}</dl>
    </div>
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

function formatDurationMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (value < 1000) return `${value} ms`;
  const totalSeconds = Math.round(value / 1000);
  if (totalSeconds < 60) {
    const seconds = value / 1000;
    return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function formatDependencyResolution(value: string | null | undefined): string {
  const normalized = String(value || '').trim();
  if (!normalized) return '—';
  return normalized.replaceAll('_', ' ');
}

function dependencyHref(workflowId: string): string {
  return `/tasks/${encodeURIComponent(workflowId)}?source=temporal`;
}

function MergeAutomationPanel({
  mergeAutomation,
}: {
  mergeAutomation: z.infer<typeof MergeAutomationSchema>;
}) {
  const workflowId = mergeAutomation.workflowId || mergeAutomation.childWorkflowId || '';
  const resolverChildren: Array<{
    workflowId: string;
    taskRunId?: string | null;
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
        <Card label="Status">{mergeAutomation.status || '—'}</Card>
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
                {child.status ? <span className="small"> {child.status}</span> : null}
                {child.taskRunId ? (
                  <span className="small">
                    {' '}
                    logs: <code className="text-xs break-all">{child.taskRunId}</code>
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
    ['Temporal Run ID', debugFields.temporalRunId || execution.temporalRunId || execution.runId],
    ['Namespace', debugFields.namespace || execution.namespace],
    ['Temporal Status', debugFields.temporalStatus || execution.temporalStatus],
    ['Raw State', debugFields.rawState || execution.rawState || execution.state],
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
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(apiBase, routeTemplate, `/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`, {
      taskRunId,
    }),
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
  taskRunId: string,
  stream: 'stdout' | 'stderr',
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`,
      { taskRunId },
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
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`,
      { taskRunId },
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

function deriveCodexSessionId(
  taskRunId: string | null | undefined,
  runtimeId: string | null | undefined,
): string | null {
  const normalizedRuntime = String(runtimeId || '').trim().toLowerCase();
  if (!taskRunId || (normalizedRuntime !== 'codex' && normalizedRuntime !== 'codex_cli')) {
    return null;
  }
  return `sess:${taskRunId}:codex_cli`;
}

async function fetchArtifactSessionProjection(
  apiBase: string,
  taskRunId: string,
  sessionId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionProjectionSchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}`,
      { taskRunId, sessionId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Session continuity: ${resp.status}`);
  }
  return ArtifactSessionProjectionSchema.parse(await resp.json());
}

async function controlArtifactSession(
  apiBase: string,
  taskRunId: string,
  sessionId: string,
  body: { action: 'send_follow_up' | 'clear_session'; message?: string; reason?: string },
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionControlResponseSchema>> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}/control`,
      { taskRunId, sessionId },
    ),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    throw new Error(`Session control: ${resp.status}`);
  }
  return ArtifactSessionControlResponseSchema.parse(await resp.json());
}

/** Fetch the observability summary for a task run. */
async function fetchObservabilitySummary(
  apiBase: string,
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilitySummarySchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/observability-summary`,
      { taskRunId },
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
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilityEventsResponseSchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/observability/events`,
      { taskRunId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  return ObservabilityEventsResponseSchema.parse(await resp.json());
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

const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'failed',
  'canceled',
  'cancelled',
  'timed_out',
]);

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
  rowType: 'output' | 'system' | 'session' | 'approval' | 'publication' | 'boundary' | 'fallback';
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

function classifyTimelineRow(event: ObservabilityEvent): TimelineRow['rowType'] {
  if (event.kind === 'session_reset_boundary') {
    return 'boundary';
  }
  if (event.stream === 'system') {
    return 'system';
  }
  if (event.stream === 'session') {
    if ((event.kind ?? '').startsWith('approval_')) {
      return 'approval';
    }
    if ((event.kind ?? '').endsWith('_published')) {
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
    source !== 'task-dashboard-objective-attachment' &&
    source !== 'task-dashboard-step-attachment'
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

  if (source === 'task-dashboard-step-attachment') {
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
        Images are grouped by the persisted task target from the execution snapshot.
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
    addLink('Open summary artifact', row.metadata.summaryRef ?? row.metadata.artifactRef);
  }
  if (row.kind === 'checkpoint_published') {
    addLink('Open checkpoint artifact', row.metadata.checkpointRef ?? row.metadata.artifactRef);
  }
  if (row.kind === 'session_cleared' || row.kind === 'session_reset_boundary') {
    addLink(
      'Open control event artifact',
      row.metadata.controlEventRef ?? (row.kind === 'session_cleared' ? row.metadata.artifactRef : null),
    );
    addLink(
      'Open reset boundary artifact',
      row.metadata.resetBoundaryRef ?? (row.kind === 'session_reset_boundary' ? row.metadata.artifactRef : null),
    );
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
      {timelineViewerEnabled && row.kind ? (
        <span className="live-logs-kind-chip">{row.kind.replaceAll('_', ' ')}</span>
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

type TaskRunRouteTemplates = {
  observabilitySummary?: string | undefined;
  observabilityEvents?: string | undefined;
  logsStream?: string | undefined;
  logsStdout?: string | undefined;
  logsStderr?: string | undefined;
  logsMerged?: string | undefined;
  diagnostics?: string | undefined;
  artifactSession?: string | undefined;
  artifactSessionControl?: string | undefined;
};

function readTaskRunRouteTemplates(config: DashboardConfig | undefined): TaskRunRouteTemplates {
  return {
    observabilitySummary: config?.sources?.taskRuns?.observabilitySummary,
    observabilityEvents: config?.sources?.taskRuns?.observabilityEvents,
    logsStream: config?.sources?.taskRuns?.logsStream,
    logsStdout: config?.sources?.taskRuns?.logsStdout,
    logsStderr: config?.sources?.taskRuns?.logsStderr,
    logsMerged: config?.sources?.taskRuns?.logsMerged,
    diagnostics: config?.sources?.taskRuns?.diagnostics,
    artifactSession: config?.sources?.taskRuns?.artifactSession,
    artifactSessionControl: config?.sources?.taskRuns?.artifactSessionControl,
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
  return normalized === 'succeeded'
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

function StepCheckBadge({ check }: { check: z.infer<typeof StepLedgerCheckSchema> }) {
  const checkStatusClass = stepCheckStatusClass(check.status);
  const statusPillClassName = executionStatusPillProps(check.status).className;
  return (
    <span className={`step-check-badge ${checkStatusClass} ${statusPillClassName}`}>
      {check.kind.replaceAll('_', ' ')}: {check.status.replaceAll('_', ' ')}
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
      <li><strong>Attempt:</strong> {row.attempt}</li>
      <li><strong>Depends on:</strong> {row.dependsOn.length > 0 ? row.dependsOn.join(', ') : 'None'}</li>
      <li><strong>Child workflow:</strong> {row.refs.childWorkflowId ? <code className="text-xs break-all">{row.refs.childWorkflowId}</code> : '—'}</li>
      <li><strong>Child run:</strong> {row.refs.childRunId ? <code className="text-xs break-all">{row.refs.childRunId}</code> : '—'}</li>
      <li><strong>Task run:</strong> {row.refs.taskRunId ? <code className="text-xs break-all">{row.refs.taskRunId}</code> : '—'}</li>
      <li><strong>Started:</strong> {formatWhen(row.startedAt)}</li>
      <li><strong>Updated:</strong> {formatWhen(row.updatedAt)}</li>
    </ul>
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
        <li><strong>Status:</strong> {formatOptionalValue(workload.status)}</li>
        <li><strong>Exit code:</strong> {formatOptionalValue(workload.exitCode)}</li>
        <li><strong>Duration:</strong> {formatOptionalValue(workload.durationSeconds)}s</li>
        <li><strong>Tool:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.toolName)}</code></li>
        <li><strong>Step:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.stepId)}</code></li>
        <li><strong>Task run:</strong> <code className="text-xs break-all">{formatOptionalValue(workload.taskRunId)}</code></li>
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
  routes,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  routes: TaskRunRouteTemplates;
}) {
  if (!logStreamingEnabled) {
    return (
      <p className="small">Live log streaming is disabled in the server dashboard config.</p>
    );
  }

  const taskRunId = row.refs.taskRunId;
  if (!taskRunId) {
    return (
      <p className="small">
        {renderMissingTaskRunCopy(
          row.status === 'running' || row.status === 'awaiting_external'
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
        taskRunId={taskRunId}
        isTerminal={stepTerminal(row.status)}
        autoExpand
        routes={routes}
        sessionTimelineEnabled={sessionTimelineEnabled}
        structuredHistoryEnabled={structuredHistoryEnabled}
      />
      <StaticLogPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        stream="stdout"
        routes={routes}
      />
      <StaticLogPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        stream="stderr"
        routes={routes}
      />
      <DiagnosticsPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        routes={routes}
      />
    </div>
  );
}

function stepStatusIcon(status: string): { icon: string; cssClass: string } {
  const s = status.toLowerCase().trim();
  if (s === 'succeeded' || s === 'completed') return { icon: '✓', cssClass: 'step-icon-ok' };
  if (s === 'failed') return { icon: '✕', cssClass: 'step-icon-fail' };
  if (s === 'canceled' || s === 'cancelled' || s === 'skipped') return { icon: '–', cssClass: 'step-icon-skip' };
  if (s === 'running' || s === 'executing' || s === 'planning' || s === 'initializing' || s === 'finalizing')
    return { icon: '●', cssClass: 'step-icon-running' };
  return { icon: '○', cssClass: 'step-icon-pending' };
}

function StepLedgerRowCard({
  apiBase,
  logStreamingEnabled,
  sessionTimelineEnabled,
  structuredHistoryEnabled,
  row,
  runId,
  expanded,
  onToggle,
  isLast,
  routes,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  runId: string;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
  routes: TaskRunRouteTemplates;
}) {
  const lastError = formatStepLastError(row.lastError);
  const { icon, cssClass } = stepStatusIcon(row.status);

  return (
    <article className={`step-tl-row${expanded ? ' step-tl-expanded' : ''}${isLast ? ' step-tl-last' : ''}`}>
      <div className="step-tl-gutter" aria-hidden="true">
        <span className={`step-tl-icon ${cssClass}`} title={row.status.replaceAll('_', ' ')}>{icon}</span>
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
              <span {...executionStatusPillProps(row.status)}>{row.status.replaceAll('_', ' ')}</span>
              {row.attempt > 1 ? <span className="step-attempt-pill">Attempt {row.attempt}</span> : null}
              <span className={`step-tl-chevron${expanded ? ' step-tl-chevron-open' : ''}`} aria-hidden="true">›</span>
            </span>
          </div>
          {!expanded && row.summary ? (
            <p className="step-tl-summary">{row.summary}</p>
          ) : null}
          {!expanded && row.checks.length > 0 ? (
            <div className="step-check-badges">
              {row.checks.map((check, index) => (
                <StepCheckBadge key={`${check.kind}-${check.status}-${index}`} check={check} />
              ))}
            </div>
          ) : null}
        </button>
        {expanded ? (
          <div className="step-tl-details">
            <section className="step-tl-detail-section">
              <h4>Summary</h4>
              <p className="small">{row.summary || 'No step summary yet.'}</p>
              {row.waitingReason ? <p className="small">Waiting reason: {row.waitingReason}</p> : null}
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
            <section className="step-tl-detail-section">
              <h4>Logs & Diagnostics</h4>
              <StepObservabilityGroup
                apiBase={apiBase}
                logStreamingEnabled={logStreamingEnabled}
                sessionTimelineEnabled={sessionTimelineEnabled}
                structuredHistoryEnabled={structuredHistoryEnabled}
                row={row}
                routes={routes}
              />
            </section>
            <section className="step-tl-detail-section">
              <h4>Artifacts</h4>
              <StepArtifactsList artifacts={row.artifacts} />
            </section>
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
  taskRunId,
  isTerminal,
  autoExpand = false,
  routes,
  sessionTimelineEnabled,
  structuredHistoryEnabled,
}: {
  apiBase: string;
  taskRunId: string;
  isTerminal: boolean;
  autoExpand?: boolean;
  routes: TaskRunRouteTemplates;
  sessionTimelineEnabled: boolean;
  structuredHistoryEnabled: boolean;
}) {
  const [logContent, setLogContent] = useState<TimelineRow[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  const [expanded, setExpanded] = useState(false);
  const isVisible = usePageVisibility();
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const isTerminalRef = useRef(isTerminal);
  const [sessionSnapshot, setSessionSnapshot] = useState<SessionSnapshot | null>(null);

  // Keep isTerminalRef current so the onerror handler always sees the latest value.
  useEffect(() => {
    isTerminalRef.current = isTerminal;
  }, [isTerminal]);

  // Reset log state whenever we switch to a different task run.
  useEffect(() => {
    setLogContent([]);
    lastSeqRef.current = null;
    setViewerState('starting');
  }, [taskRunId]);

  useEffect(() => {
    if (autoExpand) {
      setExpanded(true);
    }
  }, [autoExpand]);

  // Query for observability summary
  const summaryQuery = useQuery({
    queryKey: ['observability-summary', taskRunId],
    queryFn: () => fetchObservabilitySummary(apiBase, taskRunId, routes.observabilitySummary),
    enabled: !!taskRunId && expanded,
    // The summary indicates stream availability; refetch occasionally if not terminal
    staleTime: 1000 * 10,
  });

  const historyQuery = useQuery({
    queryKey: ['task-run-observability-events', taskRunId],
    queryFn: () => fetchObservabilityEvents(apiBase, taskRunId, routes.observabilityEvents),
    enabled: structuredHistoryEnabled && !!taskRunId && expanded && summaryQuery.isSuccess,
    staleTime: Infinity,
    retry: false,
  });
  const historyRows = useMemo(() => mapEventsToTimelineRows(historyQuery.data), [historyQuery.data]);
  const historyUnavailable = !structuredHistoryEnabled || historyQuery.isError || historyQuery.data === null;
  const historyEmpty = structuredHistoryEnabled && historyQuery.isSuccess && historyRows.length === 0;

  // Legacy fallback: keep merged text available for older runs or partial failures.
  const tailQuery = useQuery({
    queryKey: ['task-run-tail', taskRunId],
    queryFn: () => fetchMergedTail(apiBase, taskRunId, routes.logsMerged),
    enabled:
      !!taskRunId &&
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
    if (!taskRunId || !expanded || !summaryQuery.isSuccess || !isVisible) return;
    if ((structuredHistoryEnabled && !historyQuery.isSuccess && !tailQuery.isSuccess) || (!structuredHistoryEnabled && !tailQuery.isSuccess)) return;

    const summary = summaryQuery.data;
    const runIsTerminal =
      isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
    const supportsStreaming = summary?.supportsLiveStreaming ?? false;

    if (runIsTerminal || !supportsStreaming) return;

    let cancelled = false;

    const since = lastSeqRef.current != null ? `?since=${lastSeqRef.current}` : '';
    const streamUrl = taskRunRoute(
      apiBase,
      routes.logsStream,
      `/task-runs/${encodeURIComponent(taskRunId)}/logs/stream`,
      { taskRunId },
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
    taskRunId,
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

  const downloadUrl = taskRunRoute(
    apiBase,
    routes.logsMerged,
    `/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`,
    { taskRunId },
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
          Task run <code className="text-xs">{taskRunId}</code> — {statusLabel}
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
            <div data-testid="live-logs-timeline-viewer" className="live-logs-viewer">
              <Virtuoso
                style={{ height: 400 }}
                data={logContent}
                computeItemKey={(_, row) => row.id}
                itemContent={(_, row) => renderTimelineRow(row, wrapLines, true, apiBase)}
              />
            </div>
          ) : (
            <div data-testid="live-logs-legacy-viewer" className="live-logs-legacy-viewer">
              {logContent.map((line) => renderTimelineRow(line, wrapLines, false, apiBase))}
            </div>
          )}
        </div>
      </div>
    </details>
  );
}

function InterventionPanel({
  actions,
  busy,
  audit,
  onPause,
  onResume,
  onApprove,
  onCancel,
  onReject,
  onSendMessage,
}: {
  actions: NonNullable<z.infer<typeof ExecutionDetailSchema>['actions']>;
  busy: boolean;
  audit: Array<{
    action: string;
    transport: string;
    summary: string;
    detail?: string | null | undefined;
    createdAt: string;
  }>;
  onPause: () => void;
  onResume: () => void;
  onApprove: () => void;
  onCancel: () => void;
  onReject: () => void;
  onSendMessage: (message: string) => void;
}) {
  const [operatorMessage, setOperatorMessage] = useState('');
  const hasControls = Boolean(
    actions.canPause ||
      actions.canResume ||
      actions.canApprove ||
      actions.canCancel ||
      actions.canReject ||
      actions.canSendMessage,
  );

  const submitMessage = () => {
    const message = operatorMessage.trim();
    if (!message) return;
    onSendMessage(message);
    setOperatorMessage('');
  };

  return (
    <section className="stack">
      <div>
        <h3>Intervention</h3>
        <p className="small">
          Controls use Temporal or provider-native APIs and do not require a live log connection.
        </p>
      </div>

      {hasControls ? (
        <div className="actions">
          {actions.canPause ? (
            <button type="button" disabled={busy} className="secondary" onClick={onPause}>
              Pause
            </button>
          ) : null}
          {actions.canResume ? (
            <button type="button" disabled={busy} className="queue-action" onClick={onResume}>
              Resume
            </button>
          ) : null}
          {actions.canApprove ? (
            <button type="button" disabled={busy} className="queue-action" onClick={onApprove}>
              Approve
            </button>
          ) : null}
          {actions.canReject ? (
            <button
              type="button"
              disabled={busy}
              className="queue-action queue-action-danger"
              onClick={onReject}
            >
              Reject
            </button>
          ) : null}
          {actions.canCancel ? (
            <button
              type="button"
              disabled={busy}
              className="queue-action queue-action-danger"
              onClick={onCancel}
            >
              Cancel
            </button>
          ) : null}
        </div>
      ) : (
        <p className="small">No intervention controls are available for the current task state.</p>
      )}

      {actions.canSendMessage ? (
        <div className="stack">
          <label htmlFor="operator-message">Operator message</label>
          <textarea
            id="operator-message"
            value={operatorMessage}
            onChange={(event) => setOperatorMessage(event.target.value)}
            rows={3}
            placeholder="Send an explicit operator message without using the log viewer."
          />
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={busy || !operatorMessage.trim()}
              onClick={submitMessage}
            >
              Send Message
            </button>
          </div>
        </div>
      ) : null}

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
  taskRunId,
  stream,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  stream: 'stdout' | 'stderr';
  routes: TaskRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const streamQuery = useQuery({
    queryKey: ['task-run-stream', taskRunId, stream],
    queryFn: () =>
      fetchStream(
        apiBase,
        taskRunId,
        stream,
        stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
      ),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const title = stream === 'stdout' ? 'Stdout' : 'Stderr';

  const handleCopy = () => {
    if (!streamQuery.data) return;
    copyTextToClipboard(streamQuery.data);
  };

  const downloadUrl = taskRunRoute(
    apiBase,
    stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
    `/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`,
    { taskRunId },
  );

  return (
    <details className="stack" open={expanded}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>{title}</span>
      </summary>
      <div className="stack">
        {expanded ? (
          <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
              <span className="small">Wrap lines</span>
            </label>
            <button className="secondary small" onClick={handleCopy}>Copy</button>
            <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
        ) : null}
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <pre
            style={{
              background: '#111',
              color: '#e8e8e8',
              padding: '0.75rem',
              fontSize: '0.7rem',
              lineHeight: 1.4,
              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
              wordBreak: wrapLines ? 'break-all' : 'normal',
              borderRadius: '4px',
              margin: 0,
            }}
          >
            {streamQuery.isLoading ? 'Loading...' : streamQuery.isError ? `Error loading ${stream}` : streamQuery.data || `(no ${stream} output)`}
          </pre>
        </div>
      </div>
    </details>
  );
}

function DiagnosticsPanel({
  apiBase,
  taskRunId,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  routes: TaskRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const diagQuery = useQuery({
    queryKey: ['task-run-diagnostics', taskRunId],
    queryFn: () => fetchDiagnostics(apiBase, taskRunId, routes.diagnostics),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const handleCopy = () => {
    if (!diagQuery.data) return;
    copyTextToClipboard(diagQuery.data);
  };

  const downloadUrl = taskRunRoute(
    apiBase,
    routes.diagnostics,
    `/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`,
    { taskRunId },
  );

  return (
    <details className="stack" open={expanded}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>Diagnostics</span>
      </summary>
      <div className="stack">
        {expanded ? (
          <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
              <span className="small">Wrap lines</span>
            </label>
            <button className="secondary small" onClick={handleCopy}>Copy</button>
            <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
        ) : null}
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <pre
            style={{
              background: '#111',
              color: '#e8e8e8',
              padding: '0.75rem',
              fontSize: '0.7rem',
              lineHeight: 1.4,
              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
              wordBreak: wrapLines ? 'break-all' : 'normal',
              borderRadius: '4px',
              margin: 0,
            }}
          >
            {diagQuery.isLoading ? 'Loading...' : diagQuery.isError ? 'Error loading diagnostics' : diagQuery.data || '(no diagnostics output)'}
          </pre>
        </div>
      </div>
    </details>
  );
}

function SessionContinuityPanel({
  apiBase,
  taskRunId,
  targetRuntime,
  isTerminal,
  onCancel,
  invalidateTaskDetail,
  cancelBusy,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  targetRuntime: string | null | undefined;
  isTerminal: boolean;
  onCancel: () => void;
  invalidateTaskDetail: () => void;
  cancelBusy: boolean;
  routes: TaskRunRouteTemplates;
}) {
  const queryClient = useQueryClient();
  const sessionId = deriveCodexSessionId(taskRunId, targetRuntime);
  const [followUpMessage, setFollowUpMessage] = useState('');
  const [panelError, setPanelError] = useState<string | null>(null);

  const projectionQuery = useQuery({
    queryKey: ['task-run-session-projection', taskRunId, sessionId],
    queryFn: () => {
      if (!sessionId) return Promise.resolve(null);
      return fetchArtifactSessionProjection(apiBase, taskRunId, sessionId, routes.artifactSession);
    },
    enabled: Boolean(taskRunId && sessionId),
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
    mutationFn: async (body: { action: 'send_follow_up' | 'clear_session'; message?: string; reason?: string }) => {
      if (!sessionId) throw new Error('Managed session is unavailable.');
      return controlArtifactSession(apiBase, taskRunId, sessionId, body, routes.artifactSessionControl);
    },
    onSuccess: (result) => {
      setPanelError(null);
      void queryClient.setQueryData(
        ['task-run-session-projection', taskRunId, sessionId],
        result.projection,
      );
      invalidateTaskDetail();
      if (result.action === 'send_follow_up') {
        setFollowUpMessage('');
      }
    },
    onError: (error: Error) => setPanelError(error.message),
  });

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
  const latestBadges = [
    ['Latest Summary', projection.latest_summary_ref?.artifact_id ?? null],
    ['Latest Checkpoint', projection.latest_checkpoint_ref?.artifact_id ?? null],
    ['Latest Control', projection.latest_control_event_ref?.artifact_id ?? null],
    ['Latest Reset', projection.latest_reset_boundary_ref?.artifact_id ?? null],
  ].filter(([, artifactId]) => artifactId !== null) as Array<[string, string]>;
  const busy = controlMutation.isPending || cancelBusy;

  const submitFollowUp = () => {
    const message = followUpMessage.trim();
    if (!message) return;
    setPanelError(null);
    controlMutation.mutate({
      action: 'send_follow_up',
      message,
    });
  };

  const clearSession = () => {
    setPanelError(null);
    controlMutation.mutate({
      action: 'clear_session',
    });
  };

  return (
    <section className="stack">
      <div>
        <h3>Session Continuity</h3>
        <p className="small">
          Continuity artifacts are durable evidence and drill-down for this session.
        </p>
        <p className="small">
          Session <code>{projection.session_id}</code> — Epoch {projection.session_epoch}
        </p>
      </div>

      {panelError ? <div className="notice error">{panelError}</div> : null}

      <div className="grid-2">
        <Card label="Session ID">
          <code className="text-xs break-all">{projection.session_id}</code>
        </Card>
        <Card label="Current Epoch">{projection.session_epoch}</Card>
      </div>

      {latestBadges.length > 0 ? (
        <div className="actions">
          {latestBadges.map(([label, artifactId]) => (
            <span key={`${label}-${artifactId}`} className="card">
              <strong>{label}:</strong> <code className="text-xs">{artifactId}</code>
            </span>
          ))}
        </div>
      ) : null}

      <div className="stack">
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
      </div>

      <div className="stack">
        <label htmlFor="session-follow-up">Follow-up message</label>
        <textarea
          id="session-follow-up"
          value={followUpMessage}
          onChange={(event) => setFollowUpMessage(event.target.value)}
          rows={3}
          placeholder="Send a follow-up turn to the managed Codex session."
          disabled={busy || isTerminal}
        />
        <div className="actions">
          <button
            type="button"
            className="secondary"
            disabled={busy || isTerminal || !followUpMessage.trim()}
            onClick={submitFollowUp}
          >
            Send follow-up
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || isTerminal}
            onClick={clearSession}
          >
            Clear / Reset
          </button>
          <button
            type="button"
            className="queue-action queue-action-danger"
            disabled={busy || isTerminal}
            onClick={onCancel}
          >
            Cancel Execution
          </button>
        </div>
      </div>
    </section>
  );
}

type MissingTaskRunState = 'waiting_for_launch' | 'binding_missing' | 'launch_failed';

function inferMissingTaskRunState(execution: z.infer<typeof ExecutionDetailSchema>): MissingTaskRunState {
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

function renderMissingTaskRunCopy(state: MissingTaskRunState): string {
  if (state === 'launch_failed') {
    return 'This execution ended before a managed runtime observability record was created.';
  }
  if (state === 'binding_missing') {
    return 'This execution is running but has not received its managed runtime binding yet.';
  }
  return 'Waiting for managed runtime launch to create live logs.';
}

function isRemediationEligibleTarget(execution: z.infer<typeof ExecutionDetailSchema>): boolean {
  const state = (execution.rawState || execution.state || execution.status || '').toLowerCase();
  return (
    execution.attentionRequired === true ||
    Boolean(execution.waitingReason) ||
    state.includes('failed') ||
    state.includes('stuck') ||
    state === 'awaiting_external'
  );
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
    'remediation.decision_log': 'Decision Log',
    'remediation.action_request': 'Action Request',
    'remediation.action_result': 'Action Result',
    'remediation.verification': 'Verification',
    'remediation.summary': 'Summary',
  };
  return labels[type] ?? type.replace(/^remediation\./, '').replaceAll('_', ' ');
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
          Remediation relationship data is degraded. Existing task detail remains available.
        </div>
      ) : null}
      {inboundItems.length > 0 ? (
        <div className="stack">
          <h4>Remediation Tasks</h4>
          <ul className="td-remediation-list">
            {inboundItems.map((item) => (
              <li key={item.remediationWorkflowId} className="card">
                <a href={dependencyHref(item.remediationWorkflowId)}>
                  <code className="text-xs break-all">{item.remediationWorkflowId}</code>
                </a>
                <div className="grid-2">
                  <Card label="Status">{item.status || '—'}</Card>
                  <Card label="Authority">{item.authorityMode || '—'}</Card>
                  <Card label="Latest Action">{item.latestActionSummary || '—'}</Card>
                  <Card label="Resolution">{item.resolution || '—'}</Card>
                  <Card label="Lock">{item.activeLockScope || 'None'}</Card>
                  <Card label="Updated">{formatWhen(item.updatedAt)}</Card>
                </div>
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
        <p className="notice subtle">No inbound remediation tasks linked yet.</p>
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
                  <Card label="Status">{item.status || '—'}</Card>
                  <Card label="Evidence Bundle">{item.contextArtifactRef || 'Missing'}</Card>
                  <Card label="Approval">{item.approvalState?.decision || 'not_required'}</Card>
                </div>
                {!item.contextArtifactRef ? (
                  <p className="notice subtle">Evidence bundle is missing.</p>
                ) : null}
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

export function TaskDetailPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const cfg = readDashboardConfig(payload);
  const taskRunRoutes = readTaskRunRouteTemplates(cfg);
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const actionsOn = Boolean(cfg?.features?.temporalDashboard?.actionsEnabled);
  const taskEditingOn = Boolean(cfg?.features?.temporalDashboard?.temporalTaskEditing);
  const debugOn = Boolean(cfg?.features?.temporalDashboard?.debugFieldsEnabled);
  const logStreamingEnabled = cfg?.features?.logStreamingEnabled !== false;
  const structuredHistoryEnabled = shouldUseStructuredHistory(cfg);

  const taskIdMatch = window.location.pathname.match(
    /^\/tasks\/(?:temporal\/|proposals\/|schedules\/|manifests\/)?([^/]+)$/,
  );
  const taskId = decodeTaskPathSegment(taskIdMatch ? taskIdMatch[1] : null);
  const encodedTaskId = taskId ? encodeURIComponent(taskId) : null;
  const search = useMemo(() => new URLSearchParams(window.location.search), []);
  const sourceTemporal = search.get('source') === 'temporal';

  const [actionError, setActionError] = useState<string | null>(null);
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
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});
  const [instructionsExpanded, setInstructionsExpanded] = useState(false);
  const [remediationMode, setRemediationMode] = useState('snapshot_then_follow');
  const [remediationAuthority, setRemediationAuthority] = useState('approval_gated');
  const [remediationActionPolicy, setRemediationActionPolicy] = useState('admin_healer_default');

  const detailQuery = useQuery({
    queryKey: ['task-detail', encodedTaskId, sourceTemporal],
    queryFn: async () => {
      if (!encodedTaskId) throw new Error('Task ID is required.');
      const suffix = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(`${payload.apiBase}/executions/${encodedTaskId}${suffix}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch task: ${response.statusText}`);
      }
      return ExecutionDetailSchema.parse(await response.json());
    },
    enabled: Boolean(encodedTaskId),
    refetchInterval: liveUpdates ? detailPoll : false,
  });

  const execution = detailQuery.data;
  const workflowId = execution?.workflowId || execution?.taskId || taskId || '';
  const runId = execution?.temporalRunId || execution?.runId || '';
  const namespace = execution?.namespace || '';
  const summaryArtifactRef = execution?.summaryArtifactRef || execution?.summary_artifact_ref || '';
  const explicitTaskRunId = execution?.taskRunId || execution?.task_run_id || '';
  const resolvedTaskRunId = explicitTaskRunId;
  const shouldFetchRemediationLinks = Boolean(execution && workflowId);
  const sessionTimelineEnabled = shouldEnableSessionTimelineViewer({
    config: cfg,
    targetRuntime: execution?.targetRuntime,
    taskRunId: resolvedTaskRunId,
  });
  const previousTaskRunIdRef = useRef(resolvedTaskRunId);
  const [showTaskRunAttachNotice, setShowTaskRunAttachNotice] = useState(false);

  useEffect(() => {
    if (!resolvedTaskRunId) {
      previousTaskRunIdRef.current = '';
      setShowTaskRunAttachNotice(false);
      return;
    }

    if (!previousTaskRunIdRef.current) {
      previousTaskRunIdRef.current = resolvedTaskRunId;
      setShowTaskRunAttachNotice(true);
      const timeout = window.setTimeout(() => {
        setShowTaskRunAttachNotice(false);
      }, 250);
      return () => window.clearTimeout(timeout);
    }

    previousTaskRunIdRef.current = resolvedTaskRunId;
    setShowTaskRunAttachNotice(false);
    return undefined;
  }, [resolvedTaskRunId]);

  const missingTaskRunState = execution && !resolvedTaskRunId ? inferMissingTaskRunState(execution) : null;

  const stepsQuery = useQuery({
    queryKey: ['task-detail-steps', workflowId, execution?.stepsHref],
    queryFn: () => fetchStepLedger(String(execution?.stepsHref || '')),
    enabled: Boolean(execution?.stepsHref),
    refetchInterval: liveUpdates && execution?.stepsHref ? detailPoll : false,
  });
  const latestRunId = stepsQuery.data?.runId || runId;

  const artifactsQuery = useQuery({
    queryKey: ['task-detail-artifacts', namespace, workflowId, latestRunId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(latestRunId)}/artifacts`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Artifacts: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled:
      Boolean(namespace && workflowId && latestRunId)
      && (!execution?.stepsHref || stepsQuery.isSuccess || stepsQuery.isError),
    refetchInterval: liveUpdates && namespace && workflowId && latestRunId ? detailPoll : false,
  });

  const latestReportQuery = useQuery({
    queryKey: ['task-detail-latest-report', namespace, workflowId, latestRunId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(latestRunId)}/artifacts?link_type=report.primary&latest_only=true`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Report: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled:
      Boolean(namespace && workflowId && latestRunId)
      && (!execution?.stepsHref || stepsQuery.isSuccess || stepsQuery.isError),
    refetchInterval: liveUpdates && namespace && workflowId && latestRunId ? detailPoll : false,
  });

  const runSummaryQuery = useQuery({
    queryKey: ['task-detail-run-summary', summaryArtifactRef],
    queryFn: () => fetchRunSummaryArtifact(payload.apiBase, summaryArtifactRef),
    enabled: Boolean(summaryArtifactRef),
    refetchInterval: liveUpdates && summaryArtifactRef ? detailPoll : false,
  });
  const inboundRemediationsQuery = useQuery({
    queryKey: ['task-detail-remediations', workflowId, 'inbound'],
    queryFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/remediations?direction=inbound`,
      );
      if (!response.ok) throw new Error(`Remediations: ${response.statusText}`);
      return RemediationLinksSchema.parse(await response.json());
    },
    enabled: shouldFetchRemediationLinks,
  });
  const outboundRemediationsQuery = useQuery({
    queryKey: ['task-detail-remediations', workflowId, 'outbound'],
    queryFn: async () => {
      const response = await fetch(
        `${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/remediations?direction=outbound`,
      );
      if (!response.ok) throw new Error(`Remediations: ${response.statusText}`);
      return RemediationLinksSchema.parse(await response.json());
    },
    enabled: shouldFetchRemediationLinks,
  });
  const runSummary = runSummaryQuery.data;
  const displayedMergeAutomation =
    execution?.mergeAutomation || runSummary?.mergeAutomation || null;
  const displayedSummary = runSummary?.operatorSummary || execution?.summary || '—';
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
    if (execution.prerequisites.length > 0) {
      return execution.prerequisites;
    }
    return ids.map((workflowId) => ({
      workflowId,
      title: workflowId,
      summary: null,
      state: null,
      closeStatus: null,
      workflowType: 'MoonMind.Run',
    }));
  }, [execution]);
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

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['task-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-steps', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-artifacts', namespace, workflowId, latestRunId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-latest-report', namespace, workflowId, latestRunId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-run-summary', summaryArtifactRef] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-remediations', workflowId] });
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
      reason,
    }: {
      action?: 'cancel' | 'reject';
      graceful?: boolean;
      reason?: string;
    }) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          action,
          graceful,
          ...(reason ? { reason } : {}),
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

  const createRemediationMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/remediation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          repository: execution?.repository ?? null,
          instructions: `Investigate and remediate target execution ${workflowId} using bounded evidence.`,
          remediation: {
            mode: remediationMode,
            authorityMode: remediationAuthority,
            target: {
              runId: latestRunId || runId || undefined,
            },
            actionPolicyRef: remediationActionPolicy.trim() || undefined,
            evidencePolicy: {
              includeStepLedger: true,
              includeDiagnostics: true,
              tailLines: 2000,
            },
            trigger: { type: 'manual' },
          },
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: () => {
      setActionNotice('Remediation task creation submitted.');
      invalidate();
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

  const onRename = () => {
    setActionError(null);
    const title = window.prompt('New task title', execution?.title || '');
    if (title === null || !title.trim()) return;
    updateMutation.mutate({ updateName: 'SetTitle', title: title.trim() });
  };

  const onPause = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Pause', payload: {} });
  };

  const onResume = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Resume', payload: {} });
  };

  const onApprove = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Approve', payload: {} });
  };

  const onSendMessage = (message: string) => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'SendMessage', payload: { message } });
  };

  const onBypassDependencies = () => {
    setActionError(null);
    if (!window.confirm('Bypass dependency waiting for this task?')) return;
    signalMutation.mutate({
      signalName: 'BypassDependencies',
      payload: { reason: 'Dependency wait bypassed by operator from Mission Control.' },
    });
  };

  const onCancel = () => {
    setActionError(null);
    if (!window.confirm('Cancel this task?')) return;
    cancelMutation.mutate({ action: 'cancel', graceful: true });
  };

  const onReject = () => {
    setActionError(null);
    if (!window.confirm('Reject this task?')) return;
    cancelMutation.mutate({
      action: 'reject',
      graceful: true,
      reason: 'Rejected by operator.',
    });
  };

  const actions = execution?.actions;
  const busy =
    updateMutation.isPending ||
    signalMutation.isPending ||
    cancelMutation.isPending ||
    createRemediationMutation.isPending ||
    remediationApprovalMutation.isPending;
  const editHref = workflowId ? taskEditHref(workflowId) : '';
  const rerunHref = workflowId ? taskRerunHref(workflowId) : '';
  const onTaskEditingNavigation = (
    event: MouseEvent<HTMLAnchorElement>,
    telemetryEvent: 'detail_edit_click' | 'detail_rerun_click',
  ) => {
    if (busy) {
      event.preventDefault();
      return;
    }
    recordTemporalTaskEditingClientEvent({
      event: telemetryEvent,
      mode: 'detail',
      workflowId,
    });
  };
  const isTerminalExecution = TERMINAL_STATES.has(execution?.rawState || execution?.state || '');
  const canCreateRemediation = Boolean(execution && isRemediationEligibleTarget(execution));
  const hasTaskEditingActions = taskEditingOn && Boolean(actions?.canUpdateInputs || actions?.canRerun);
  const hasTaskActions = Boolean(actions?.canSetTitle || hasTaskEditingActions || canCreateRemediation);
  const taskInstructions = execution?.taskInstructions?.trim() || '';
  const hasTaskInstructions = taskInstructions.length > 0;
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
  const toggleStep = (logicalStepId: string) => {
    setExpandedSteps((prev) => ({
      ...prev,
      [logicalStepId]: !prev[logicalStepId],
    }));
  };
  const primaryReport = latestReportQuery.data?.artifacts[0] ?? null;
  const relatedReports = relatedReportArtifacts(artifactsQuery.data?.artifacts || []);

  return (
    <div className="stack task-detail-page">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Temporal Task Detail</h2>
          <div className="toolbar-identity-row">
            <p className="page-meta">Task {taskId || '—'}</p>
            {execution ? (
              <span
                {...executionStatusPillProps(
                  execution.rawState || execution.state || execution.status,
                )}
              >
                {execution.rawState || execution.state || execution.status || '—'}
              </span>
            ) : null}
          </div>
        </div>
        <div className="toolbar-controls">
          <label className="queue-inline-toggle toolbar-live-toggle">
            <input
              type="checkbox"
              checked={liveUpdates}
              onChange={(event) => setLiveUpdates(event.target.checked)}
            />
            Live updates
          </label>
          <span className="small">
            {liveUpdates
              ? `Polling every ${Math.round(detailPoll / 1000)}s`
              : 'Updates paused to keep selections stable.'}
          </span>
        </div>
      </div>

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
        <p className="loading">Loading task...</p>
      ) : detailQuery.isError ? (
        <div className="notice error">{(detailQuery.error as Error).message}</div>
      ) : execution ? (
        <>
          <div className="td-hero">
            <div className="td-hero-body">
              <div className="td-hero-headline">
                <h3 className="td-title-text">{execution.title}</h3>
                <span className="meta-inline">
                  Temporal
                  {execution.workflowType ? (
                    <>
                      <span className="dot">·</span>
                      {execution.workflowType}
                    </>
                  ) : null}
                  {execution.entry ? (
                    <>
                      <span className="dot">·</span>
                      {execution.entry}
                    </>
                  ) : null}
                </span>
              </div>
              <button
                type="button"
                className="td-instructions-toggle"
                aria-expanded={instructionsExpanded}
                aria-controls="task-instructions-panel"
                onClick={() => setInstructionsExpanded((current) => !current)}
              >
                {instructionsExpanded ? 'Hide instructions' : 'Show instructions'}
              </button>
            </div>
            {instructionsExpanded ? (
              <div id="task-instructions-panel" className="td-instructions-panel">
                {hasTaskInstructions ? (
                  <pre>{taskInstructions}</pre>
                ) : (
                  <p className="small">Full instructions are not available for this task.</p>
                )}
              </div>
            ) : null}
          </div>

          <div className="td-summary-block">
            <h4>Summary</h4>
            <p className="whitespace-pre-wrap">{displayedSummary}</p>
            {runSummary?.finishOutcome?.reason && runSummary.finishOutcome.reason !== displayedSummary ? (
              <p className="small" style={{ marginTop: '0.4rem' }}>
                Outcome: {runSummary.finishOutcome.reason}
              </p>
            ) : null}
          </div>

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
            </FactGroup>

            <FactGroup title="Git & Publish">
              {execution.repository ? (
                <Fact label="Repository">
                  <code className="text-xs break-all">{execution.repository}</code>
                </Fact>
              ) : null}
              {execution.publishMode ? (
                <Fact label="Publish Mode">
                  <code className="text-xs">{execution.publishMode}</code>
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
              <Fact label="Merge Automation">{execution.mergeAutomationSelected ? 'Selected' : '—'}</Fact>
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
              <Fact label="Updated">{formatWhen(execution.updatedAt)}</Fact>
              <Fact label="Closed">{formatWhen(execution.closedAt)}</Fact>
              {execution.scheduledFor ? <Fact label="Scheduled For">{formatWhen(execution.scheduledFor)}</Fact> : null}
              {execution.waitingReason ? <Fact label="Waiting Reason">{execution.waitingReason}</Fact> : null}
            </FactGroup>

            <FactGroup title="Temporal">
              <Fact label="Temporal Status">{execution.temporalStatus || '—'}</Fact>
              <Fact label="Current State">{execution.rawState || execution.state || '—'}</Fact>
              {execution.closeStatus ? <Fact label="Close Status">{execution.closeStatus}</Fact> : null}
              <Fact label="Source">Temporal</Fact>
              <Fact label="Workflow Type">{execution.workflowType || '—'}</Fact>
              <Fact label="Entry">{execution.entry || '—'}</Fact>
              <Fact label="Latest Run">
                <code className="text-xs break-all">{latestRunId || '—'}</code>
              </Fact>
              {resolvedTaskRunId ? (
                <Fact label="Task Run">
                  <code className="text-xs break-all">{resolvedTaskRunId}</code>
                </Fact>
              ) : null}
              <Fact label="Workflow ID">
                <code className="text-xs break-all">{workflowId}</code>
              </Fact>
            </FactGroup>
          </div>

          {runSummary ? (
            <section className="stack td-run-summary-region td-evidence-region">
              <h3>Run Summary</h3>
              {runSummary.finishOutcome ? (
                <div className="grid-2">
                  <Card label="Outcome Code">{runSummary.finishOutcome.code || '—'}</Card>
                  <Card label="Outcome Stage">{runSummary.finishOutcome.stage || '—'}</Card>
                </div>
              ) : null}
              {runSummary.publish ? (
                <div className="grid-2">
                  <Card label="Publish Status">{runSummary.publish.status || '—'}</Card>
                  <Card label="Publish Mode">{runSummary.publish.mode || '—'}</Card>
                </div>
              ) : null}
              {runSummary.publish?.reason ? (
                <p className="whitespace-pre-wrap">{runSummary.publish.reason}</p>
              ) : null}
              {runSummary.publishContext ? (
                <div className="grid-2">
                  {runSummary.publishContext.branch ? (
                    <Card label="Publish Branch">
                      <code className="text-xs break-all">{runSummary.publishContext.branch}</code>
                    </Card>
                  ) : null}
                  {runSummary.publishContext.baseRef ? (
                    <Card label="Base Ref">
                      <code className="text-xs break-all">{runSummary.publishContext.baseRef}</code>
                    </Card>
                  ) : null}
                  {runSummary.publishContext.commitCount !== undefined &&
                  runSummary.publishContext.commitCount !== null ? (
                    <Card label="Commit Count">{String(runSummary.publishContext.commitCount)}</Card>
                  ) : null}
                </div>
              ) : null}
              {runSummary.lastStep?.summary && runSummary.lastStep.summary !== displayedSummary ? (
                <div>
                  <strong>Last Step</strong>
                  <p className="whitespace-pre-wrap">{runSummary.lastStep.summary}</p>
                </div>
              ) : null}
              {runSummary.nextAction ? <p className="small">{runSummary.nextAction}</p> : null}
            </section>
          ) : null}

          {execution.waitingReason ? (
            <section>
              <h3>Waiting Reason</h3>
              <p>{execution.waitingReason}</p>
            </section>
          ) : null}

          {displayedMergeAutomation ? (
            <MergeAutomationPanel mergeAutomation={displayedMergeAutomation} />
          ) : null}

          {hasStepsEndpoint ? (
            <section className="stack td-steps-region td-evidence-region">
              <div className="step-tl-section-header">
                <h3>Steps</h3>
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
                <p className="loading">Loading steps...</p>
              ) : stepsQuery.isError ? (
                <div className="notice error">{(stepsQuery.error as Error).message}</div>
              ) : stepsQuery.data ? (
                <div className="step-tl-list">
                  {stepsQuery.data.steps.map((row, idx) => (
                    <StepLedgerRowCard
                      key={row.logicalStepId}
                      apiBase={payload.apiBase}
                      logStreamingEnabled={logStreamingEnabled}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                      structuredHistoryEnabled={structuredHistoryEnabled}
                      row={row}
                      runId={latestRunId}
                      expanded={Boolean(expandedSteps[row.logicalStepId])}
                      onToggle={() => toggleStep(row.logicalStepId)}
                      isLast={idx === stepsQuery.data.steps.length - 1}
                      routes={taskRunRoutes}
                    />
                  ))}
                </div>
              ) : (
                <p className="small">No step ledger available for this execution.</p>
              )}
            </section>
          ) : null}

          {execution.attentionRequired ? (
            <section className="notice">
              <strong>Attention required.</strong> This task is waiting for external input before it can continue.
            </section>
          ) : null}

          {hasDependencySection ? (
            <section className="stack">
              <div>
                <h3>Dependencies</h3>
                <p className="small">
                  Direct prerequisite runs gate this execution before planning or execution begins.
                </p>
              </div>
              <div className="grid-2">
                <Card label="Declared Prerequisites">{String(execution.dependsOn.length)}</Card>
                <Card label="Blocked On Dependencies">{execution.blockedOnDependencies ? 'Yes' : 'No'}</Card>
                <Card label="Dependency Resolution">{formatDependencyResolution(execution.dependencyResolution)}</Card>
                <Card label="Dependency Wait Duration">
                  {formatDurationMs(execution.dependencyWaitDurationMs ?? null)}
                </Card>
                {execution.failedDependencyId ? (
                  <Card label="Failed Dependency">
                    <code className="text-xs break-all">{execution.failedDependencyId}</code>
                  </Card>
                ) : null}
              </div>
              {execution.blockedOnDependencies ? (
                <div className="notice">
                  <strong>Blocked on prerequisites.</strong> This run will not advance until every prerequisite reaches <code>completed</code>.
                  {actionsOn && actions?.canBypassDependencies ? (
                    <div className="actions" style={{ marginTop: '0.75rem' }}>
                      <button
                        type="button"
                        disabled={busy}
                        className="queue-action queue-action-danger"
                        onClick={onBypassDependencies}
                      >
                        Bypass Dependency Wait
                      </button>
                    </div>
                  ) : null}
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
                      const stateLabel = outcome?.terminalState || item.state || 'unknown';
                      return (
                        <li key={item.workflowId} className="card">
                          <div className="stack gap-1">
                            <a href={dependencyHref(item.workflowId)}>
                              <strong>{item.title || item.workflowId}</strong>
                            </a>
                            <code className="text-xs break-all">{item.workflowId}</code>
                            <span {...executionStatusPillProps(stateLabel)}>{stateLabel}</span>
                            {item.summary ? <p className="small">{item.summary}</p> : null}
                            {outcome?.message ? <p className="small">{outcome.message}</p> : null}
                            {outcome?.failureCategory ? (
                              <p className="small">Failure category: <code>{outcome.failureCategory}</code></p>
                            ) : null}
                            {(outcome?.closeStatus || item.closeStatus) ? (
                              <p className="small">Close status: {outcome?.closeStatus || item.closeStatus}</p>
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
                {execution.dependents.length === 0 ? (
                  <p className="small">No downstream dependents reference this run.</p>
                ) : (
                  <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {execution.dependents.map((item) => (
                      <li key={item.workflowId} className="card">
                        <div className="stack gap-1">
                          <a href={dependencyHref(item.workflowId)}>
                            <strong>{item.title || item.workflowId}</strong>
                          </a>
                          <code className="text-xs break-all">{item.workflowId}</code>
                          <span {...executionStatusPillProps(item.state)}>{item.state || 'unknown'}</span>
                          {item.summary ? <p className="small">{item.summary}</p> : null}
                          {item.closeStatus ? <p className="small">Close status: {item.closeStatus}</p> : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}

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

          {actionsOn && actions && hasTaskActions ? (
            <section className="stack td-actions-region">
              <div>
                <h3>Task Actions</h3>
                <p className="small">Workflow editing actions stay separate from intervention controls.</p>
              </div>
              <div className="actions">
                {canCreateRemediation ? (
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
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      onClick={() => {
                        setActionError(null);
                        createRemediationMutation.mutate();
                      }}
                    >
                      Create remediation task
                    </button>
                  </div>
                ) : null}
                {actions.canSetTitle ? (
                  <button type="button" disabled={busy} className="secondary" onClick={onRename}>
                    Rename
                  </button>
                ) : null}
                {taskEditingOn && actions.canUpdateInputs && editHref ? (
                  <a
                    className="button secondary"
                    href={editHref}
                    aria-disabled={busy}
                    onClick={(event) => onTaskEditingNavigation(event, 'detail_edit_click')}
                  >
                    Edit
                  </a>
                ) : null}
                {taskEditingOn && actions.canRerun && rerunHref ? (
                  <a
                    className="button secondary"
                    href={rerunHref}
                    aria-disabled={busy}
                    onClick={(event) => onTaskEditingNavigation(event, 'detail_rerun_click')}
                  >
                    Rerun
                  </a>
                ) : null}
              </div>
            </section>
          ) : null}

          {actionsOn && actions && hasInterventionSection ? (
            <InterventionPanel
              actions={actions}
              busy={busy}
              audit={execution.interventionAudit || []}
              onPause={onPause}
              onResume={onResume}
              onApprove={onApprove}
              onCancel={onCancel}
              onReject={onReject}
              onSendMessage={onSendMessage}
            />
          ) : null}

          {resolvedTaskRunId ? (
            <SessionContinuityPanel
              apiBase={payload.apiBase}
              taskRunId={resolvedTaskRunId}
              targetRuntime={execution.targetRuntime}
              isTerminal={isTerminalExecution}
              onCancel={onCancel}
              invalidateTaskDetail={invalidate}
              cancelBusy={cancelMutation.isPending}
              routes={taskRunRoutes}
            />
          ) : null}

          <InputImagesSection
            artifacts={artifactsQuery.data?.artifacts || []}
            apiBase={payload.apiBase}
          />

          <ReportPresentationSection
            primaryReport={primaryReport}
            relatedArtifacts={relatedReports}
            apiBase={payload.apiBase}
          />

          <RemediationEvidencePanel
            artifacts={artifactsQuery.data?.artifacts || []}
            apiBase={payload.apiBase}
            showEmpty={shouldFetchRemediationLinks && artifactsQuery.isSuccess}
          />

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
                  <tr>
                    <td>Started</td>
                    <td>{formatWhen(execution.startedAt)}</td>
                    <td>Execution created.</td>
                  </tr>
                  <tr>
                    <td>Last update</td>
                    <td>{formatWhen(execution.updatedAt)}</td>
                    <td>State: {(execution.state || '').replaceAll('_', ' ')}</td>
                  </tr>
                  {execution.waitingReason || execution.attentionRequired ? (
                    <tr>
                      <td>Waiting</td>
                      <td>{formatWhen(execution.updatedAt)}</td>
                      <td>
                        {execution.waitingReason || 'Awaiting external input.'}
                        {execution.attentionRequired ? ' Attention required.' : ''}
                      </td>
                    </tr>
                  ) : null}
                  {execution.closedAt ? (
                    <tr>
                      <td>Closed</td>
                      <td>{formatWhen(execution.closedAt)}</td>
                      <td>Close status: {execution.closeStatus || execution.temporalStatus || '—'}</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="stack td-artifacts-region td-evidence-region">
            <h3>Artifacts</h3>
            {artifactsQuery.isLoading ? (
              <p className="loading">Loading artifacts...</p>
            ) : artifactsQuery.isError ? (
              <div className="notice error">{(artifactsQuery.error as Error).message}</div>
            ) : (
              <div className="queue-table-wrapper td-evidence-slab" data-layout="table">
                <table>
                  <thead>
                    <tr>
                      <th>Artifact</th>
                      <th>Size</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(artifactsQuery.data?.artifacts || []).length === 0 ? (
                      <tr>
                        <td colSpan={4}>No artifacts.</td>
                      </tr>
                    ) : (
                      (artifactsQuery.data?.artifacts || []).map((artifact) => (
                        <tr key={artifact.artifactId}>
                          <td>
                            <code>{artifact.artifactId}</code>
                          </td>
                          <td>{artifact.sizeBytes ?? '—'}</td>
                          <td>{String(artifact.status ?? '—')}</td>
                          <td>
                            <a
                              className="button secondary"
                              href={artifactDownloadHref(payload.apiBase, artifact)}
                              title="Download artifact"
                            >
                              Download
                            </a>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {showExecutionObservationFallback ? (
            <section className="stack td-observation-region td-evidence-region">
              <div>
                <h3>Observation</h3>
                <p className="small">
                  Live logs are passive observation only. Use the Intervention panel for control actions.
                </p>
              </div>
              {logStreamingEnabled ? (
                resolvedTaskRunId ? (
                  <>
                    {showTaskRunAttachNotice ? (
                      <p className="small">Waiting for managed runtime launch to create live logs.</p>
                    ) : null}
                    <LiveLogsPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      isTerminal={isTerminalExecution}
                      autoExpand={showTaskRunAttachNotice}
                      routes={taskRunRoutes}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                      structuredHistoryEnabled={structuredHistoryEnabled}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      stream="stdout"
                      routes={taskRunRoutes}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      stream="stderr"
                      routes={taskRunRoutes}
                    />
                    <DiagnosticsPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      routes={taskRunRoutes}
                    />
                  </>
                ) : (
                  <>
                    <h3>Live Logs</h3>
                    <p className="small">{missingTaskRunState ? renderMissingTaskRunCopy(missingTaskRunState) : 'Waiting for task details...'}</p>
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

          {debugOn && execution.debugFields ? (
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
        <p>No task details.</p>
      )}
    </div>
  );
}
export default TaskDetailPage;
