export const EXECUTING_STATUS_PILL_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
  relatedJiraIssues: [
    'MM-489',
    'MM-490',
    'MM-491',
    'MM-704',
    'MM-1035',
    'MM-1036',
    'MM-1073',
    'MM-1083',
  ],
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

const SHIMMER_SWEEP_KEYS = ['executing', 'running', 'initializing', 'planning', 'finalizing'] as const;
type ShimmerSweepStatusKey = (typeof SHIMMER_SWEEP_KEYS)[number];

export type ExecutionStatusPillProps = Readonly<{
  className: string;
  'data-state'?: ShimmerSweepStatusKey;
  'data-effect'?: 'shimmer-sweep';
  'data-shimmer-label'?: string;
}>;

export type ExecutionStatusPillOptions = Readonly<{
  enableMotion?: boolean;
}>;

export type StatusPillView = Readonly<{
  label: string;
  pillProps: ExecutionStatusPillProps;
}>;

export const WORKFLOW_LIFECYCLE_STATUSES = [
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

export const STEP_LEDGER_STATUSES = [
  'pending',
  'ready',
  'running',
  'awaiting_external',
  'reviewing',
  'succeeded',
  'failed',
  'skipped',
  'canceled',
] as const;

const WORKFLOW_LABELS = {
  scheduled: 'Scheduled',
  initializing: 'Initializing',
  waiting_on_dependencies: 'Waiting on dependencies',
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
} as const satisfies Record<WorkflowLifecycleStatus, string>;

const STEP_LABELS = {
  pending: 'Pending',
  ready: 'Ready',
  running: 'Running',
  awaiting_external: 'Awaiting external',
  reviewing: 'Reviewing',
  succeeded: 'Succeeded',
  failed: 'Failed',
  skipped: 'Skipped',
  canceled: 'Canceled',
} as const satisfies Record<StepLedgerStatus, string>;

const INTEGRATION_LABELS: Record<string, string> = {
  no_changes: 'No commit',
  no_commit: 'No commit',
  succeeded: 'Succeeded',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
  cancelled: 'Canceled',
  skipped: 'Skipped',
  running: 'Running',
  executing: 'Executing',
  pending: 'Pending',
  queued: 'Queued',
  scheduling: 'Scheduling',
  awaiting_action: 'Awaiting action',
  waiting: 'Waiting',
};

type WorkflowLifecycleStatus = (typeof WORKFLOW_LIFECYCLE_STATUSES)[number];
type StepLedgerStatus = (typeof STEP_LEDGER_STATUSES)[number];
type StatusDomain = 'workflow lifecycle' | 'step ledger' | 'integration/provider';

const WORKFLOW_STATUS_SET = new Set<string>(WORKFLOW_LIFECYCLE_STATUSES);
const STEP_STATUS_SET = new Set<string>(STEP_LEDGER_STATUSES);
const SHIMMER_SWEEP_STATUS_KEYS = new Set<string>(SHIMMER_SWEEP_KEYS);

function normalizedStatusKey(status: string | null | undefined): string {
  return String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
}

function statusClassName(key: string): string {
  return `status-${key.replace(/_/g, '-')}`;
}

function warnUnknownStatus(domain: StatusDomain, key: string): void {
  console.warn(`[MoonMind] Unknown ${domain} status "${key || '(empty)'}"; rendering neutral status pill.`);
}

function fallbackLabel(key: string, fallback = 'Unknown'): string {
  if (!key) return fallback;
  return key
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^./, (first) => first.toUpperCase());
}

function workflowStatusClass(key: WorkflowLifecycleStatus): string {
  if (key === 'completed') return 'status-completed';
  if (key === 'executing' || key === 'proposals') return 'status-running';
  return statusClassName(key);
}

function stepStatusClass(key: StepLedgerStatus): string {
  if (key === 'succeeded') return 'status-succeeded';
  if (key === 'running') return 'status-running';
  return statusClassName(key);
}

function integrationStatusClass(key: string): string {
  if (key === 'no_commit' || key === 'no_changes') return 'status-no-commit';
  if (key === 'succeeded' || key === 'completed') return 'status-completed';
  if (key === 'failed') return 'status-failed';
  if (key === 'canceled' || key === 'cancelled') return 'status-canceled';
  if (key === 'skipped') return 'status-skipped';
  if (key === 'running' || key === 'executing') return 'status-running';
  if (key === 'queued' || key === 'scheduling') return 'status-queued';
  if (key === 'awaiting_action' || key === 'waiting') return statusClassName(key);
  if (key === 'pending') return 'status-pending';
  return 'status-neutral';
}

function isShimmerSweepStatusKey(key: string): key is ShimmerSweepStatusKey {
  return SHIMMER_SWEEP_STATUS_KEYS.has(key);
}

function withMotionProps(
  key: string,
  label: string,
  className: string,
  options: ExecutionStatusPillOptions,
): ExecutionStatusPillProps {
  if (options.enableMotion !== false && isShimmerSweepStatusKey(key)) {
    return {
      className: `${className} is-${key}`,
      'data-state': key,
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': label,
    };
  }
  return { className };
}

export function isWorkflowLifecycleStatus(status: string | null | undefined): status is WorkflowLifecycleStatus {
  return WORKFLOW_STATUS_SET.has(normalizedStatusKey(status));
}

export function isStepLedgerStatus(status: string | null | undefined): status is StepLedgerStatus {
  return STEP_STATUS_SET.has(normalizedStatusKey(status));
}

export function workflowLifecycleStatusPillView(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): StatusPillView {
  const key = normalizedStatusKey(status);
  if (!isWorkflowLifecycleStatus(key)) {
    warnUnknownStatus('workflow lifecycle', key);
    return {
      label: fallbackLabel(key),
      pillProps: { className: 'status status-neutral' },
    };
  }

  const label = WORKFLOW_LABELS[key];
  const className = `status ${workflowStatusClass(key)}`;
  return {
    label,
    pillProps: withMotionProps(key, label, className, options),
  };
}

export function stepLedgerStatusPillView(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): StatusPillView {
  const key = normalizedStatusKey(status);
  if (!isStepLedgerStatus(key)) {
    warnUnknownStatus('step ledger', key);
    return {
      label: fallbackLabel(key),
      pillProps: { className: 'status status-neutral' },
    };
  }

  const label = STEP_LABELS[key];
  const className = `status ${stepStatusClass(key)}`;
  return {
    label,
    pillProps: withMotionProps(key, label, className, options),
  };
}

export function integrationProviderStatusPillView(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): StatusPillView {
  const key = normalizedStatusKey(status);
  const label = INTEGRATION_LABELS[key];
  if (!label) {
    warnUnknownStatus('integration/provider', key);
    return {
      label: fallbackLabel(key),
      pillProps: { className: 'status status-neutral' },
    };
  }

  const className = `status ${integrationStatusClass(key)}`;
  return {
    label,
    pillProps: withMotionProps(key, label, className, options),
  };
}

export function workflowLifecycleStatusPillProps(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): ExecutionStatusPillProps {
  return workflowLifecycleStatusPillView(status, options).pillProps;
}

export function stepLedgerStatusPillProps(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): ExecutionStatusPillProps {
  return stepLedgerStatusPillView(status, options).pillProps;
}

export function integrationProviderStatusPillProps(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): ExecutionStatusPillProps {
  return integrationProviderStatusPillView(status, options).pillProps;
}
