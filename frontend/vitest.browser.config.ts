import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { playwright } from '@vitest/browser-playwright';

// Real-browser regression suite for styling contracts that jsdom cannot
// verify: computed pseudo-element styles, CSS custom-property scoping, and
// layout overflow. Run with `npm run ui:test:browser`. WebKit covers the
// targeted form/fieldset/overlay leg (MoonLadderStudios/MoonMind#4559); the
// Playwright container image used in CI ships all three engines, so no extra
// provisioning step is needed there.
const supportedEngines = ['chromium', 'firefox', 'webkit'] as const;
type BrowserEngine = (typeof supportedEngines)[number];
const configuredEngines = process.env.MOONMIND_BROWSER_ENGINES
  ?.split(',')
  .map((engine) => engine.trim())
  .filter(Boolean);
const engines = (configuredEngines?.length ? configuredEngines : supportedEngines) as BrowserEngine[];

for (const engine of engines) {
  if (!supportedEngines.includes(engine)) {
    throw new Error(`Unsupported MOONMIND_BROWSER_ENGINES value: ${engine}`);
  }
}

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['frontend/src/browser/**/*.browser.test.{ts,tsx}'],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      // Local runs default to both engines. CI assigns one engine per matrix leg.
      instances: engines.map((browser) => ({
        browser,
        viewport: { width: 1280, height: 800 },
      })),
    },
  },
});
