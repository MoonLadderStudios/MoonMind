export function formatTaskSkills(
  taskSkills: string[] | null | undefined,
  skillId: string | null | undefined
): string {
  if (taskSkills?.length) {
    return taskSkills.join(', ');
  }
  return skillId || '—';
}

const STATUS_DISPLAY_NAMES: Record<string, string> = {
  checkpoint_validation: 'Checkpoint validation',
  failed_step_execution: 'Failed step execution',
  preserved_output_injection: 'Preserved output injection',
  workspace_restoration: 'Workspace restoration',
};

export function formatStatusLabel(status: string | null | undefined, fallback = '—'): string {
  const raw = String(status || fallback).trim();
  if (!raw) return fallback;
  const key = raw.toLowerCase().replace(/[\s_]+/g, '_');
  if (Object.prototype.hasOwnProperty.call(STATUS_DISPLAY_NAMES, key)) {
    return STATUS_DISPLAY_NAMES[key]!;
  }
  return raw.replace(/_/g, ' ').replace(/\s+/g, ' ');
}

const RUNTIME_DISPLAY_NAMES: Record<string, string> = {
  codex_cli: 'Codex CLI',
  codex: 'Codex CLI',
  claude_code: 'Claude Code',
  claude: 'Claude Code',
  jules: 'Jules',
  codex_cloud: 'Codex Cloud',
};

function titleizeRuntime(runtime: string): string {
  return runtime
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((part) => {
      if (part === 'cli') return 'CLI';
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(' ');
}

export function formatRuntimeLabel(runtime: string | null | undefined): string {
  const key = runtime?.trim().toLowerCase();
  if (!key) return '—';
  return RUNTIME_DISPLAY_NAMES[key] || titleizeRuntime(key);
}

export function formatDurationMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (value < 1000) return `${Math.max(0, Math.round(value))} ms`;
  const totalSeconds = Math.round(value / 1000);
  if (totalSeconds < 60) {
    const seconds = value / 1000;
    return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} s`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  if (totalMinutes < 60) {
    return `${totalMinutes}m ${remainingSeconds}s`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}
