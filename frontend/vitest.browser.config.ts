import { resolve } from 'node:path';
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
        prepareCaptureViewport: async ({ page }, width: number, height: number) => {
          const original = page.viewportSize();
          // Size the runner before Vitest sizes its test iframe or the app renders.
          // This allows Vitest to select scale(1) without changing a live portal.
          await page.setViewportSize({ width: width + 320, height: height + 320 });
          return original;
        },
        restoreCaptureViewport: async ({ page }, original: { width: number; height: number } | null) => {
          if (original) await page.setViewportSize(original);
        },
        captureViewport: async ({ page, iframe }, fileName: string, width: number, height: number) => {
          const geometry = async () => ({
            frame: await iframe.owner().boundingBox(),
            document: await iframe.locator('html').evaluate(element => {
              const doc = element.ownerDocument;
              const win = doc.defaultView!;
              const root = element.getBoundingClientRect();
              return {
                innerWidth: win.innerWidth, innerHeight: win.innerHeight,
                clientWidth: doc.documentElement.clientWidth,
                rootWidth: root.width, scrollX: win.scrollX, scrollY: win.scrollY,
                usableRight: Math.min(win.innerWidth, doc.documentElement.clientWidth, root.right),
                tooltips: Array.from(doc.querySelectorAll<HTMLElement>('[role="tooltip"]'))
                  .filter(panel => !panel.hidden && win.getComputedStyle(panel).visibility !== 'hidden')
                  .map(panel => {
                    const box = panel.getBoundingClientRect();
                    return { left: box.left, right: box.right, width: box.width, inlineLeft: panel.style.left };
                  }),
              };
            }),
          });
          const before = await geometry();
          console.info('captureViewport before', fileName, JSON.stringify(before));
          // Whole-runner evidence distinguishes a browser capture issue from a
          // clipped element. Neither capture scrolls the iframe or changes its scale.
          await page.screenshot({ path: resolve('artifacts/browser-layout', fileName.replace(/\.png$/, '-runner.png')) });
          if (!before.frame) throw new Error('Test iframe has no capture bounds');
          await page.screenshot({ path: resolve('artifacts/browser-layout', fileName), clip: before.frame });
          const after = await geometry();
          console.info('captureViewport after', fileName, JSON.stringify(after));
          for (const sample of [before, after]) {
            if (sample.document.innerWidth !== width || sample.document.innerHeight !== height
              || !sample.frame || Math.abs(sample.frame.width - width) > 1 || Math.abs(sample.frame.height - height) > 1) {
              throw new Error(`Unexpected scaled or resized test viewport: ${JSON.stringify(sample)}`);
            }
            for (const panel of sample.document.tooltips) {
              if (panel.left < -1 || panel.right > sample.document.usableRight + 1) {
                throw new Error(`Tooltip exceeds usable viewport during capture: ${JSON.stringify(sample)}`);
              }
            }
          }
        },
        replaceInputByKeyboard: async ({ iframe }, label: string, text: string) => {
          const input = iframe.getByLabel(label, { exact: true });
          await input.click();
          // Use the platform's native select-all shortcut; WebKit can treat
          // Home/End as viewport navigation instead of input caret movement.
          await input.press('ControlOrMeta+A');
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
