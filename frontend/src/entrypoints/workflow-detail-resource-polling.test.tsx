import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import { QueryClient, QueryObserver } from '@tanstack/react-query';

import { createResourceEvidencePoller } from './workflow-detail';

// MoonLadderStudios/MoonMind#4870: resource evidence stuck in pending or error
// backs off to a capped rate instead of polling every 5s forever, and still
// collects evidence that arrives late.
describe('bridge resource evidence polling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  async function fetchTimesWhile(
    respond: (elapsedMs: number) => Promise<unknown>,
    isTerminal: boolean,
    totalMs: number,
  ) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const poller = createResourceEvidencePoller();
    const fetchedAt: number[] = [];
    const start = Date.now();
    const observer = new QueryObserver(client, {
      queryKey: ['omnigent-bridge-session-resources', 'bridge-1'],
      queryFn: () => {
        const elapsed = Date.now() - start;
        fetchedAt.push(elapsed);
        return respond(elapsed);
      },
      refetchInterval: (query) => poller(query, isTerminal),
      refetchIntervalInBackground: true,
    });
    const unsubscribe = observer.subscribe(() => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(totalMs);
    });
    unsubscribe();
    client.clear();
    return { fetchedAt, observer };
  }

  const gaps = (times: number[]) => times.slice(1).map((time, index) => time - times[index]!);

  it('backs off and caps while the resource request keeps failing', async () => {
    const { fetchedAt } = await fetchTimesWhile(
      () => Promise.reject(new Error('bridge unavailable')),
      false,
      5 * 60_000,
    );

    expect(gaps(fetchedAt).slice(0, 6)).toEqual([5_000, 10_000, 20_000, 40_000, 60_000, 60_000]);
    // A fixed 5s poll would have made 60 requests in five minutes.
    expect(fetchedAt.length).toBeLessThanOrEqual(9);
  });

  it('backs off while evidence stays pending after the workflow ended, then collects late evidence', async () => {
    const { fetchedAt, observer } = await fetchTimesWhile(
      (elapsed) => Promise.resolve({ completeness: elapsed >= 90_000 ? 'complete' : 'pending', groups: [] }),
      true,
      5 * 60_000,
    );

    expect(fetchedAt).toEqual([0, 5_000, 15_000, 35_000, 75_000, 135_000]);
    // The late evidence was collected, and polling stopped once it was complete.
    expect(observer.getCurrentResult().data).toEqual({ completeness: 'complete', groups: [] });
  });

  it('keeps the base rate for a healthy running workflow and stops when terminal evidence is complete', () => {
    const poller = createResourceEvidencePoller();
    const settled = (at: number, completeness: string) => ({
      state: { data: { completeness }, status: 'success', dataUpdatedAt: at, errorUpdatedAt: 0 },
    });

    expect(poller(settled(1, 'pending'), false)).toBe(5_000);
    expect(poller(settled(2, 'pending'), false)).toBe(5_000);
    expect(poller(settled(3, 'partial'), false)).toBe(5_000);
    expect(poller(settled(4, 'complete'), true)).toBe(false);
  });
});
