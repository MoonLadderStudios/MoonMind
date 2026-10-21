import { readFileSync } from 'node:fs';
import { expect, it, vi } from 'vitest';

it.each(['script', 'SCRIPT'])('follows system theme changes live without an unused stored override (%s)', (scriptTag) => {
  const template = readFileSync('api_service/templates/react_dashboard.html', 'utf8')
    .replace('<script>', `<${scriptTag}>`)
    .replace('</script>', `</${scriptTag}>`);
  const boot = new DOMParser().parseFromString(template, 'text/html').querySelector('script')!.textContent!;
  let change: (() => void) | undefined;
  const media = { matches: false, addEventListener: vi.fn((_event, listener) => { change = listener; }) };
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  localStorage.setItem('moonmind.theme', 'dark');
  try {
    new Function(boot)();
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    media.matches = true;
    expect(change).toBeDefined();
    change!();
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  } finally {
    vi.unstubAllGlobals();
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    delete document.documentElement.dataset.theme;
  }
});
