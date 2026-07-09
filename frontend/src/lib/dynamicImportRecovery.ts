const DYNAMIC_IMPORT_ERROR_PATTERNS = [
  'Failed to fetch dynamically imported module',
  'Importing a module script failed',
  'error loading dynamically imported module',
];

export function isDynamicImportLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '');
  const normalized = message.toLowerCase();
  return DYNAMIC_IMPORT_ERROR_PATTERNS.some((pattern) =>
    normalized.includes(pattern.toLowerCase()),
  );
}

export function reloadOnceForDynamicImportError(buildId?: string | null): boolean {
  if (typeof window === 'undefined') {
    return false;
  }

  const normalizedBuildId =
    typeof buildId === 'string' && buildId.trim() ? buildId.trim() : 'unknown';
  const key = `moonmind.dashboard.dynamic-import-reload:${normalizedBuildId}`;

  try {
    if (window.sessionStorage.getItem(key) === '1') {
      return false;
    }
    window.sessionStorage.setItem(key, '1');
  } catch {
    return false;
  }

  window.location.reload();
  return true;
}
