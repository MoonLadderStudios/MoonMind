export const WORKFLOW_STATUS_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
  relatedJiraIssues: ['MM-489', 'MM-490', 'MM-491', 'MM-704', 'MM-1035', 'MM-1036', 'MM-1073'],
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

const WORKFLOW_STATUS_LABELS: Record<string, string> = {
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

const WORKFLOW_STATUS_CLASSES: Record<string, string> = {
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

export function formatWorkflowStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = normalizedWorkflowStatusKey(status);
  if (!key) return fallback;
  if (Object.prototype.hasOwnProperty.call(WORKFLOW_STATUS_LABELS, key)) {
    return WORKFLOW_STATUS_LABELS[key]!;
  }
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
  const key = normalizedWorkflowStatusKey(status);
  const className = Object.prototype.hasOwnProperty.call(WORKFLOW_STATUS_CLASSES, key)
    ? WORKFLOW_STATUS_CLASSES[key]!
    : 'status status-neutral';

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
