import { formatStatusLabel } from './formatters';

export const EXECUTING_STATUS_PILL_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
  relatedJiraIssues: ['MM-489', 'MM-490', 'MM-491'],
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

const SHIMMER_SWEEP_KEYS = ['executing', 'planning', 'running', 'initializing', 'finalizing'] as const;
type ShimmerSweepStatusKey = (typeof SHIMMER_SWEEP_KEYS)[number];

export type ExecutionStatusPillProps = Readonly<{
  className: string;
  'data-state'?: ShimmerSweepStatusKey;
  'data-effect'?: 'shimmer-sweep';
  'data-shimmer-label'?: string;
}>;

function normalizedExecutionStatusKey(status: string | null | undefined): string {
  return String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
}

function executionStatusBaseClasses(key: string): string {
  if (key === 'succeeded' || key === 'completed') return 'status status-completed';
  if (key === 'failed') return 'status status-failed';
  if (key === 'canceled' || key === 'cancelled') return 'status status-cancelled';
  if (key === 'queued' || key === 'scheduling') return 'status status-queued';
  if (
    key === 'running' ||
    key === 'executing' ||
    key === 'planning' ||
    key === 'initializing' ||
    key === 'finalizing'
  ) {
    return 'status status-running';
  }
  if (key === 'awaiting_action' || key === 'awaiting_external') return 'status status-awaiting_action';
  if (key === 'waiting' || key === 'waiting_on_dependencies') return 'status status-waiting';
  return 'status status-neutral';
}

function executionStatusVisibleLabel(status: string | null | undefined): string {
  return formatStatusLabel(status, 'executing');
}

const SHIMMER_SWEEP_STATUS_KEYS = new Set<string>(SHIMMER_SWEEP_KEYS);

function isShimmerSweepStatusKey(key: string): key is ShimmerSweepStatusKey {
  return SHIMMER_SWEEP_STATUS_KEYS.has(key);
}

export function executionStatusPillProps(status: string | null | undefined): ExecutionStatusPillProps {
  const key = normalizedExecutionStatusKey(status);
  const className = executionStatusBaseClasses(key);

  if (isShimmerSweepStatusKey(key)) {
    return {
      className: `${className} is-${key}`,
      'data-state': key,
      'data-effect': 'shimmer-sweep',
      'data-shimmer-label': executionStatusVisibleLabel(status),
    };
  }

  return { className };
}
