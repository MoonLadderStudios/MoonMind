const RUNTIME_DISPLAY_NAMES: Record<string, string> = {
  codex_cli: 'Codex CLI',
  claude_code: 'Claude Code',
  gemini_cli: 'Gemini CLI',
  jules: 'Jules',
  codex_cloud: 'Codex Cloud',
  codex: 'Codex',
};

export function formatRuntime(runtime: string | null | undefined): string {
  if (!runtime) return '—';
  return RUNTIME_DISPLAY_NAMES[runtime] ?? runtime;
}

export function formatTaskSkills(
  taskSkills: string[] | null | undefined,
  skillId: string | null | undefined
): string {
  if (taskSkills?.length) {
    return taskSkills.join(', ');
  }
  return skillId || '—';
}
