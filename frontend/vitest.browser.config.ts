import { defineConfig } from 'vitest/config';
import type { BrowserCommand } from 'vitest/node';
import react from '@vitejs/plugin-react';
import { playwright } from '@vitest/browser-playwright';

// Real-browser regression suite for styling contracts that jsdom cannot
// verify: computed pseudo-element styles, CSS custom-property scoping, and
// layout overflow. Run with `npm run ui:test:browser`.
// MoonMind#4559 adds a targeted WebKit leg for form/fieldset/overlay cases;
// Chromium/Firefox remain the default matrix, WebKit runs when requested via
// MOONMIND_BROWSER_ENGINES=webkit or in the CI webkit leg.
const supportedEngines = ['chromium', 'firefox', 'webkit'] as const;
type BrowserEngine = (typeof supportedEngines)[number];
// Default matrix stays Chromium + Firefox: WebKit runs only when requested
// via MOONMIND_BROWSER_ENGINES=webkit or in the CI webkit leg.
const defaultEngines: readonly BrowserEngine[] = ['chromium', 'firefox'];
const configuredEngines = process.env.MOONMIND_BROWSER_ENGINES
  ?.split(',')
  .map((engine) => engine.trim())
  .filter(Boolean);
const engines = (configuredEngines?.length ? configuredEngines : [...defaultEngines]) as BrowserEngine[];

for (const engine of engines) {
  if (!supportedEngines.includes(engine)) {
    throw new Error(`Unsupported MOONMIND_BROWSER_ENGINES value: ${engine}`);
  }
}

// userEvent cannot hold a mouse button, so computed-style checks of :active
// press and release the real Playwright mouse over an element.
const pressPointer: BrowserCommand<[selector: string]> = async (context, selector) => {
  const frame = await context.frame();
  await frame.locator(selector).hover();
  await context.page.mouse.down();
};
const releasePointer: BrowserCommand<[]> = async (context) => {
  await context.page.mouse.up();
};

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['frontend/src/browser/**/*.browser.test.{ts,tsx}'],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      commands: { pressPointer, releasePointer },
      // Local runs default to both engines. CI assigns one engine per matrix leg.
      instances: engines.map((browser) => ({
        browser,
        // Firefox and WebKit can lose native focus or keyboard activation when
        // another test page takes focus during an interaction.
        ...(browser === 'firefox' || browser === 'webkit' ? { fileParallelism: false } : {}),
        viewport: { width: 1280, height: 800 },
      })),
    },
  },
});
