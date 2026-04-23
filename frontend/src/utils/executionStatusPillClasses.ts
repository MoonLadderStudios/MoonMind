export const EXECUTING_STATUS_PILL_TRACEABILITY = Object.freeze({
  jiraIssue: 'MM-488',
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

export type ExecutionStatusPillProps = Readonly<{
  className: string;
  'data-state'?: 'executing';
  'data-effect'?: 'shimmer-sweep';
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

export function executionStatusPillProps(status: string | null | undefined): ExecutionStatusPillProps {
  const key = normalizedExecutionStatusKey(status);
  const className = executionStatusBaseClasses(key);

  if (key === 'executing') {
    return {
      className: `${className} is-executing`,
      'data-state': 'executing',
      'data-effect': 'shimmer-sweep',
    };
  }

  return { className };
}

export function executionStatusPillClasses(status: string | null | undefined): string {
  return executionStatusPillProps(status).className;
}
