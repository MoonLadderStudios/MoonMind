export const INTEGRATION_STATUS_KEYS = [
  'queued',
  'running',
  'completed',
  'failed',
  'canceled',
  'unknown',
] as const;

export type IntegrationStatusKey = (typeof INTEGRATION_STATUS_KEYS)[number];
type IntegrationStatusLabelKey = IntegrationStatusKey | 'awaiting_feedback';

const INTEGRATION_STATUS_LABELS: Record<IntegrationStatusLabelKey, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
  unknown: 'Unknown',
  awaiting_feedback: 'Awaiting feedback',
};

const INTEGRATION_STATUS_CLASSES: Record<IntegrationStatusLabelKey, string> = {
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

function isIntegrationStatusKey(key: string): key is IntegrationStatusLabelKey {
  return Object.prototype.hasOwnProperty.call(INTEGRATION_STATUS_LABELS, key);
}

export function isIntegrationStatus(status: string | null | undefined): boolean {
  return isIntegrationStatusKey(normalizedIntegrationStatusKey(status));
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
  if (isIntegrationStatusKey(key)) {
    return INTEGRATION_STATUS_LABELS[key];
  }
  warnUnknownIntegrationStatus(key);
  return fallback;
}

export function integrationStatusPillProps(status: string | null | undefined): Readonly<{ className: string }> {
  const key = normalizedIntegrationStatusKey(status);
  const className = isIntegrationStatusKey(key) ? INTEGRATION_STATUS_CLASSES[key] : 'status status-neutral';
  if (!isIntegrationStatusKey(key)) {
    warnUnknownIntegrationStatus(key);
  }
  return { className };
}
