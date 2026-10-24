import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { expect, it, vi } from 'vitest';

const { JSDOM } = createRequire(import.meta.url)('jsdom') as {
  JSDOM: new (html: string, options: {
    url: string;
    runScripts: 'dangerously';
    beforeParse: (window: Window) => void;
  }) => { window: Window };
};

it.each(['script', 'SCRIPT'])('follows system theme changes live without an unused stored override (%s)', (scriptTag) => {
  const template = readFileSync('api_service/templates/react_dashboard.html', 'utf8')
    .replace('<script>', `<${scriptTag}>`)
    .replace('</script>', `</${scriptTag}>`);
  const hostTheme = document.documentElement.dataset.theme;
  const hostClasses = document.documentElement.className;
  const hostStoredTheme = localStorage.getItem('moonmind.theme');
  const hostMatchMedia = window.matchMedia;
  let change: (() => void) | undefined;
  const media = { matches: false, addEventListener: vi.fn((_event, listener) => { change = listener; }) };
  const matchMedia = vi.fn(() => media);
  const scriptErrors: ErrorEvent[] = [];
  // Execute only this trusted, checked-in template. Subresource loading stays disabled.
  const dom = new JSDOM(template, {
    url: 'https://moonmind.test/',
    runScripts: 'dangerously',
    beforeParse(window) {
      Object.defineProperty(window, 'matchMedia', { value: matchMedia });
      window.localStorage.setItem('moonmind.theme', 'dark');
      window.addEventListener('error', (event) => { scriptErrors.push(event); });
    },
  });
  try {
    const root = dom.window.document.documentElement;
    expect(scriptErrors).toEqual([]);
    expect(matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
    expect(media.addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    expect(root.dataset.theme).toBe('light');
    expect(root.classList.contains('dark')).toBe(false);
    media.matches = true;
    expect(change).toBeDefined();
    change!();
    expect(root.dataset.theme).toBe('dark');
    expect(root.classList.contains('dark')).toBe(true);
    media.matches = false;
    change!();
    expect(root.dataset.theme).toBe('light');
    expect(root.classList.contains('dark')).toBe(false);
    expect(dom.window.localStorage.getItem('moonmind.theme')).toBe('dark');
    expect(dom.window.document).not.toBe(document);
    expect(dom.window.localStorage).not.toBe(localStorage);
    expect(document.documentElement.dataset.theme).toBe(hostTheme);
    expect(document.documentElement.className).toBe(hostClasses);
    expect(localStorage.getItem('moonmind.theme')).toBe(hostStoredTheme);
    expect(window.matchMedia).toBe(hostMatchMedia);
  } finally {
    dom.window.close();
  }
});
