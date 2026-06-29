import { formatStatusLabel } from './formatters';

export const EXECUTING_STATUS_PILL_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
  relatedJiraIssues: ['MM-489', 'MM-490', 'MM-491', 'MM-704', 'MM-1035'],
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

const SHIMMER_SWEEP_KEYS = ['executing', 'running'] as const;
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

function normalizedExecutionStatusKey(status: string | null | undefined): string {
  return String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
}

function executionStatusBaseClasses(key: string): string {
  if (key === 'no_commit' || key === 'no_changes') return 'status status-no-commit';
  if (key === 'succeeded' || key === 'completed') return 'status status-completed';
  if (key === 'failed') return 'status status-failed';
  if (key === 'canceled') return 'status status-canceled';
  if (key === 'scheduled') return 'status status-scheduled';
  if (key === 'awaiting_slot') return 'status status-awaiting-slot';
  if (key === 'waiting_on_dependencies') return 'status status-awaiting-dependencies';
  if (key === 'awaiting_external') return 'status status-awaiting-external';
  if (key === 'initializing') return 'status status-initializing';
  if (key === 'planning') return 'status status-planning';
  if (key === 'finalizing') return 'status status-finalizing';
  if (key === 'queued' || key === 'scheduling') return 'status status-queued';
  if (
    key === 'running' ||
    key === 'executing' ||
    key === 'proposals'
  ) {
    return 'status status-running';
  }
  if (key === 'awaiting_action') return 'status status-awaiting_action';
  if (key === 'waiting') return 'status status-waiting';
  return 'status status-neutral';
}

function executionStatusVisibleLabel(status: string | null | undefined): string {
  return formatStatusLabel(status, 'executing');
}

const SHIMMER_SWEEP_STATUS_KEYS = new Set<string>(SHIMMER_SWEEP_KEYS);

function isShimmerSweepStatusKey(key: string): key is ShimmerSweepStatusKey {
  return SHIMMER_SWEEP_STATUS_KEYS.has(key);
}

export function executionStatusPillProps(
  status: string | null | undefined,
  options: ExecutionStatusPillOptions = {},
): ExecutionStatusPillProps {
  const key = normalizedExecutionStatusKey(status);
  const className = executionStatusBaseClasses(key);

  if (options.enableMotion !== false && isShimmerSweepStatusKey(key)) {
    return {
      className: `${className} is-${key}`,
      'data-state': key,
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': executionStatusVisibleLabel(status),
    };
  }

  return { className };
}
