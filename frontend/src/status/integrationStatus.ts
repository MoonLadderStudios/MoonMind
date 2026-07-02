export const INTEGRATION_STATUS_KEYS = [
  'queued',
  'running',
  'completed',
  'failed',
  'canceled',
  'unknown',
] as const;

export type IntegrationStatusKey = (typeof INTEGRATION_STATUS_KEYS)[number];

const INTEGRATION_STATUS_LABELS: Record<IntegrationStatusKey | 'awaiting_feedback', string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
  unknown: 'Unknown',
  awaiting_feedback: 'Awaiting feedback',
};

function isIntegrationStatusKey(
  key: string,
): key is IntegrationStatusKey | 'awaiting_feedback' {
  return Object.prototype.hasOwnProperty.call(INTEGRATION_STATUS_LABELS, key);
}

export function formatIntegrationStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  if (!key) return fallback;
  return isIntegrationStatusKey(key) ? INTEGRATION_STATUS_LABELS[key] : fallback;
}
