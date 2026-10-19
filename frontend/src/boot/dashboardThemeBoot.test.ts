import { readFileSync } from 'node:fs';

import { afterEach, describe, expect, it } from 'vitest';

// The dashboard HTML shell sets the theme before React loads. Run its inline
// script against a controllable prefers-color-scheme query.
const template = readFileSync(`${process.cwd()}/api_service/templates/react_dashboard.html`, 'utf8');
const themeScript = template.match(/<script>\s*([\s\S]*?)<\/script>/)?.[1] ?? '';

function runThemeScript(prefersDark: boolean) {
  const listeners: Array<() => void> = [];
  const query = {
    matches: prefersDark,
    addEventListener: (_type: string, listener: () => void) => listeners.push(listener),
  };
  window.matchMedia = (() => query) as unknown as typeof window.matchMedia;
  new Function(themeScript)();
  return {
    changeTo(dark: boolean) {
      query.matches = dark;
      listeners.forEach((listener) => listener());
    },
  };
}

afterEach(() => {
  document.documentElement.classList.remove('dark');
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
});

describe('dashboard theme boot script', () => {
  it('follows the operating-system theme on load', () => {
    expect(themeScript).toContain('prefers-color-scheme');
    runThemeScript(true);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('applies operating-system theme changes without a reload', () => {
    const os = runThemeScript(false);
    expect(document.documentElement.dataset.theme).toBe('light');
    os.changeTo(true);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
    os.changeTo(false);
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('ignores the retired moonmind.theme storage override', () => {
    window.localStorage.setItem('moonmind.theme', 'dark');
    runThemeScript(false);
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
