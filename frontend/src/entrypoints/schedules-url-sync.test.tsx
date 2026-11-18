import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import { SchedulesPage } from './schedules';
import { navigateTo } from '../lib/navigation';

let observeSearch: (search: string) => void;
vi.mock('../lib/navigation', () => ({ navigateTo: vi.fn() }));
vi.mock('../components/RouterSearchListener', () => ({
  RouterSearchListener: ({ onSearchChange }: { onSearchChange: (search: string) => void }) => {
    observeSearch = onSearchChange;
    return null;
  },
}));

beforeEach(() => {
  window.history.replaceState({}, '', '/schedules');
  vi.mocked(navigateTo).mockReset();
  vi.spyOn(window, 'fetch').mockResolvedValue({
    ok: true, json: async () => ({ items: [] }),
  } as Response);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('does not let a delayed own URL echo erase newer filter input, but follows external navigation', async () => {
  renderWithClient(<SchedulesPage payload={{ page: 'schedules', apiBase: '/api', initialData: {} }} />);
  await waitFor(() => expect(navigateTo).toHaveBeenCalled());
  const initialWrite = String(vi.mocked(navigateTo).mock.calls.at(-1)![0]);
  fireEvent.click(screen.getByRole('button', { name: 'Filter recurring schedules' }));
  const input = screen.getByRole('textbox', { name: 'Schedule filter value' }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: 'nightly' } });
  // A router effect can observe our initial canonicalization after newer input.
  act(() => observeSearch(new URL(initialWrite, window.location.origin).search));
  expect(input.value).toBe('nightly');
  await waitFor(() => expect(String(vi.mocked(navigateTo).mock.calls.at(-1)?.[0])).toContain('schedule=nightly'));
  const filterWrite = String(vi.mocked(navigateTo).mock.calls.at(-1)![0]);
  act(() => observeSearch(new URL(filterWrite, window.location.origin).search));
  act(() => observeSearch('?schedule=weekly'));
  expect(input.value).toBe('weekly');
});

it('forgets older own writes when the router coalesces to a newer query', async () => {
  renderWithClient(<SchedulesPage payload={{ page: 'schedules', apiBase: '/api', initialData: {} }} />);
  await waitFor(() => expect(navigateTo).toHaveBeenCalled());
  const initialSearch = new URL(String(vi.mocked(navigateTo).mock.calls.at(-1)![0]), window.location.origin).search;
  fireEvent.click(screen.getByRole('button', { name: 'Filter recurring schedules' }));
  const input = screen.getByRole('textbox', { name: 'Schedule filter value' }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: 'nightly' } });
  await waitFor(() => expect(String(vi.mocked(navigateTo).mock.calls.at(-1)?.[0])).toContain('schedule=nightly'));
  const writtenSearch = new URL(String(vi.mocked(navigateTo).mock.calls.at(-1)![0]), window.location.origin).search;
  fireEvent.change(input, { target: { value: 'newer' } });
  act(() => observeSearch(writtenSearch));
  expect(input.value).toBe('newer');
  // The initial write was superseded, so a later external return is not ignored.
  act(() => observeSearch(initialSearch));
  expect(input.value).toBe('');
});
