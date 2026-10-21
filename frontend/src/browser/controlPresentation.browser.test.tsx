import type {} from '@vitest/browser-playwright';
import { afterEach, describe, expect, it } from 'vitest';
import { cdp, commands, page, userEvent } from 'vitest/browser';
import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Button } from '../components/Button';
import { WorkflowActionsMenu } from '../components/WorkflowActionsMenu';
import { DashboardSystemMenu } from '../components/DashboardSystemMenu';
import { WorkflowLifecycleStatusPill } from '../components/ExecutionStatusPill';
import { contrast, paint, textContrast } from './colorContrast';
import '../styles/dashboard.css';

afterEach(async () => {
  cleanup();
  document.documentElement.classList.remove('dark');
  await page.viewport(1280, 800);
});

// Replaces the touched CSS-source assertions for issue #4871. These fixtures
// exercise retained component geometry and simultaneous interaction states.
describe('rendered dashboard control presentation', () => {
  for (const theme of ['light', 'dark']) {
    const surface = theme === 'dark' ? 'rgb(20, 18, 34)' : 'rgb(255, 255, 255)';
    it(`gives workflow action triggers a border, fill and interaction states (${theme})`, async () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { getByRole } = render(<WorkflowActionsMenu
        items={[{ id: 'rerun', label: 'Rerun' }]}
        triggerContent="⋯"
        triggerAriaLabel="More actions"
        triggerClassName="td-workflow-actions-trigger td-workflow-actions-trigger-compact"
      />);
      const trigger = getByRole('button', { name: 'More actions' });
      trigger.style.transition = 'none';
      const normal = getComputedStyle(trigger).backgroundColor;
      expect(normal).not.toBe('rgba(0, 0, 0, 0)');
      expect(getComputedStyle(trigger).borderTopStyle).toBe('solid');
      expect(parseFloat(getComputedStyle(trigger).borderTopWidth)).toBeGreaterThanOrEqual(1);
      expect(trigger.getBoundingClientRect().width).toBe(32);
      await userEvent.hover(trigger);
      const hovered = getComputedStyle(trigger).backgroundColor;
      expect(hovered).not.toBe(normal);
      await commands.pointerButton('down');
      try {
        expect(getComputedStyle(trigger).backgroundColor).not.toBe(hovered);
        expect(getComputedStyle(trigger).boxShadow).toContain('inset');
      } finally {
        await commands.pointerButton('up');
      }
      expect(getByRole('menu')).toBeTruthy();
      await userEvent.keyboard('{Escape}');
      expect(document.activeElement).toBe(trigger);
    });

    it(`keeps status pills, notices, badges and common text readable (${theme})`, () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { container } = render(<div style={{ background: surface }}>
        {['executing', 'scheduled', 'initializing', 'planning', 'finalizing', 'waiting_on_dependencies', 'completed', 'failed', 'canceled'].map((status) => <WorkflowLifecycleStatusPill key={status} status={status} />)}
        {['ok', 'pending', 'error', 'warning', 'subtle'].map((kind) => <p className={`notice ${kind}`} key={kind}>{kind}</p>)}
        {['success', 'warning', 'error'].map((kind) => <span className={`badge badge-${kind}`} key={kind}>{kind}</span>)}
        <label>Field label<input placeholder="Helpful placeholder" /></label>
        <span className="workflow-list-filter-chip">Filter</span>
        <span className="field-hint">Field hint</span>
      </div>);
      for (const element of container.querySelectorAll('.status, .notice, .badge, label, .workflow-list-filter-chip, .field-hint')) {
        expect(textContrast(element, surface), element.className).toBeGreaterThanOrEqual(4.5);
      }
      for (const pill of container.querySelectorAll('.status[data-effect="shimmer-sweep"]')) {
        const label = pill.querySelector('.status-label, .status-label')!;
        const labelOverlay = getComputedStyle(label, '::after');
        expect(['none', 'normal']).toContain(labelOverlay.content);
        const style = getComputedStyle(pill);
        const light = paint(style.getPropertyValue('--mm-status-shimmer-core'), style.getPropertyValue('--mm-status-shimmer-halo'));
        const background = paint(surface, style.backgroundColor);
        const opacity = Number(getComputedStyle(pill, '::before').opacity);
        const brightestFill = background.slice(0, 3).map((channel, i) => Math.min(255, channel + light[i]! * light[3]! / 255 * opacity));
        expect(contrast(style.color, `rgb(${brightestFill.join(' ')})`), pill.className).toBeGreaterThanOrEqual(4.5);
      }
      const input = container.querySelector('input')!;
      expect(contrast(getComputedStyle(input, '::placeholder').color, surface, getComputedStyle(input).backgroundColor)).toBeGreaterThanOrEqual(4.5);
      // Shimmer may move behind the text but must not brighten the label.
      expect(getComputedStyle(container.querySelector('.status-label')!, '::after').filter).toBe('none');
    });

    it(`separates hover, open, applied and keyboard focus on filter triggers (${theme})`, async () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const classes = ['workflow-list-column-filter-button', 'workflow-list-filter-trigger', 'workflow-list-view-options-trigger--icon', 'workflow-list-advanced-filters-trigger'];
      const { getByRole } = render(<>{classes.map((className) => <button key={className} className={className} aria-label={className} style={{ transition: 'none' }}>⋯</button>)}<a href="#after-filters">After filters</a></>);
      for (const className of classes) {
        await userEvent.hover(getByRole('link', { name: 'After filters' }));
        const trigger = getByRole('button', { name: className });
        const normal = getComputedStyle(trigger).backgroundColor;
        await userEvent.hover(trigger);
        const hover = getComputedStyle(trigger).backgroundColor;
        expect(hover).not.toBe(normal);
        trigger.setAttribute('aria-expanded', 'true');
        const open = getComputedStyle(trigger).backgroundColor;
        expect(open).not.toBe(hover);
        trigger.removeAttribute('aria-expanded');
        trigger.classList.add('is-active');
        expect(getComputedStyle(trigger).backgroundColor).not.toBe(open);
        trigger.focus();
        await userEvent.keyboard('{Tab}{Shift>}{Tab}{/Shift}');
        expect(getComputedStyle(trigger).outlineStyle).toBe('solid');
        expect(contrast(getComputedStyle(trigger).outlineColor, surface)).toBeGreaterThanOrEqual(3);
      }
    });

    it(`keeps mobile navigation readable in resting, hovered and selected states (${theme})`, async () => {
      await page.viewport(360, 800);
      document.documentElement.classList.toggle('dark', theme === 'dark');
      const { container, getByRole } = render(<nav className="route-nav route-nav--open"><div className="route-nav-primary"><a href="#workflows">Workflows</a><a href="#skills" className="active">Skills</a></div></nav>);
      const navBackground = getComputedStyle(container.querySelector('nav')!).backgroundColor;
      for (const link of container.querySelectorAll('a')) {
        const check = () => {
          const style = getComputedStyle(link);
          const fills = style.backgroundImage.match(/rgba?\([^)]+\)/g) ?? [style.backgroundColor];
          for (const fill of fills) expect(contrast(style.color, surface, navBackground, fill)).toBeGreaterThanOrEqual(4.5);
        };
        check();
        await userEvent.hover(link);
        check();
      }
      expect(getByRole('link', { name: 'Skills' })).toBeTruthy();
    });
  }

  it('keeps the combobox chevron chromeless, rotates it when expanded, and retains keyboard focus', async () => {
    const { getByRole } = render(<div className="skill-combobox" style={{ width: '20rem', position: 'relative' }}><button className="skill-combobox-toggle" aria-expanded="false" aria-label="Skills"><svg viewBox="0 0 10 10"><path d="M0 0L5 5L10 0" /></svg></button></div>);
    const toggle = getByRole('button');
    const check = () => {
      const style = getComputedStyle(toggle);
      expect(style.backgroundColor).toBe('rgba(0, 0, 0, 0)');
      expect(style.boxShadow).toBe('none');
      expect(style.transform).toBe('none');
      expect(style.filter).toBe('none');
    };
    check();
    await userEvent.hover(toggle);
    check();
    await userEvent.tab();
    check();
    expect(getComputedStyle(toggle).outlineStyle).toBe('solid');
    const before = getComputedStyle(toggle.querySelector('svg')!).transform;
    toggle.setAttribute('aria-expanded', 'true');
    // Rotation is animated; compare the resolved transform after it settles.
    await expect.poll(() => getComputedStyle(toggle.querySelector('svg')!).transform).not.toBe(before);
  });

  it('preserves compact wordmark and System popover geometry with visible focus', async () => {
    const { container } = render(<header className="masthead"><a className="masthead-brand" href="#workflows"><h1 className="masthead-wordmark"><span className="masthead-brand-moon">Moon</span><span className="masthead-brand-mind">Mind</span></h1></a><div className="masthead-nav"><nav className="route-nav"><div className="route-nav-primary"><a className="active" href="#create">Create</a></div><div className="dashboard-system-menu"><button className="dashboard-system-trigger active">System</button><div className="dashboard-system-popover"><a className="active" href="#settings">Settings</a></div></div></nav></div></header>);
    const brand = container.querySelector('a')!;
    expect(getComputedStyle(brand).textDecorationLine).toBe('none');
    expect(parseFloat(getComputedStyle(container.querySelector('h1')!).fontSize)).toBeLessThanOrEqual(24.32);
    const popover = container.querySelector('.dashboard-system-popover')!;
    expect(getComputedStyle(popover).position).toBe('absolute');
    expect(getComputedStyle(popover).zIndex).toBe('60');
    expect(popover.getBoundingClientRect().right).toBeLessThanOrEqual(window.innerWidth);
    expect(getComputedStyle(container.querySelector('.masthead-nav')!).overflow).toBe('visible');
    for (const control of container.querySelectorAll('a, button')) {
      await userEvent.tab();
      expect(document.activeElement).toBe(control);
      expect(getComputedStyle(control).outlineStyle).toBe('solid');
      if (control.matches('.dashboard-system-popover a.active')) expect(getComputedStyle(control).boxShadow).toContain('inset');
    }
    await page.viewport(360, 800);
    expect(getComputedStyle(container.querySelector('.dashboard-system-menu')!).display).toBe('none');
  });

  it('keeps the list display thumb, selected icons and highlight edges while hiding the control on phones', async () => {
    const { container } = render(<div className="workflow-list-display-control" role="radiogroup"><button className="workflow-list-display-option" role="radio" aria-checked="true">List</button><button className="workflow-list-display-option" role="radio" aria-checked="false">Table</button></div>);
    const control = container.firstElementChild!;
    expect(getComputedStyle(control, '::after').backgroundImage).toContain('radial-gradient');
    const before = getComputedStyle(control, '::before').transform;
    const radios = container.querySelectorAll('button');
    radios[0]!.setAttribute('aria-checked', 'false');
    radios[1]!.setAttribute('aria-checked', 'true');
    await expect.poll(() => getComputedStyle(control, '::before').transform).not.toBe(before);
    const selectedColor = getComputedStyle(radios[1]!).color;
    await userEvent.hover(radios[1]!);
    expect(getComputedStyle(radios[1]!).color).toBe(selectedColor);
    await page.viewport(360, 800);
    expect(getComputedStyle(control).display).toBe('none');
  });

  it('keeps step icon controls within their row and chip removal compact', async () => {
    const { container, getByRole } = render(<div style={{ width: '20rem' }}><div className="queue-step-controls"><Button variant="icon" className="queue-step-icon-button" disabled disabledReason="This is the first step.">Move up</Button><Button variant="icon" className="queue-step-icon-button" disabled disabledReason="This is the last step.">Move down</Button><Button variant="icon" className="queue-step-icon-button">Remove</Button></div><span className="queue-step-capability-chip"><button className="queue-step-icon-button queue-step-capability-chip-remove" aria-label="Remove capability"><svg /></button></span><ul id="queue-dependency-list"><li><span>A long dependency description that wraps to another line</span><Button variant="icon" className="queue-step-icon-button">Remove dependency</Button></li></ul></div>);
    const edge = container.firstElementChild!.getBoundingClientRect().right;
    const toolbar = container.querySelector('.queue-step-controls')!;
    expect(toolbar.getBoundingClientRect().width).toBeLessThan(140);
    for (const button of toolbar.querySelectorAll('button[aria-describedby]')) {
      const reason = document.getElementById(button.getAttribute('aria-describedby')!)!;
      expect(reason.textContent).toMatch(/This is the (first|last) step\./);
      expect(reason.getBoundingClientRect().width).toBeLessThanOrEqual(1);
    }
    await userEvent.hover(getByRole('button', { name: /^Remove$/ }));
    expect(getByRole('button', { name: /^Remove$/ }).getBoundingClientRect().right).toBeLessThanOrEqual(edge);
    const chip = getByRole('button', { name: 'Remove capability' });
    expect(chip.getBoundingClientRect().width).toBeLessThan(14);
    expect(chip.getBoundingClientRect().height).toBeLessThan(14);
    await userEvent.hover(chip);
    expect(getComputedStyle(chip).backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(getComputedStyle(container.querySelector('#queue-dependency-list li')!).display).toBe('grid');
  });

  it.skipIf(!navigator.userAgent.includes('Chrome')).each(['light', 'dark'])('removes control motion when reduced motion is requested (%s)', async (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    const protocol = cdp();
    await protocol.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    try {
      expect(matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true);
      const { getByRole } = render(<>
        <Button>Action</Button>
        <MemoryRouter initialEntries={['/workflows']}>
          <DashboardSystemMenu uiInfo={null} availabilityUnknown mobileDrawerOpen={false} />
        </MemoryRouter>
        <div className="workflow-list-display-control" role="radiogroup" aria-label="List display">
          <button className="workflow-list-display-option" role="radio" aria-checked="true">List</button>
          <button className="workflow-list-display-option" role="radio" aria-checked="false">Table</button>
        </div>
      </>);
      expect(getComputedStyle(getByRole('button', { name: 'Action' })).transitionDuration).toBe('0s');

      const trigger = getByRole('button', { name: 'System' });
      const checkNoMotion = (element: Element) => {
        const style = getComputedStyle(element);
        expect(style.animationName, element.className).toBe('none');
        expect(style.transitionDuration, element.className).toBe('0s');
        expect(style.transform, element.className).toBe('none');
      };
      checkNoMotion(trigger);
      await userEvent.hover(trigger);
      checkNoMotion(trigger);
      trigger.focus();
      await userEvent.keyboard('{ArrowDown}');
      expect(trigger.getAttribute('aria-expanded')).toBe('true');
      await expect.poll(() => getByRole('menu', { name: 'System' }).contains(document.activeElement)).toBe(true);
      checkNoMotion(trigger);
      checkNoMotion(getByRole('menu', { name: 'System' }));

      const display = getByRole('radiogroup', { name: 'List display' });
      const thumb = () => getComputedStyle(display, '::before');
      expect(thumb().transitionDuration).toBe('0s');
      const before = thumb().transform;
      getByRole('radio', { name: 'List' }).setAttribute('aria-checked', 'false');
      getByRole('radio', { name: 'Table' }).setAttribute('aria-checked', 'true');
      expect(thumb().transitionDuration).toBe('0s');
      expect(thumb().transform).not.toBe(before);

      await userEvent.keyboard('{Escape}');
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
      expect(document.activeElement).toBe(trigger);
    } finally {
      await protocol.send('Emulation.setEmulatedMedia', { features: [] });
    }
  });
});
