export function executionStatusPillClasses(status: string | null | undefined): string {
  const key = String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
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
  return 'status status-neutral';
}
