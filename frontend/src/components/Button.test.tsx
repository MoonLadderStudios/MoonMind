import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Button } from './Button';

afterEach(cleanup);

it('preserves native form behavior, refs, selected state and accessible names', () => {
  const submit = vi.fn((event) => event.preventDefault());
  const ref = { current: null as HTMLButtonElement | null };
  render(<form onSubmit={submit}><Button ref={ref} type="submit" variant="primary" aria-pressed="true">Save</Button></form>);
  const save = screen.getByRole('button', { name: 'Save', pressed: true });
  expect(ref.current).toBe(save);
  fireEvent.click(save);
  expect(submit).toHaveBeenCalledOnce();
});

it('prevents native and focusable disabled actions while preserving their explanation', () => {
  const click = vi.fn();
  render(<><p id="reason">Connect a provider first</p><Button disabled onClick={click}>Save</Button><Button aria-disabled="true" aria-describedby="reason" onClick={click}>Connect</Button></>);
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  const connect = screen.getByRole('button', { name: 'Connect' });
  fireEvent.click(connect);
  connect.focus();
  expect(document.activeElement).toBe(connect);
  expect(connect.getAttribute('aria-describedby')).toBe('reason');
  expect(click).not.toHaveBeenCalled();
});

it('shows and associates a disabled reason without relying on title', () => {
  render(<Button disabled disabledReason="Connect a provider first">Start</Button>);
  const button = screen.getByRole('button', { name: 'Start' });
  const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
  expect(reason?.textContent).toBe('Connect a provider first');
});
