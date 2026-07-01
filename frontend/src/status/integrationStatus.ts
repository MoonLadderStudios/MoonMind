const INTEGRATION_STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
  unknown: 'Unknown',
  awaiting_feedback: 'Awaiting feedback',
};

export function formatIntegrationStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  if (!key) return fallback;
  return INTEGRATION_STATUS_LABELS[key] || fallback;
}

