export const WORKFLOW_STATUS_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
  relatedJiraIssues: ['MM-489', 'MM-490', 'MM-491', 'MM-704', 'MM-1035', 'MM-1036', 'MM-1073', 'MM-1083'],
  designRequirements: [
    'DESIGN-REQ-001',
    'DESIGN-REQ-002',
    'DESIGN-REQ-003',
    'DESIGN-REQ-004',
    'DESIGN-REQ-011',
    'DESIGN-REQ-013',
    'DESIGN-REQ-016',
  ],
});

export const WORKFLOW_STATUS_KEYS = [
  'scheduled',
  'initializing',
  'waiting_on_dependencies',
  'planning',
  'awaiting_slot',
  'executing',
  'awaiting_external',
  'proposals',
  'finalizing',
  'no_commit',
  'completed',
  'failed',
  'canceled',
] as const;

export type WorkflowStatusKey = (typeof WORKFLOW_STATUS_KEYS)[number];

const WORKFLOW_STATUS_LABELS: Record<WorkflowStatusKey, string> = {
  scheduled: 'Scheduled',
  initializing: 'Initializing',
  waiting_on_dependencies: 'Awaiting dependencies',
  planning: 'Planning',
  awaiting_slot: 'Awaiting slot',
  executing: 'Executing',
  awaiting_external: 'Awaiting external',
  proposals: 'Proposals',
  finalizing: 'Finalizing',
  no_commit: 'No commit',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
};

const WORKFLOW_COMPATIBILITY_ALIASES: Record<string, WorkflowStatusKey> = {
  no_changes: 'no_commit',
  running: 'executing',
};

const WORKFLOW_STATUS_CLASSES: Record<WorkflowStatusKey, string> = {
  scheduled: 'status status-scheduled',
  initializing: 'status status-initializing',
  waiting_on_dependencies: 'status status-awaiting-dependencies',
  planning: 'status status-planning',
  awaiting_slot: 'status status-awaiting-slot',
  executing: 'status status-running',
  awaiting_external: 'status status-awaiting-external',
  proposals: 'status status-running',
  finalizing: 'status status-finalizing',
  no_commit: 'status status-no-commit',
  completed: 'status status-completed',
  failed: 'status status-failed',
  canceled: 'status status-canceled',
};

const SHIMMER_SWEEP_KEYS = ['executing', 'initializing', 'planning', 'finalizing'] as const;
type ShimmerSweepStatusKey = (typeof SHIMMER_SWEEP_KEYS)[number];

export type WorkflowStatusPillProps = Readonly<{
  className: string;
  'data-state'?: ShimmerSweepStatusKey;
  'data-effect'?: 'shimmer-sweep';
  'data-shimmer-label'?: string;
}>;

export type WorkflowStatusPillOptions = Readonly<{
  enableMotion?: boolean;
}>;

export function normalizedWorkflowStatusKey(status: string | null | undefined): string {
  return String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
}

function isWorkflowStatusKey(key: string): key is WorkflowStatusKey {
  return Object.prototype.hasOwnProperty.call(WORKFLOW_STATUS_LABELS, key);
}

function canonicalWorkflowStatusKey(status: string | null | undefined): string {
  const key = normalizedWorkflowStatusKey(status);
  if (Object.prototype.hasOwnProperty.call(WORKFLOW_COMPATIBILITY_ALIASES, key)) {
    return WORKFLOW_COMPATIBILITY_ALIASES[key]!;
  }
  return key;
}

export function isWorkflowLifecycleStatus(status: string | null | undefined): boolean {
  return isWorkflowStatusKey(canonicalWorkflowStatusKey(status));
}

function warnUnknownWorkflowStatus(key: string): void {
  if (key) {
    console.warn(`Unknown workflow lifecycle status: ${key}`);
  }
}

export function resolveWorkflowDisplayStatus(
  ...candidates: Array<string | null | undefined>
): WorkflowStatusKey | null {
  for (const candidate of candidates) {
    const key = canonicalWorkflowStatusKey(candidate);
    if (isWorkflowStatusKey(key)) {
      return key;
    }
  }
  return null;
}

export function formatWorkflowStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = canonicalWorkflowStatusKey(status);
  if (!key) return fallback;
  if (isWorkflowStatusKey(key)) {
    return WORKFLOW_STATUS_LABELS[key];
  }
  warnUnknownWorkflowStatus(key);
  return fallback;
}

const SHIMMER_SWEEP_STATUS_KEYS = new Set<string>(SHIMMER_SWEEP_KEYS);

function isShimmerSweepStatusKey(key: string): key is ShimmerSweepStatusKey {
  return SHIMMER_SWEEP_STATUS_KEYS.has(key);
}

export function workflowStatusPillProps(
  status: string | null | undefined,
  options: WorkflowStatusPillOptions = {},
): WorkflowStatusPillProps {
  const key = canonicalWorkflowStatusKey(status);
  const className = isWorkflowStatusKey(key) ? WORKFLOW_STATUS_CLASSES[key] : 'status status-neutral';

  if (!isWorkflowStatusKey(key)) {
    warnUnknownWorkflowStatus(key);
  }

  if (options.enableMotion !== false && isShimmerSweepStatusKey(key)) {
    return {
      className: `${className} is-${key}`,
      'data-state': key,
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': formatWorkflowStatusLabel(status, 'Executing'),
    };
  }

  return { className };
}
