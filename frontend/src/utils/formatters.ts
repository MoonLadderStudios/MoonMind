export function formatTaskSkills(
  taskSkills: string[] | null | undefined,
  skillId: string | null | undefined
): string {
  if (taskSkills?.length) {
    return taskSkills.join(', ');
  }
  return skillId || '—';
}

const RUNTIME_DISPLAY_NAMES: Record<string, string> = {
  codex_cli: 'Codex CLI',
  codex: 'Codex CLI',
  gemini_cli: 'Gemini CLI',
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
