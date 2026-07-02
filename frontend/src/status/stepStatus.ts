const STEP_STATUS_LABELS: Record<string, string> = {
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
  skipped: 'Skipped',
  canceled: 'Canceled',
};

const STEP_STATUS_CLASSES: Record<string, string> = {
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
  skipped: 'status status-neutral',
  canceled: 'status status-canceled',
};

function normalizedStepStatusKey(status: string | null | undefined): string {
  return String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
}

export function isStepLedgerStatus(status: string | null | undefined): boolean {
  const key = normalizedStepStatusKey(status);
  return Object.prototype.hasOwnProperty.call(STEP_STATUS_LABELS, key);
}

function warnUnknownStepStatus(key: string): void {
  if (key) {
    console.warn(`Unknown step ledger status: ${key}`);
  }
}

export function formatStepStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = normalizedStepStatusKey(status);
  if (!key) return fallback;
  if (Object.prototype.hasOwnProperty.call(STEP_STATUS_LABELS, key)) {
    return STEP_STATUS_LABELS[key]!;
  }
  warnUnknownStepStatus(key);
  return fallback;
}

export function stepStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = normalizedStepStatusKey(status);
  const known = Object.prototype.hasOwnProperty.call(STEP_STATUS_CLASSES, key);
  const className = known ? STEP_STATUS_CLASSES[key]! : 'status status-neutral';
  if (!known) {
    warnUnknownStepStatus(key);
  }
  return { className };
}
