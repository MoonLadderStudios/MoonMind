import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest';
import postcss from 'postcss';
import type { Root, Rule } from 'postcss';

import type { BootPayload } from '../boot/parseBootPayload';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { MissionControlApp } from './mission-control-app';

function normalizeCssSelector(selector: string): string {
  return selector
    .replace(/\s*\{\s*$/, '')
    .trim()
    .replace(/\s+/g, ' ');
}

const parsedCssCache = new Map<string, Root>();

function parsedCssRoot(css: string): Root {
  const cachedRoot = parsedCssCache.get(css);
  if (cachedRoot) {
    return cachedRoot;
  }

  const root = postcss.parse(css);
  parsedCssCache.set(css, root);
  return root;
}

function cssRuleBlocks(css: string, selector: string): string[] {
  const expectedSelector = normalizeCssSelector(selector);
  const expectedSelectors = selector.split(',').map(normalizeCssSelector);
  const blocks: string[] = [];
  parsedCssRoot(css).walkRules((rule) => {
    const ruleSelector = normalizeCssSelector(rule.selector);
    const ruleSelectors = rule.selector.split(',').map(normalizeCssSelector);
    if (
      ruleSelector === expectedSelector ||
        ruleSelectors.includes(expectedSelector) ||
        expectedSelectors.every((expected) => ruleSelectors.includes(expected))
    ) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks;
}

function cssRuleBlock(css: string, selector: string): string {
  return cssRuleBlocks(css, selector)[0] ?? '';
}

function cssRuleBlockMatching(css: string, matches: (rule: Rule) => boolean): string {
  let block = '';
  parsedCssRoot(css).walkRules((rule) => {
    if (!block && matches(rule)) {
      block = rule.nodes.map((node) => `${node.toString()};`).join('\n');
    }
  });
  return block;
}

vi.mock('@xterm/xterm', () => {
  class MockTerminal {
    cols = 80;
    rows = 24;
    private element: HTMLElement | null = null;
    constructor(_options?: unknown) {}
    loadAddon(_addon: unknown) {}
    open(element: HTMLElement) {
      this.element = element;
      element.setAttribute('data-testid', 'oauth-xterm');
    }
    write(data: string) {
      if (this.element) {
        this.element.textContent = `${this.element.textContent ?? ''}${data}`;
      }
    }
    writeln(data: string) {
      this.write(`${data}\n`);
    }
    onData(_callback: (data: string) => void) {
      return { dispose: vi.fn() };
    }
    getSelection() {
      return this.element?.textContent ?? '';
    }
    dispose() {}
  }

  return { Terminal: MockTerminal };
});

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit() {}
  },
}));

