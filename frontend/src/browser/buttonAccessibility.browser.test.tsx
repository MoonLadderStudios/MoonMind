import type {} from '@vitest/browser-playwright';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { userEvent, commands, cdp } from 'vitest/browser';
import { render, cleanup } from '@testing-library/react';
import { Button, type ButtonVariant } from '../components/Button';
import { WorkflowLifecycleStatusPill } from '../components/ExecutionStatusPill';
import { composite, contrast, luminance, textContrast } from './colorContrast';
import '../styles/dashboard.css';

// Issue #4871: exercise the rendered controls, including simultaneous states.
declare module 'vitest/browser' {
  interface BrowserCommands {
    pointerButton: (action: 'down' | 'up') => Promise<void>;
    touchTreatment: (markup: string, css: string) => Promise<{ before: string[]; after: string[]; hoverNone: boolean }>;
  }
}

const variants: ButtonVariant[] = ['primary', 'secondary', 'ghost', 'danger', 'icon'];
const surface = (theme: string) => theme === 'dark' ? 'rgb(20, 18, 34)' : 'rgb(255, 255, 255)';
const treatment = (element: Element) => {
  const style = getComputedStyle(element);
  return [style.backgroundColor, style.backgroundImage, style.color, style.borderColor, style.boxShadow, style.transform, style.filter];
};

afterEach(() => { cleanup(); document.documentElement.classList.remove('dark'); });

