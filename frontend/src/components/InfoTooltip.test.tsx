import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { InfoTooltip } from './InfoTooltip';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('does not open help while the pointer passes across a neighboring help control', () => {
  vi.useFakeTimers();
  render(<><InfoTooltip label="Author help" panelId="authors-help" title="Author help">Author scope</InfoTooltip>
    <label><input type="checkbox" />Run verification</label></>);
  const help = screen.getByRole('button', { name: 'Author help' });
  fireEvent.mouseEnter(help);
  expect(screen.queryByRole('tooltip')).toBeNull();
  act(() => vi.advanceTimersByTime(100));
  fireEvent.mouseLeave(help);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Run verification' }));
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
});

it('opens intentional hover after a short delay and persists while the pointer enters the help', () => {
  vi.useFakeTimers();
  render(<InfoTooltip label="Author help" panelId="authors-help" title="Author help">Author scope</InfoTooltip>);
  const help = screen.getByRole('button', { name: 'Author help' });
  fireEvent.mouseEnter(help);
  act(() => vi.advanceTimersByTime(350));
  const tooltip = screen.getByRole('tooltip');
  fireEvent.mouseLeave(help);
  fireEvent.mouseEnter(tooltip);
  act(() => vi.advanceTimersByTime(500));
  expect(screen.getByRole('tooltip')).toBe(tooltip);
  fireEvent.mouseLeave(tooltip);
  act(() => vi.advanceTimersByTime(120));
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('keeps focus and touch activation immediate and dismisses keyboard help on Escape', () => {
  vi.useFakeTimers();
  render(<InfoTooltip label="Author help" panelId="authors-help" title="Author help">Author scope</InfoTooltip>);
  const help = screen.getByRole('button', { name: 'Author help' });
  fireEvent.mouseEnter(help);
  fireEvent.focus(help);
  expect(screen.getByRole('tooltip')).toBeTruthy();
  fireEvent.keyDown(help, { key: 'Escape' });
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.click(help);
  expect(screen.getByRole('tooltip')).toBeTruthy();
  fireEvent.click(help);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('clamps help inside the usable viewport rather than under its scrollbar', () => {
  vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(375);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const tooltip = this.getAttribute('role') === 'tooltip';
    return { x: 350, y: 400, left: 350, top: 400, right: 374, bottom: 424,
      width: tooltip ? 352 : 24, height: tooltip ? 60 : 24, toJSON() {} };
  });
  render(<InfoTooltip label="Help" panelId="viewport-help" title="Help">Explanation</InfoTooltip>);
  fireEvent.focus(screen.getByRole('button', { name: 'Help' }));
  expect(screen.getByRole('tooltip').style.left).toBe('15px');
});
