export const STEP_LEDGER_STATUS_KEYS = [
  'pending',
  'ready',
  'executing',
  'awaiting_external',
  'reviewing',
  'completed',
  'failed',
  'skipped',
  'canceled',
] as const;

export const STEP_EXECUTION_STATUS_KEYS = [
  'pending',
  'preparing',
  'executing',
  'running',
  'checking',
  'completed',
  'succeeded',
  'failed',
  'blocked',
  'canceled',
  'superseded',
] as const;

export type StepLedgerStatusKey = (typeof STEP_LEDGER_STATUS_KEYS)[number];
export type StepExecutionStatusKey = (typeof STEP_EXECUTION_STATUS_KEYS)[number];
type StepStatusKey = StepLedgerStatusKey | StepExecutionStatusKey;

const STEP_STATUS_LABELS: Record<StepStatusKey, string> = {
  pending: 'Pending',
  ready: 'Ready',
  preparing: 'Preparing',
  executing: 'Executing',
  running: 'Running',
  checking: 'Checking',
  awaiting_external: 'Awaiting external',
  reviewing: 'Reviewing',
  completed: 'Completed',
  succeeded: 'Succeeded',
  failed: 'Failed',
  blocked: 'Blocked',
  skipped: 'Skipped',
  canceled: 'Canceled',
  superseded: 'Superseded',
};

const STEP_STATUS_CLASSES: Record<StepStatusKey, string> = {
  pending: 'status status-scheduled',
  ready: 'status status-scheduled',
  preparing: 'status status-running',
  executing: 'status status-running',
  running: 'status status-running',
  checking: 'status status-awaiting-external',
  awaiting_external: 'status status-awaiting-external',
  reviewing: 'status status-awaiting-external',
  completed: 'status status-completed',
  succeeded: 'status status-succeeded',
  failed: 'status status-failed',
  blocked: 'status status-awaiting-external',
  skipped: 'status status-neutral',
  canceled: 'status status-canceled',
  superseded: 'status status-neutral',
};

function isStepStatusKey(key: string): key is StepStatusKey {
  return Object.prototype.hasOwnProperty.call(STEP_STATUS_LABELS, key);
}

export function formatStepStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  if (!key) return fallback;
  return isStepStatusKey(key) ? STEP_STATUS_LABELS[key] : fallback;
}

export function stepStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  const className = isStepStatusKey(key) ? STEP_STATUS_CLASSES[key] : 'status status-neutral';
  return { className };
}
