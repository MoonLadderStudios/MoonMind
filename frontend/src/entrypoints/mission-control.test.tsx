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

import type { BootPayload } from '../boot/parseBootPayload';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { MissionControlApp } from './mission-control-app';

function normalizeCssSelector(selector: string): string {
  return selector
    .replace(/\s*\{\s*$/, '')
    .trim()
    .replace(/\s+/g, ' ');
}

function cssRuleBlock(css: string, selector: string): string {
  const expectedSelector = normalizeCssSelector(selector);
  const expectedSelectors = selector.split(',').map(normalizeCssSelector);
  let block = '';
  postcss.parse(css).walkRules((rule) => {
    const ruleSelector = normalizeCssSelector(rule.selector);
    const ruleSelectors = rule.selector.split(',').map(normalizeCssSelector);
    if (
      !block &&
      (ruleSelector === expectedSelector ||
        ruleSelectors.includes(expectedSelector) ||
        expectedSelectors.every((expected) => ruleSelectors.includes(expected)))
    ) {
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
    const compactControlBlock = cssRuleBlock(missionControlCss, '.queue-inline-toggle,\n.queue-inline-filter');
    expect(compactControlBlock).toContain('background: var(--mm-control-shell)');
    expect(compactControlBlock).toContain('border: 1px solid var(--mm-control-border)');

    const filterChipBlock = cssRuleBlock(missionControlCss, '.task-list-filter-chip {');
    expect(filterChipBlock).toContain('background: var(--mm-control-shell)');
    expect(filterChipBlock).toContain('border: 1px solid var(--mm-control-border)');
    expect(cssRuleBlock(missionControlCss, '.queue-inline-toggle:focus-within,\n.queue-inline-filter:focus-within')).toContain(
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
      /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*button,[^}]*\.button,[^}]*\.queue-action,[^}]*\.queue-submit-primary,[^}]*\.queue-step-icon-button,[^}]*\.queue-step-extension-button,[^}]*\.queue-inline-toggle,[^}]*\.queue-inline-filter\s*\{[^}]*transition-duration:\s*0s !important;[^}]*animation-duration:\s*0s !important;[^}]*transform:\s*none !important;/s,
    );
    expect(missionControlCss).toMatch(
      /@media \(forced-colors: active\)\s*\{[^}]*button:focus-visible,[^}]*\.button:focus-visible,[^}]*\.queue-action:focus-visible,[^}]*\.queue-submit-primary:focus-visible\s*\{[^}]*outline:\s*2px solid ButtonText;[^}]*outline-offset:\s*2px;/s,
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

      expect(await screen.findByText('Provider Login Terminal')).toBeTruthy();
      expect(await screen.findByText('Ready for login')).toBeTruthy();
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

    expect(await screen.findByText('Provider Login Terminal')).toBeTruthy();
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
});
