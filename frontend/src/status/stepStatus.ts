const STEP_STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  ready: 'Ready',
  running: 'Running',
  awaiting_external: 'Awaiting external',
  reviewing: 'Reviewing',
  succeeded: 'Succeeded',
  failed: 'Failed',
  skipped: 'Skipped',
  canceled: 'Canceled',
};

export function formatStepStatusLabel(
  status: string | null | undefined,
  fallback = '-',
): string {
  const key = String(status || '').toLowerCase().trim().replace(/\s+/g, '_');
  if (!key) return fallback;
  return STEP_STATUS_LABELS[key] || fallback;
}