describe('Mission Control shared entry', () => {
  let fetchSpy: MockInstance;
  let missionControlCss: string;
  const originalWebSocket = window.WebSocket;

  beforeAll(async () => {
    const { readFileSync } = await import('node:fs');
    missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      'utf8',
    );
  });

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/secrets') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.WebSocket = originalWebSocket;
  });

  it('renders dashboard alerts and lazy-loads the requested page component', async () => {
    const payload: BootPayload = {
      page: 'tasks-home',
      apiBase: '/api',
      initialData: {
        layout: {
          dataWidePanel: true,
        },
      },
    };

    renderWithClient(<MissionControlApp payload={payload} />);

    expect(await screen.findByText('Hello from Tasks Home!', {}, { timeout: 3000 })).toBeTruthy();
    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    await waitFor(() => {
      expect(document.querySelector('.panel--data-wide')).toBeTruthy();
      expect(document.querySelector('.dashboard-shell-constrained--data-wide')).toBeTruthy();
    });
  });

  it('uses the constrained shell by default for non-table pages', async () => {
    renderWithClient(<MissionControlApp payload={{ page: 'tasks-home', apiBase: '/api' }} />);

    expect(await screen.findByText('Hello from Tasks Home!')).toBeTruthy();
    expect(document.querySelector('.panel--data-wide')).toBeNull();
    expect(document.querySelector('.dashboard-shell-constrained--data-wide')).toBeNull();
    expect(document.querySelector('.dashboard-shell-constrained')).toBeTruthy();
  });

  it('keeps the default panel constrained and centered while data routes opt wider', async () => {
    expect(missionControlCss).toMatch(
      /\.panel\s*\{[^}]*margin-left:\s*auto;[^}]*margin-right:\s*auto;[^}]*max-width:\s*min\(72rem,\s*calc\(100vw - 2rem\)\)/s,
    );
    expect(missionControlCss).toMatch(
      /\.panel\.panel--data-wide\s*\{[^}]*max-width:\s*min\(112rem,\s*calc\(100vw - 2rem\)\)/s,
    );
  });

  it('defines shared visual atmosphere and glass tokens for light and dark themes', async () => {
    const requiredTokens = [
      '--mm-atmosphere-violet',
      '--mm-atmosphere-cyan',
      '--mm-atmosphere-warm',
      '--mm-atmosphere-base',
      '--mm-glass-fill',
      '--mm-glass-border',
      '--mm-glass-edge',
      '--mm-input-well',
      '--mm-elevation-panel',
      '--mm-elevation-floating',
    ];

    for (const token of requiredTokens) {
      expect(missionControlCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
      expect(missionControlCss).toMatch(new RegExp(`\\.dark\\s*\\{[^}]*${token}:`, 's'));
    }
  });

  it('renders Mission Control atmosphere and shared chrome from visual tokens', async () => {
    expect(missionControlCss).toMatch(
      /^body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/ms,
    );
    expect(missionControlCss).toMatch(
      /\.dark body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead::before\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*box-shadow:\s*var\(--mm-elevation-panel\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.panel\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*border:\s*1px solid var\(--mm-glass-border\);[^}]*box-shadow:\s*var\(--mm-elevation-panel\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.queue-floating-bar\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*border:\s*1px solid var\(--mm-glass-border\);[^}]*box-shadow:\s*var\(--mm-elevation-floating\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.queue-floating-bar \.queue-inline-selector select,\s*\.queue-floating-bar \.queue-inline-selector input\s*\{[^}]*background:\s*var\(--mm-input-well\);[^}]*border-color:\s*var\(--mm-glass-edge\);/s,
    );
  });

  it('defines the MM-425 shared surface hierarchy roles', async () => {
    const matteBlock = cssRuleBlock(missionControlCss, '.surface--matte-data');
    const satinBlock = cssRuleBlock(missionControlCss, '.panel--satin');
    const glassBlock = cssRuleBlock(
      missionControlCss,
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    );
    const liquidBlock = cssRuleBlock(missionControlCss, '.surface--liquidgl-hero');
    const accentBlock = cssRuleBlock(missionControlCss, '.surface--accent-live');
    const nestedDenseBlock = cssRuleBlock(missionControlCss, '.surface--nested-dense');

    expect(matteBlock).toContain('background: rgb(var(--mm-panel) / 0.92)');
    expect(missionControlCss).toMatch(
      /\.panel--data\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.92\);/s,
    );
    expect(satinBlock).toContain('background: var(--mm-input-well)');
    expect(glassBlock).toContain('background: var(--mm-glass-fill)');
    expect(glassBlock).toContain('border: 1px solid var(--mm-glass-border)');
    expect(glassBlock).toContain('box-shadow: var(--mm-elevation-panel)');
    expect(liquidBlock).toContain('background: var(--mm-glass-fill)');
    expect(liquidBlock).toContain('box-shadow: var(--mm-elevation-floating)');
    expect(accentBlock).toContain('background: rgb(var(--mm-accent) / 0.14)');
    expect(nestedDenseBlock).toContain('background: rgb(var(--mm-panel) / 0.86)');
  });

  it('keeps glass token based with near-opaque fallbacks when backdrop filtering is unavailable', async () => {
    const glassBlock = cssRuleBlock(
      missionControlCss,
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    );

    expect(glassBlock).toContain('backdrop-filter: blur(18px) saturate(1.35)');
    expect(glassBlock).toContain('-webkit-backdrop-filter: blur(18px) saturate(1.35)');
    expect(missionControlCss).toMatch(
      /@supports not \(\(backdrop-filter:\s*blur\(2px\)\) or \(-webkit-backdrop-filter:\s*blur\(2px\)\)\)\s*\{[^}]*\.surface--glass-control,\s*\.panel--controls,\s*\.panel--floating,\s*\.panel--utility,\s*\.surface--liquidgl-hero,\s*\.queue-floating-bar\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.94\);/s,
    );
  });

  it('keeps liquidGL opt-in and away from default dense surfaces', async () => {
    expect(cssRuleBlock(missionControlCss, '.panel')).not.toContain('liquid');
    expect(cssRuleBlock(missionControlCss, '.card')).not.toContain('liquid');
    expect(cssRuleBlock(missionControlCss, 'table')).not.toContain('liquid');
    expect(cssRuleBlock(missionControlCss, '.data-table-slab')).not.toContain('liquid');

    const liquidBlock = cssRuleBlock(missionControlCss, '.surface--liquidgl-hero');
    expect(liquidBlock).toContain('isolation: isolate');
    expect(liquidBlock).toContain('overflow: hidden');
    expect(liquidBlock).toContain('backdrop-filter: blur(26px) saturate(1.65)');
  });

  it('enforces MM-429 readable contrast tokens across common Mission Control surfaces', async () => {
    expect(cssRuleBlock(missionControlCss, 'label')).toContain('color: rgb(var(--mm-ink))');
    expect(cssRuleBlock(missionControlCss, '.data-table th,\n.data-table td')).toContain(
      'color: rgb(var(--mm-ink))',
    );
    expect(cssRuleBlock(missionControlCss, 'input::placeholder,\ntextarea::placeholder')).toContain(
      'color: rgb(var(--mm-muted))',
    );
    expect(cssRuleBlock(missionControlCss, '.task-list-filter-chip')).toContain(
      'color: rgb(var(--mm-ink))',
    );
    const primaryButtonBlock = cssRuleBlockMatching(missionControlCss, (rule) => {
      const selectors = rule.selector.split(',').map(normalizeCssSelector);
      return (
        selectors.some((selector) => selector.startsWith('button:not(')) &&
        rule.nodes.some((node) => node.type === 'decl' && node.prop === 'color' && node.value === '#fff')
      );
    });
    expect(primaryButtonBlock).toContain('color: #fff');
    expect(cssRuleBlock(missionControlCss, '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility')).toContain(
      'background: var(--mm-glass-fill)',
    );
  });

  it('enforces MM-429 focus visibility across representative interactive surfaces', async () => {
    const focusSelectors = [
      'button:focus-visible',
      '.button:focus-visible',
      'input:focus-visible,\nselect:focus-visible,\ntextarea:focus-visible',
      '.route-nav a:focus-visible',
      '.table-sort-button:focus-visible',
      '.queue-action:focus-visible,\n.queue-submit-primary:focus-visible',
      '.queue-step-attachment-add-button:focus-visible',
      '.live-logs-artifact-link:focus-visible',
      '.td-instructions-toggle:focus-visible',
    ];

    for (const selector of focusSelectors) {
      const block = cssRuleBlock(missionControlCss, selector);
      expect(block).toContain('box-shadow: var(--mm-control-focus-ring)');
    }
  });

  it('enforces MM-429 reduced-motion suppression for live and premium effects', async () => {
    const runningIconBlock = cssRuleBlockMatching(
      missionControlCss,
      (rule) =>
        normalizeCssSelector(rule.selector) === '.step-tl-icon.step-icon-running' &&
        rule.nodes.some((node) => node.type === 'decl' && node.toString() === 'animation: none !important'),
    );
    expect(runningIconBlock).toContain('animation: none !important');
    expect(runningIconBlock).toContain('opacity: 1');

    const premiumEffectBlock = cssRuleBlockMatching(
      missionControlCss,
      (rule) =>
        rule.selector
          .split(',')
          .map(normalizeCssSelector)
          .includes('.surface--liquidgl-hero') &&
        rule.nodes.some(
          (node) =>
            node.type === 'decl' && node.toString() === 'transition-duration: 0s !important',
        ),
    );
    expect(premiumEffectBlock).toContain('transition-duration: 0s !important');
    expect(premiumEffectBlock).toContain('animation-duration: 0s !important');
  });

  it('enforces MM-429 fallback shells and premium-effect limits', async () => {
    expect(missionControlCss).toMatch(
      /@supports not \(\(backdrop-filter:\s*blur\(2px\)\) or \(-webkit-backdrop-filter:\s*blur\(2px\)\)\)\s*\{[^}]*\.surface--glass-control,[^}]*\.panel--controls,[^}]*\.panel--floating,[^}]*\.panel--utility,[^}]*\.surface--liquidgl-hero,[^}]*\.queue-floating-bar\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.94\);[^}]*border-color:\s*rgb\(var\(--mm-border\) \/ 0\.84\);/s,
    );

    for (const selector of [
      '.surface--matte-data',
      '.surface--nested-dense',
      '.data-table-slab',
      '.td-evidence-region',
      '.td-evidence-slab',
      'textarea',
    ]) {
      const block = cssRuleBlock(missionControlCss, selector);
      expect(block).not.toContain('liquid');
      expect(block).not.toContain('backdrop-filter');
      expect(block).not.toContain('blur(26px)');
    }
  });

  it('enforces MM-430 semantic shell class stability for Mission Control sources', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardTemplate = readFileSync(
      `${process.cwd()}/api_service/templates/react_dashboard.html`,
      'utf8',
    );
    const navigationTemplate = readFileSync(
      `${process.cwd()}/api_service/templates/_navigation.html`,
      'utf8',
    );

    expect(dashboardTemplate).toContain('class="dashboard-root"');
    expect(dashboardTemplate).toContain('class="masthead"');
    expect(navigationTemplate).toContain('class="route-nav"');

    for (const selector of [
      '.dashboard-root',
      '.masthead',
      '.route-nav',
      '.panel',
      '.card',
      '.toolbar',
      '.status-queued',
      '.status-running',
      '.queue-submit-form',
    ]) {
      expect(cssRuleBlock(missionControlCss, selector)).not.toBe('');
    }
  });


  it('defines the shared MM-488 executing shimmer modifier contract', async () => {
    expect(missionControlCss).toMatch(/--mm-executing-sweep-cycle-duration:\s*1800ms/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-band-width:\s*24%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-band-height:\s*180%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-halo-width-multiplier:\s*10/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-core-width-multiplier:\s*9\.1667/);
    expect(missionControlCss).not.toContain('--mm-executing-sweep-halo-peak-width-multiplier');
    expect(missionControlCss).not.toContain('--mm-executing-sweep-core-peak-width-multiplier');
    expect(missionControlCss).toMatch(/--mm-executing-sweep-start-x:\s*135%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-start-y:\s*160%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-end-x:\s*-135%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-end-y:\s*-160%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-layer-offset-x:\s*-12%/);
    expect(missionControlCss).toMatch(/--mm-executing-sweep-layer-offset-y:\s*-10%/);
    expect(missionControlCss).toMatch(/--mm-executing-letter-sweep-width:\s*84%/);
    expect(missionControlCss).toMatch(/--mm-executing-letter-cycle-duration:\s*var\(--mm-executing-sweep-cycle-duration\)/);
    expect(missionControlCss).toMatch(/--mm-executing-letter-edge-padding:\s*3/);
    expect(missionControlCss).toMatch(/--mm-executing-letter-sweep-direction:\s*1/);
    expect(missionControlCss).toContain('--mm-executing-letter-halo: rgb(var(--mm-accent-2) / 0.32)');
    expect(missionControlCss).toContain('--mm-executing-letter-bright: color-mix(in srgb, rgb(var(--mm-accent-2)) 68%, white 32%)');

    const shimmerBlock = cssRuleBlocks(
      missionControlCss,
      '.status-running[data-state="executing"][data-effect="shimmer-sweep"], .status-running.is-executing',
    ).join('\n');
    expect(shimmerBlock).toContain('animation: mm-status-pill-shimmer');
    expect(shimmerBlock).toContain('background-color: rgb(var(--mm-accent-2) / 0.14)');
    expect(shimmerBlock).toContain('background-image:');
    expect(shimmerBlock).toContain('overflow: hidden');
    expect(shimmerBlock).toContain('isolation: isolate');
    expect(shimmerBlock).not.toContain('animation-delay:');
    expect(shimmerBlock).toContain('var(--mm-executing-sweep-cycle-duration)');
    expect(shimmerBlock).toContain('linear infinite');
    expect(shimmerBlock).toContain('rgb(var(--mm-accent) / var(--mm-executing-sweep-halo-opacity))');
    expect(shimmerBlock).toContain('rgb(var(--mm-accent-2) / var(--mm-executing-sweep-core-opacity))');
    expect(shimmerBlock).toMatch(
      /background-size:\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-halo-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\),\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-core-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\)/,
    );
    expect(shimmerBlock).toMatch(
      /background-position:\s*var\(--mm-executing-sweep-start-x\)\s*var\(--mm-executing-sweep-start-y\),\s*calc\(var\(--mm-executing-sweep-start-x\)\s*\+\s*var\(--mm-executing-sweep-layer-offset-x\)\)\s*calc\(var\(--mm-executing-sweep-start-y\)\s*\+\s*var\(--mm-executing-sweep-layer-offset-y\)\)/,
    );

    expect(missionControlCss).not.toMatch(/@keyframes mm-status-pill-shimmer\s*\{[\s\S]*?52%\s*\{/);
    expect(missionControlCss).toMatch(/@keyframes mm-status-pill-shimmer\s*\{[\s\S]*?0%\s*\{[\s\S]*?background-position:\s*var\(--mm-executing-sweep-start-x\)\s*var\(--mm-executing-sweep-start-y\),[\s\S]*?100%\s*\{[\s\S]*?background-position:\s*var\(--mm-executing-sweep-end-x\)\s*var\(--mm-executing-sweep-end-y\),[\s\S]*?background-size:\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-halo-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\),[\s\S]*?calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-core-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\);/);
    expect(missionControlCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.status-running\[data-state="executing"\]\[data-effect="shimmer-sweep"\],\s*\.status-running\.is-executing[\s\S]*?animation: none;/,
    );
    expect(missionControlCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?background-position:\s*50% 50%,[\s\S]*?calc\(50% \+ \(var\(--mm-executing-sweep-layer-offset-x\) \/ 2\)\)\s*calc\(50% \+ \(var\(--mm-executing-sweep-layer-offset-y\) \/ 2\)\);[\s\S]*?background-size:\s*160% var\(--mm-executing-sweep-band-height\),\s*140% var\(--mm-executing-sweep-band-height\);/,
    );
    const shimmerBeforeSelector = cssRuleBlockMatching(
      missionControlCss,
      (rule) =>
        rule.selector.includes('status-running') &&
        rule.selector.includes('shimmer-sweep') &&
        rule.selector.includes('::before'),
    );
    expect(shimmerBeforeSelector).toBe('');

    const shimmerAfterBlock = cssRuleBlockMatching(
      missionControlCss,
      (rule) =>
        rule.selector.includes('status-running') &&
        rule.selector.includes('shimmer-sweep') &&
        rule.selector.includes('::after'),
    );
    expect(shimmerAfterBlock).toBe('');
    expect(cssRuleBlock(missionControlCss, '.status-letter-wave')).toContain('z-index: 2');
    const glyphBlock = cssRuleBlock(missionControlCss, '.status-letter-wave__glyph');
    expect(glyphBlock).toContain('animation-name: mm-executing-letter-brighten');
    expect(glyphBlock).toContain('animation-duration: var(--mm-executing-letter-cycle-duration, var(--mm-executing-sweep-cycle-duration, 1800ms))');
    expect(glyphBlock).toContain('var(--mm-executing-letter-sweep-direction)');
    expect(glyphBlock).toContain('var(--mm-executing-letter-edge-padding)');
    expect(glyphBlock).toContain('animation-delay: calc(var(--mm-executing-letter-cycle-duration, var(--mm-executing-sweep-cycle-duration, 1800ms)) * var(--mm-letter-delay-ratio))');
    expect(glyphBlock).not.toContain('will-change');
    expect(missionControlCss).toMatch(
      /@keyframes mm-executing-letter-brighten\s*\{[\s\S]*?0%\s*\{[\s\S]*?var\(--mm-executing-letter-bright[\s\S]*?5%\s*\{[\s\S]*?brightness\(1\.14\)[\s\S]*?12%,\s*100%\s*\{[\s\S]*?brightness\(1\)/,
    );
    expect(missionControlCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.status-letter-wave__glyph\s*\{[\s\S]*?animation: none !important;[\s\S]*?text-shadow: none !important;[\s\S]*?filter: none !important;/,
    );
  });

  it('enforces MM-430 additive shared styling modifiers', async () => {
    for (const selector of [
      '.panel--controls',
      '.panel--data',
      '.panel--floating',
      '.panel--utility',
      '.panel.panel--data-wide',
      '.dashboard-shell-constrained--data-wide',
    ]) {
      expect(cssRuleBlock(missionControlCss, selector)).not.toBe('');
    }

    expect(cssRuleBlock(missionControlCss, '.panel.panel--data-wide')).toContain(
      'max-width: min(112rem, calc(100vw - 2rem))',
    );
  });

  it('enforces MM-430 token-first styling for semantic role surfaces', async () => {
    const semanticRoleSelectors = [
      '.panel',
      '.card',
      '.route-nav a',
      '.queue-floating-bar',
      '.queue-inline-filter',
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    ];

    for (const selector of semanticRoleSelectors) {
      const blocks = cssRuleBlocks(missionControlCss, selector);
      expect(blocks.join('\n')).toContain('var(--mm-');
      for (const block of blocks) {
        expect(block).not.toMatch(
          /(?:^|\n)(?:color|background|border|outline|box-shadow):.*?(?:#[0-9a-fA-F]{3,8}\b|rgba\(|rgb\((?!var\())/,
        );
      }
    }
  });

  it('enforces MM-430 light and dark themes through token swaps', async () => {
    for (const token of [
      '--mm-bg',
      '--mm-panel',
      '--mm-ink',
      '--mm-muted',
      '--mm-border',
      '--mm-accent',
      '--mm-glass-fill',
      '--mm-control-shell',
    ]) {
      expect(missionControlCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
      expect(missionControlCss).toMatch(new RegExp(`\\.dark\\s*\\{[^}]*${token}:`, 's'));
    }

    expect(missionControlCss).toMatch(
      /^body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/ms,
    );
    expect(missionControlCss).toMatch(
      /\.dark body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/s,
    );
    expect(missionControlCss).not.toMatch(/\.dark\s+\.panel\s*\{[^}]*background:/s);
    expect(missionControlCss).not.toMatch(/\.dark\s+\.card\s*\{[^}]*background:/s);
  });

  it('defines shared interaction tokens for routine controls', async () => {
    const requiredTokens = [
      '--mm-control-hover-scale',
      '--mm-control-press-scale',
      '--mm-control-transition',
      '--mm-control-focus-ring',
      '--mm-control-disabled-opacity',
      '--mm-control-shell',
      '--mm-control-shell-hover',
      '--mm-control-border',
    ];

    for (const token of requiredTokens) {
      expect(missionControlCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
    }
  });

  it('uses scale-only glow and grow states for routine buttons', async () => {
    const routineBlocks = [
      cssRuleBlock(
        missionControlCss,
        'button:not(.secondary):not(.queue-action):not(.queue-submit-primary):not(.queue-step-icon-button):not(.queue-step-attachment-add-button):not(.queue-step-extension-button):not(.table-sort-button):not(.td-instructions-toggle):hover',
      ),
      cssRuleBlock(missionControlCss, 'button.secondary:hover'),
      cssRuleBlock(
        missionControlCss,
        '.button:not(.secondary):not(.queue-action):not(.queue-submit-primary):hover',
      ),
      cssRuleBlock(missionControlCss, '.button.secondary:hover'),
      cssRuleBlock(missionControlCss, '.queue-action:hover,\n.queue-submit-primary:hover'),
      cssRuleBlock(missionControlCss, '.queue-step-extension-button:hover'),
      cssRuleBlock(missionControlCss, '.queue-step-icon-button:hover'),
      cssRuleBlock(missionControlCss, '.queue-step-icon-button.destructive:hover'),
    ];

    for (const block of routineBlocks) {
      expect(block).toContain('scale(var(--mm-control-hover-scale))');
      expect(block).not.toContain('translateY');
    }

    const pressedBlocks = [
      cssRuleBlock(
        missionControlCss,
        'button:not(.secondary):not(.queue-action):not(.queue-submit-primary):not(.queue-step-icon-button):not(.queue-step-attachment-add-button):not(.queue-step-extension-button):not(.table-sort-button):not(.td-instructions-toggle):active',
      ),
      cssRuleBlock(
        missionControlCss,
        '.button:not(.secondary):not(.queue-action):not(.queue-submit-primary):active',
      ),
      cssRuleBlock(missionControlCss, '.queue-action:active,\n.queue-submit-primary:active'),
      cssRuleBlock(missionControlCss, '.queue-step-icon-button:active'),
      cssRuleBlock(missionControlCss, '.queue-step-icon-button.destructive:active'),
    ];

    for (const block of pressedBlocks) {
      expect(block).toContain('scale(var(--mm-control-press-scale))');
      expect(block).not.toContain('translateY');
    }
  });

  it('aligns compact controls, focus rings, disabled states, and reduced motion', async () => {
    const inlineToggleBlock = cssRuleBlock(missionControlCss, '.queue-inline-toggle {');
    expect(inlineToggleBlock).toContain('padding: 0');
    expect(inlineToggleBlock).not.toContain('background: var(--mm-control-shell)');
    expect(inlineToggleBlock).not.toContain('border: 1px solid var(--mm-control-border)');

    const inlineFilterBlock = cssRuleBlock(missionControlCss, '.queue-inline-filter {');
    expect(inlineFilterBlock).toContain('background: var(--mm-control-shell)');
    expect(inlineFilterBlock).toContain('border: 1px solid var(--mm-control-border)');

    const pageSizeSelectorBlock = cssRuleBlock(missionControlCss, '.queue-page-size-selector {');
    expect(pageSizeSelectorBlock).toContain('background: transparent');
    expect(pageSizeSelectorBlock).toContain('border: 0');
    expect(pageSizeSelectorBlock).toContain('box-shadow: none');
    expect(pageSizeSelectorBlock).toContain('transition: var(--mm-control-transition)');

    const filterChipBlock = cssRuleBlock(missionControlCss, '.task-list-filter-chip {');
    expect(filterChipBlock).toContain('background: var(--mm-control-shell)');
    expect(filterChipBlock).toContain('border: 1px solid var(--mm-control-border)');
    expect(cssRuleBlock(missionControlCss, '.queue-inline-filter:focus-within')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(missionControlCss, 'button:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(missionControlCss, 'input:focus-visible,\nselect:focus-visible,\ntextarea:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(missionControlCss, 'button:disabled,\nbutton:disabled:hover,\nbutton.secondary:disabled,\nbutton.secondary:disabled:hover,\nbutton:not(.secondary):not(.queue-action):not(.queue-submit-primary):not(.queue-step-icon-button):not(.queue-step-attachment-add-button):not(.queue-step-extension-button):not(.table-sort-button):not(.td-instructions-toggle):disabled,\nbutton:not(.secondary):not(.queue-action):not(.queue-submit-primary):not(.queue-step-icon-button):not(.queue-step-attachment-add-button):not(.queue-step-extension-button):not(.table-sort-button):not(.td-instructions-toggle):disabled:hover,\n.button[aria-disabled="true"],\n.button[aria-disabled="true"]:hover,\n.button.secondary[aria-disabled="true"],\n.button.secondary[aria-disabled="true"]:hover,\n.button:not(.secondary):not(.queue-action):not(.queue-submit-primary)[aria-disabled="true"],\n.button:not(.secondary):not(.queue-action):not(.queue-submit-primary)[aria-disabled="true"]:hover')).toMatch(
      /opacity:\s*var\(--mm-control-disabled-opacity\);[^}]*transform:\s*none;[^}]*box-shadow:\s*none;/s,
    );
    expect(missionControlCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*button,[^}]*\.button,[^}]*\.queue-action,[^}]*\.queue-submit-primary,[^}]*\.queue-step-icon-button,[^}]*\.queue-step-extension-button,[^}]*\.queue-inline-toggle,[^}]*\.queue-inline-filter,[^}]*\.queue-page-size-selector\s*\{[^}]*transition-duration:\s*0s !important;[^}]*animation-duration:\s*0s !important;[^}]*transform:\s*none !important;/s,
    );
    expect(missionControlCss).toMatch(
      /@media \(forced-colors: active\)\s*\{[^}]*button:focus-visible,[^}]*\.button:focus-visible,[^}]*\.route-nav a:focus-visible,[^}]*\.live-logs-artifact-link:focus-visible,[^}]*\.queue-action:focus-visible,[^}]*\.queue-submit-primary:focus-visible\s*\{[^}]*outline:\s*2px solid ButtonText;[^}]*outline-offset:\s*2px;/s,
    );
  });

  it('lets masthead content and chrome span the page while panels stay constrained', async () => {
    expect(missionControlCss).toMatch(
      /\.dashboard-shell-full\s*\{[^}]*width:\s*100%/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead::before\s*\{[^}]*left:\s*calc\(50% - 50cqw - 1rem\);[^}]*right:\s*calc\(50% - 50cqw - 1rem\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead::after\s*\{[^}]*left:\s*calc\(50% - 50cqw - 1rem\);[^}]*right:\s*calc\(50% - 50cqw - 1rem\);/s,
    );
  });

  it('keeps the masthead brand left, navigation centered, and version aligned right on desktop', async () => {
    const { readFileSync } = await import('node:fs');
    const missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      'utf8',
    );

    expect(missionControlCss).toMatch(
      /\.masthead\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto\s+minmax\(0,\s*1fr\);/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead-brand\s*\{[^}]*justify-self:\s*start;/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead-nav\s*\{[^}]*justify-content:\s*center;[^}]*justify-self:\s*center;/s,
    );
    expect(missionControlCss).toMatch(
      /\.masthead-title-meta\s*\{[^}]*justify-self:\s*end;[^}]*justify-content:\s*flex-end;/s,
    );
  });

  it('keeps the mobile navigation layer above route content panels', async () => {
    const mastheadBlock = cssRuleBlock(missionControlCss, '.masthead');
    expect(mastheadBlock).toContain('position: relative;');
    expect(mastheadBlock).toContain('z-index: 50;');
    expect(mastheadBlock).toContain('isolation: isolate;');

    const navBlocks = cssRuleBlocks(missionControlCss, '.route-nav');
    expect(
      navBlocks.some(
        (block) => block.includes('position: absolute;') && block.includes('z-index: 30;'),
      ),
    ).toBe(true);
  });

  it('keeps the wider masthead breakpoint isolated from the shared mobile layout rules', async () => {
    const { readFileSync } = await import('node:fs');
    const missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      'utf8',
    );

    const mastheadBreakpointStart = missionControlCss.indexOf('@media (max-width: 1180px)');
    const sharedMobileStart = missionControlCss.indexOf('@media (max-width: 900px)');
    const mastheadResponsive = missionControlCss.slice(
      mastheadBreakpointStart,
      sharedMobileStart,
    );
    const sharedMobile = missionControlCss.slice(sharedMobileStart);

    expect(mastheadBreakpointStart).toBeGreaterThanOrEqual(0);
    expect(sharedMobileStart).toBeGreaterThan(mastheadBreakpointStart);
    expect(mastheadResponsive).toContain('.masthead {');
    expect(mastheadResponsive).not.toContain('.grid-2 {');
    expect(sharedMobile).toContain('.grid-2 {');
    expect(sharedMobile).toContain('.queue-submit-form {');
  });

  it('renders an explicit error state for unknown pages', async () => {
    renderWithClient(
      <MissionControlApp payload={{ page: 'not-a-page', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown Mission Control page:/i)).toBeTruthy();
    expect(screen.getByText('not-a-page')).toBeTruthy();
  });

  it('treats inherited object keys as unsupported pages', async () => {
    renderWithClient(
      <MissionControlApp payload={{ page: 'toString', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown Mission Control page:/i)).toBeTruthy();
    expect(screen.getByText('toString')).toBeTruthy();
  });

  it('renders the OAuth terminal page and attaches through the session bridge', async () => {
    const sentFrames: string[] = [];
    const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const clipboardMock = { writeText: vi.fn() };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboardMock,
    });
    try {
      class MockWebSocket extends EventTarget {
        static readonly OPEN = 1;
        readonly OPEN = 1;
        readyState = 1;
        onopen: ((event: Event) => void) | null = null;
        onmessage: ((event: MessageEvent) => void) | null = null;
        onclose: ((event: CloseEvent) => void) | null = null;
        onerror: ((event: Event) => void) | null = null;
        constructor(readonly url: string) {
          super();
          setTimeout(() => {
            this.onopen?.(new Event('open'));
            this.onmessage?.(new MessageEvent('message', { data: 'Ready for login' }));
          }, 0);
        }
        send(frame: string) {
          sentFrames.push(frame);
        }
        close() {
          this.onclose?.(new CloseEvent('close'));
        }
      }
      window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
      fetchSpy.mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/v1/oauth-sessions/oas_terminal_ui') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              session_id: 'oas_terminal_ui',
              status: 'awaiting_user',
              terminal_session_id: 'term_oas_terminal_ui',
              terminal_bridge_id: 'br_oas_terminal_ui',
            }),
          } as Response);
        }
        if (url === '/api/v1/oauth-sessions/oas_terminal_ui/terminal/attach') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              session_id: 'oas_terminal_ui',
              terminal_session_id: 'term_oas_terminal_ui',
              terminal_bridge_id: 'br_oas_terminal_ui',
              websocket_url:
                '/api/v1/oauth-sessions/oas_terminal_ui/terminal/ws?token=once',
              attach_token: 'once',
            }),
          } as Response);
        }
        if (url === '/api/v1/secrets') {
          return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
        }
        if (url === '/api/v1/provider-profiles') {
          return Promise.resolve({ ok: true, json: async () => [] } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: 'Not Found',
          text: async () => 'Unhandled fetch',
        } as Response);
      });

      renderWithClient(
        <MissionControlApp
          payload={{
            page: 'oauth-terminal',
            apiBase: '/api',
            initialData: { sessionId: 'oas_terminal_ui' },
          }}
        />,
      );

      expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
      expect(await screen.findByText('Ready for login')).toBeTruthy();
      const terminalElement = screen.getByTestId('oauth-xterm');
      vi.spyOn(terminalElement, 'getBoundingClientRect').mockReturnValue({
        x: 10,
        y: 20,
        left: 10,
        top: 20,
        right: 90,
        bottom: 60,
        width: 80,
        height: 40,
        toJSON: () => ({}),
      } as DOMRect);

      fireEvent.contextMenu(terminalElement, {
        clientX: 24,
        clientY: 32,
      });
      const copyMenuItem = screen.getByRole('menuitem', { name: 'Copy selection' });
      await waitFor(() => {
        expect(document.activeElement).toBe(copyMenuItem);
      });
      fireEvent.contextMenu(document.body, {
        clientX: 200,
        clientY: 220,
      });
      expect(screen.queryByRole('menuitem', { name: 'Copy selection' })).toBeNull();

      fireEvent.contextMenu(terminalElement, {
        clientX: 0,
        clientY: 0,
      });
      const fallbackMenuItem = screen.getByRole('menuitem', { name: 'Copy selection' });
      const fallbackMenu = fallbackMenuItem.closest('.oauth-terminal-context-menu');
      expect(fallbackMenu).toBeInstanceOf(HTMLElement);
      expect((fallbackMenu as HTMLElement).style.left).toBe('34px');
      expect((fallbackMenu as HTMLElement).style.top).toBe('40px');

      fireEvent.click(fallbackMenuItem);
      expect(clipboardMock.writeText).toHaveBeenCalledWith('Ready for login');
      fireEvent.click(screen.getByRole('button', { name: 'Copy selection' }));
      expect(clipboardMock.writeText).toHaveBeenCalledWith('Ready for login');
      await waitFor(() => {
        expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
      });
      expect(document.body.textContent).not.toContain('Docker exec');
    } finally {
      if (originalClipboardDescriptor) {
        Object.defineProperty(navigator, 'clipboard', originalClipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, 'clipboard');
      }
    }
  });

  it('waits for OAuth terminal readiness before requesting an attach token', async () => {
    const sentFrames: string[] = [];
    const attachCalls: string[] = [];
    const sessionStatuses = [
      { status: 'pending' },
      { status: 'starting' },
      {
        status: 'awaiting_user',
        terminal_session_id: 'term_oas_terminal_wait',
        terminal_bridge_id: 'br_oas_terminal_wait',
      },
    ];

    class MockWebSocket extends EventTarget {
      static readonly OPEN = 1;
      readonly OPEN = 1;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        super();
        setTimeout(() => {
          this.onopen?.(new Event('open'));
          this.onmessage?.(new MessageEvent('message', { data: 'Ready after wait' }));
        }, 0);
      }
      send(frame: string) {
        sentFrames.push(frame);
      }
      close() {
        this.onclose?.(new CloseEvent('close'));
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions/oas_terminal_wait') {
        const nextStatus = sessionStatuses.shift() ?? {
          status: 'awaiting_user',
          terminal_session_id: 'term_oas_terminal_wait',
          terminal_bridge_id: 'br_oas_terminal_wait',
        };
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_terminal_wait',
            ...nextStatus,
          }),
        } as Response);
      }
      if (url === '/api/v1/oauth-sessions/oas_terminal_wait/terminal/attach') {
        attachCalls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_terminal_wait',
            terminal_session_id: 'term_oas_terminal_wait',
            terminal_bridge_id: 'br_oas_terminal_wait',
            websocket_url: '/api/v1/oauth-sessions/oas_terminal_wait/terminal/ws?token=once',
            attach_token: 'once',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: 'Unhandled fetch' }),
      } as Response);
    });

    renderWithClient(
      <MissionControlApp
        payload={{
          page: 'oauth-terminal',
          apiBase: '/api',
          initialData: { sessionId: 'oas_terminal_wait' },
        }}
      />,
    );

    expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions/oas_terminal_wait',
        expect.objectContaining({ headers: { Accept: 'application/json' } }),
      );
    });
    expect(attachCalls).toEqual([]);

    await waitFor(
      () => {
        expect(attachCalls).toEqual([
          '/api/v1/oauth-sessions/oas_terminal_wait/terminal/attach',
        ]);
      },
      { timeout: 3500 },
    );
    expect(await screen.findByText('Ready after wait')).toBeTruthy();
    expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
  });

  it('attaches the OAuth terminal when a Claude session reaches awaiting user', async () => {
    const sentFrames: string[] = [];
    const attachCalls: string[] = [];
    const websocketUrls: string[] = [];
    const sessionStatuses = [
      { status: 'starting' },
      {
        status: 'awaiting_user',
        runtime_id: 'claude_code',
        profile_id: 'claude_anthropic',
        terminal_session_id: 'term_oas_claude_wait',
        terminal_bridge_id: 'br_oas_claude_wait',
      },
    ];

    class MockWebSocket extends EventTarget {
      static readonly OPEN = 1;
      readonly OPEN = 1;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        super();
        websocketUrls.push(url);
        setTimeout(() => {
          this.onopen?.(new Event('open'));
          this.onmessage?.(
            new MessageEvent('message', {
              data: 'Open https://claude.ai/login and paste the returned code',
            }),
          );
        }, 0);
      }
      send(frame: string) {
        sentFrames.push(frame);
      }
      close() {
        this.onclose?.(new CloseEvent('close'));
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions/oas_claude_wait') {
        const nextStatus = sessionStatuses.shift() ?? {
          status: 'awaiting_user',
          runtime_id: 'claude_code',
          profile_id: 'claude_anthropic',
          terminal_session_id: 'term_oas_claude_wait',
          terminal_bridge_id: 'br_oas_claude_wait',
        };
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_claude_wait',
            ...nextStatus,
          }),
        } as Response);
      }
      if (url === '/api/v1/oauth-sessions/oas_claude_wait/terminal/attach') {
        attachCalls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_claude_wait',
            terminal_session_id: 'term_oas_claude_wait',
            terminal_bridge_id: 'br_oas_claude_wait',
            websocket_url: '/api/v1/oauth-sessions/oas_claude_wait/terminal/ws?token=once',
            attach_token: 'once',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: 'Unhandled fetch' }),
      } as Response);
    });

    renderWithClient(
      <MissionControlApp
        payload={{
          page: 'oauth-terminal',
          apiBase: '/api',
          initialData: { sessionId: 'oas_claude_wait' },
        }}
      />,
    );

    expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
    expect(attachCalls).toEqual([]);

    await waitFor(
      () => {
        expect(attachCalls).toEqual([
          '/api/v1/oauth-sessions/oas_claude_wait/terminal/attach',
        ]);
      },
      { timeout: 3500 },
    );
    expect(websocketUrls).toEqual([
      'ws://localhost:3000/api/v1/oauth-sessions/oas_claude_wait/terminal/ws?token=once',
    ]);
    expect(await screen.findByText(/Open https:\/\/claude\.ai\/login/)).toBeTruthy();
    expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
  });
});
