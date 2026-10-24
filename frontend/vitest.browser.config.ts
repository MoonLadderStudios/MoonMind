import { defineConfig } from 'vitest/config';
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

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['frontend/src/browser/**/*.browser.test.{ts,tsx}'],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      commands: {
        replaceInputByKeyboard: async ({ iframe }, label: string, text: string) => {
          const input = iframe.getByLabel(label, { exact: true });
          await input.click();
          // These are single-line fields. Select their line without relying
          // on the engine's platform-specific select-all shortcut.
          await input.press('Home');
          await input.press('Shift+End');
          await input.press('Backspace');
          await input.pressSequentially(text);
        },
        pointerButton: async ({ page }, action: 'down' | 'up') => page.mouse[action](),
        touchTreatment: async ({ context, page }, markup: string, css: string) => {
          const touchContext = await context.browser()!.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
          try {
            const touchPage = await touchContext.newPage();
            await touchPage.setContent(markup);
            await touchPage.addStyleTag({ content: css });
            const button = touchPage.locator('button');
            const sample = () => button.evaluate((element) => {
              const style = getComputedStyle(element);
              return [style.backgroundColor, style.backgroundImage, style.transform, style.filter, style.boxShadow];
            });
            const before = await sample();
            await button.tap();
            // A touch tap may paint a short native press after touchend. Check
            // the released treatment, including any synthetic hover left behind.
            await touchPage.waitForFunction((baseline) => {
              const element = document.querySelector('button')!;
              const style = getComputedStyle(element);
              return !element.matches(':active') && [style.backgroundColor, style.backgroundImage, style.transform, style.filter, style.boxShadow].every((value, index) => value === baseline[index]);
            }, before, { timeout: 2000 });
            return { before, after: await sample(), hoverNone: await touchPage.evaluate(() => matchMedia('(hover: none)').matches) };
          } finally {
            await touchContext.close();
            await page.bringToFront();
          }
        },
      },
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