describe('dashboard control accessibility', () => {
  for (const theme of ['light', 'dark']) {
    it(`keeps bare buttons neutral (${theme})`, () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { getByRole } = render(<button>Neutral</button>);
      const style = getComputedStyle(getByRole('button'));
      expect(style.backgroundColor).toBe('rgba(0, 0, 0, 0)');
      expect(style.backgroundImage).toBe('none');
      expect(style.color).toBe(getComputedStyle(document.body).color);
    });

    it(`retains keyboard focus during hover (${theme})`, async () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      // Tab from a focused field: Firefox's first Tab from an unfocused document can leave focus on <body>.
      const { getByRole } = render(<><input aria-label="Start" /><Button variant="secondary">Refresh</Button></>);
      getByRole('textbox').focus();
      await userEvent.tab();
      const button = getByRole('button');
      expect(document.activeElement).toBe(button);
      await userEvent.hover(button);
      const style = getComputedStyle(button);
      expect(style.outlineStyle).toBe('solid');
      expect(parseFloat(style.outlineWidth)).toBeGreaterThanOrEqual(2);
      expect(contrast(style.outlineColor, theme === 'dark' ? 'rgb(20, 18, 34)' : 'rgb(255, 255, 255)')).toBeGreaterThanOrEqual(3);
    });

    it(`keeps disabled controls unchanged on hover (${theme})`, async () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { getByRole } = render(<Button disabled>Disabled</Button>);
      const button = getByRole('button');
      const background = getComputedStyle(button).backgroundImage;
      await userEvent.hover(button, { force: true });
      expect(getComputedStyle(button).backgroundImage).toBe(background);
    });

    it(`makes the wordmark readable (${theme})`, () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { getByText } = render(<span className="masthead-brand-moon">Moon</span>);
      expect(contrast(getComputedStyle(getByText('Moon')).color, theme === 'dark' ? 'rgb(20, 18, 34)' : 'rgb(255, 255, 255)')).toBeGreaterThanOrEqual(4.5);
    });
  }

  for (const theme of ['light', 'dark']) for (const variant of variants) {
    it(`renders every state of ${variant} deliberately (${theme})`, async () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const action = vi.fn();
      const fixture = (selected: boolean) => <>
        <Button variant={variant} aria-pressed={selected} onClick={action} style={{ transition: 'none' }}>Action</Button>
        <Button variant={variant} disabled style={{ transition: 'none' }} onClick={action}>Unavailable</Button>
        <a href="#after">After</a>
      </>;
      const { getByRole, rerender } = render(fixture(false));
      const button = getByRole('button', { name: 'Action' });
      const normal = treatment(button);
      expect(textContrast(button, surface(theme))).toBeGreaterThanOrEqual(4.5);
      expect(getComputedStyle(button).backgroundImage).toBe('none');
      await userEvent.hover(button);
      const hovered = treatment(button);
      expect(hovered).not.toEqual(normal);
      expect(getComputedStyle(button).filter).toBe('none');
      expect(getComputedStyle(button).transform).toBe('none');
      expect(textContrast(button, surface(theme))).toBeGreaterThanOrEqual(4.5);
      await commands.pointerButton('down');
      expect(button.matches(':active')).toBe(true);
      expect(treatment(button)).not.toEqual(hovered);
      expect(getComputedStyle(button).boxShadow).toContain('inset');
      expect(luminance(composite(surface(theme), getComputedStyle(button).backgroundColor))).toBeLessThanOrEqual(luminance(composite(surface(theme), hovered[0]!)) + 0.001);
      expect(textContrast(button, surface(theme))).toBeGreaterThanOrEqual(4.5);
      await commands.pointerButton('up');
      await userEvent.keyboard('{Tab}{Shift>}{Tab}{/Shift}');
      expect(button.matches(':focus-visible')).toBe(true);
      await userEvent.hover(button);
      expect(getComputedStyle(button).outlineStyle).toBe('solid');
      expect(contrast(getComputedStyle(button).outlineColor, surface(theme))).toBeGreaterThanOrEqual(3);
      const disabled = getByRole('button', { name: 'Unavailable' });
      const disabledNormal = treatment(disabled);
      await userEvent.hover(disabled, { force: true });
      expect(treatment(disabled)).toEqual(disabledNormal);
      action.mockClear();
      await commands.pointerButton('down');
      expect(treatment(disabled)).toEqual(disabledNormal);
      await commands.pointerButton('up');
      expect(action).not.toHaveBeenCalled();
      rerender(fixture(true));
      expect(treatment(button)).not.toEqual(normal);
      expect(textContrast(button, surface(theme))).toBeGreaterThanOrEqual(4.5);
      await userEvent.hover(getByRole('link', { name: 'After' }));
      const selected = treatment(button);
      await userEvent.hover(button);
      expect(treatment(button)).not.toEqual(selected);
      const selectedHover = treatment(button);
      await commands.pointerButton('down');
      expect(treatment(button)).not.toEqual(selectedHover);
      expect(getComputedStyle(button).boxShadow).toContain('inset');
      await commands.pointerButton('up');
      await userEvent.hover(getByRole('link', { name: 'After' }));
      rerender(fixture(false));
      button.classList.add('active');
      expect(treatment(button)).not.toEqual(normal);
      expect(textContrast(button, surface(theme))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it('provides compact and large sizes without losing the variant', () => {
    const { getByRole } = render(<><Button size="sm">Small</Button><Button>Medium</Button><Button size="lg">Large</Button><a href="#small" className="mm-button mm-button--secondary mm-button--sm">Small link</a></>);
    expect(getByRole('button', { name: 'Small' }).getBoundingClientRect().height).toBeLessThan(getByRole('button', { name: 'Medium' }).getBoundingClientRect().height);
    expect(getByRole('button', { name: 'Large' }).getBoundingClientRect().height).toBeGreaterThan(getByRole('button', { name: 'Medium' }).getBoundingClientRect().height);
    expect(getByRole('link', { name: 'Small link' }).getBoundingClientRect().height).toBe(getByRole('button', { name: 'Small' }).getBoundingClientRect().height);
  });

  it.skipIf(!navigator.userAgent.includes('Chrome'))('keeps touch taps free of sticky hover', async () => {
    const { container } = render(<Button style={{ transition: 'none' }}>Touch action</Button>);
    const css = [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules].map((rule) => rule.cssText)).join('\n');
    const result = await commands.touchTreatment(container.innerHTML, css);
    expect(result.hoverNone).toBe(true);
    expect(result.before[0]).not.toBe('rgba(0, 0, 0, 0)');
    expect(result.after).toEqual(result.before);
  });

  it.skipIf(!navigator.userAgent.includes('Chrome'))('preserves focus on links, toast close and focusable unavailable menu items in forced colors', async () => {
    const protocol = cdp();
    await protocol.send('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value: 'active' }] });
    try {
      const { container } = render(<><a href="#artifact">Artifact</a><button className="dashboard-toast__close">Close</button><button className="td-workflow-actions-item td-workflow-actions-item-disabled" role="menuitem" aria-disabled="true">Unavailable</button></>);
      for (const control of container.querySelectorAll('a, button')) {
        await userEvent.tab();
        expect(document.activeElement).toBe(control);
        expect(getComputedStyle(control).outlineStyle).toBe('solid');
        expect(parseFloat(getComputedStyle(control).outlineWidth)).toBeGreaterThanOrEqual(2);
      }
    } finally {
      await protocol.send('Emulation.setEmulatedMedia', { features: [] });
    }
  });

  it('provides an accessible continuous executing label', () => {
    const { container } = render(<WorkflowLifecycleStatusPill status="executing" />);
    const readable = [...container.querySelectorAll('span')].find((node) => node.childNodes.length === 1 && node.firstChild?.nodeType === Node.TEXT_NODE && node.textContent === 'Executing' && !node.closest('[aria-hidden="true"]'));
    expect(readable).toBeTruthy();
    const selection = document.createRange();
    selection.selectNodeContents(container.querySelector('.status')!);
    expect(selection.toString()).toBe('Executing');
  });
});
