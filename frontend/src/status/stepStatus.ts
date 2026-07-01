const STEP_STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  ready: 'Ready',
  preparing: 'Preparing',
  running: 'Running',
  checking: 'Checking',
  awaiting_external: 'Awaiting external',
  reviewing: 'Reviewing',
  succeeded: 'Succeeded',
  failed: 'Failed',
  skipped: 'Skipped',
  canceled: 'Canceled',
};

const STEP_STATUS_CLASSES: Record<string, string> = {
  pending: 'status status-scheduled',
  ready: 'status status-scheduled',
  preparing: 'status status-running',
  running: 'status status-running',
  checking: 'status status-awaiting-external',
  awaiting_external: 'status status-awaiting-external',
  reviewing: 'status status-awaiting-external',
  succeeded: 'status status-succeeded',
  failed: 'status status-failed',
  skipped: 'status status-neutral',
  canceled: 'status status-canceled',
};

export function formatStepStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  if (!key) return fallback;
  return Object.prototype.hasOwnProperty.call(STEP_STATUS_LABELS, key)
    ? STEP_STATUS_LABELS[key]!
    : fallback;
}

export function stepStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  const className = Object.prototype.hasOwnProperty.call(STEP_STATUS_CLASSES, key)
    ? STEP_STATUS_CLASSES[key]!
    : 'status status-neutral';
  return { className };
}
