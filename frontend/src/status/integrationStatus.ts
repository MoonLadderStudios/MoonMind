const INTEGRATION_STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
  unknown: 'Unknown',
  awaiting_feedback: 'Awaiting feedback',
};

const INTEGRATION_STATUS_CLASSES: Record<string, string> = {
  queued: 'status status-scheduled',
  running: 'status status-running',
  completed: 'status status-completed',
  failed: 'status status-failed',
  canceled: 'status status-canceled',
  unknown: 'status status-neutral',
  awaiting_feedback: 'status status-awaiting-external',
};

function normalizedIntegrationStatusKey(status: string | null | undefined): string {
  return String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
}

function warnUnknownIntegrationStatus(key: string): void {
  if (key) {
    console.warn(`Unknown integration/provider status: ${key}`);
  }
}

export function formatIntegrationStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = normalizedIntegrationStatusKey(status);
  if (!key) return fallback;
  if (Object.prototype.hasOwnProperty.call(INTEGRATION_STATUS_LABELS, key)) {
    return INTEGRATION_STATUS_LABELS[key]!;
  }
  warnUnknownIntegrationStatus(key);
  return fallback;
}

export function integrationStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = normalizedIntegrationStatusKey(status);
  const known = Object.prototype.hasOwnProperty.call(INTEGRATION_STATUS_CLASSES, key);
  const className = known ? INTEGRATION_STATUS_CLASSES[key]! : 'status status-neutral';
  if (!known) {
    warnUnknownIntegrationStatus(key);
  }
  return { className };
}
