/**
 * Copy text to the clipboard without throwing in environments where the
 * Clipboard API is unavailable or rejects (e.g. insecure contexts, headless
 * test runners). Shared by dashboard primitives so copy behavior is consistent
 * across LogPanel, DashboardErrorDetails, and similar surfaces (MM-959).
 *
 * Returns a promise that resolves to whether the write was attempted. It never
 * rejects so callers can keep the UI stable on failure.
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Ignore clipboard failures; the UI should stay stable.
    return false;
  }
}
