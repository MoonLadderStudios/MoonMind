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

const STEP_LEDGER_STATUS_LABELS: Record<StepLedgerStatusKey, string> = {
  pending: 'Pending',
  ready: 'Ready',
  executing: 'Executing',
  awaiting_external: 'Awaiting external',
  reviewing: 'Reviewing',
  completed: 'Completed',
  failed: 'Failed',
  skipped: 'Skipped',
  canceled: 'Canceled',
};

const STEP_LEDGER_STATUS_CLASSES: Record<StepLedgerStatusKey, string> = {
  pending: 'status status-scheduled',
  ready: 'status status-scheduled',
  executing: 'status status-running',
  awaiting_external: 'status status-awaiting-external',
  reviewing: 'status status-awaiting-external',
  completed: 'status status-completed',
  failed: 'status status-failed',
  skipped: 'status status-neutral',
  canceled: 'status status-canceled',
};

const STEP_EXECUTION_STATUS_LABELS: Record<StepExecutionStatusKey, string> = {
  pending: 'Pending',
  preparing: 'Preparing',
  executing: 'Executing',
  running: 'Running',
  checking: 'Checking',
  completed: 'Completed',
  succeeded: 'Succeeded',
  failed: 'Failed',
  blocked: 'Blocked',
  canceled: 'Canceled',
  superseded: 'Superseded',
};

const STEP_EXECUTION_STATUS_CLASSES: Record<StepExecutionStatusKey, string> = {
  pending: 'status status-scheduled',
  preparing: 'status status-scheduled',
  executing: 'status status-running',
  running: 'status status-running',
  checking: 'status status-running',
  completed: 'status status-completed',
  succeeded: 'status status-completed',
  failed: 'status status-failed',
  blocked: 'status status-failed',
  canceled: 'status status-canceled',
  superseded: 'status status-neutral',
};

function normalizedStepStatusKey(status: string | null | undefined): string {
  return String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
}

function isStepLedgerStatusKey(key: string): key is StepLedgerStatusKey {
  return Object.prototype.hasOwnProperty.call(STEP_LEDGER_STATUS_LABELS, key);
}

function isStepExecutionStatusKey(key: string): key is StepExecutionStatusKey {
  return Object.prototype.hasOwnProperty.call(STEP_EXECUTION_STATUS_LABELS, key);
}

export function isStepLedgerStatus(status: string | null | undefined): boolean {
  return isStepLedgerStatusKey(normalizedStepStatusKey(status));
}

function warnUnknownStepStatus(key: string): void {
  if (key) {
    console.warn(`Unknown step ledger status: ${key}`);
  }
}

function warnUnknownStepExecutionStatus(key: string): void {
  if (key) {
    console.warn(`Unknown step execution status: ${key}`);
  }
}

export function formatStepStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = normalizedStepStatusKey(status);
  if (!key) return fallback;
  if (isStepLedgerStatusKey(key)) {
    return STEP_LEDGER_STATUS_LABELS[key];
  }
  warnUnknownStepStatus(key);
  return fallback;
}

export function formatStepExecutionStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = normalizedStepStatusKey(status);
  if (!key) return fallback;
  if (isStepExecutionStatusKey(key)) {
    return STEP_EXECUTION_STATUS_LABELS[key];
  }
  warnUnknownStepExecutionStatus(key);
  return fallback;
}

export function stepStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = normalizedStepStatusKey(status);
  const className = isStepLedgerStatusKey(key) ? STEP_LEDGER_STATUS_CLASSES[key] : 'status status-neutral';
  if (!isStepLedgerStatusKey(key)) {
    warnUnknownStepStatus(key);
  }
  return { className };
}

export function stepExecutionStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = normalizedStepStatusKey(status);
  const className = isStepExecutionStatusKey(key) ? STEP_EXECUTION_STATUS_CLASSES[key] : 'status status-neutral';
  if (!isStepExecutionStatusKey(key)) {
    warnUnknownStepExecutionStatus(key);
  }
  return { className };
}
