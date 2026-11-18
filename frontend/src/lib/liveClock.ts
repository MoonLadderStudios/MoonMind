import { useSyncExternalStore } from 'react';

/**
 * Shared clocks for live time displays. Each interval runs only while at least
 * one active display is subscribed, so a page with nothing live keeps no timer,
 * and many ticking rows share a single interval.
 */
function createClock(tickMs: number) {
  let nowMs = Date.now();
  let timer: ReturnType<typeof setInterval> | null = null;
  const listeners = new Set<() => void>();

  const subscribe = (listener: () => void): (() => void) => {
    listeners.add(listener);
    if (timer === null) {
      nowMs = Date.now();
      timer = setInterval(() => {
        nowMs = Date.now();
        listeners.forEach((notify) => notify());
      }, tickMs);
    }
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0 && timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
  };
  const readNow = (): number => nowMs;
  return { subscribe, readNow };
}

function subscribeIdle(): () => void {
  return () => {};
}

const secondClock = createClock(1000);
/** Minute-resolution text (ages, countdowns) does not need per-second renders. */
const coarseClock = createClock(15_000);

/**
 * Current time in milliseconds, re-rendering every second while `active`.
 * Inactive callers get a value that does not tick and should not use it for
 * display; pass `active` from the same status that decides whether a duration
 * is live.
 */
export function useLiveClock(active: boolean): number {
  return useSyncExternalStore(
    active ? secondClock.subscribe : subscribeIdle,
    secondClock.readNow,
    secondClock.readNow,
  );
}

/** Like {@link useLiveClock}, re-rendering every 15 seconds for minute-level text. */
export function useCoarseClock(active: boolean): number {
  return useSyncExternalStore(
    active ? coarseClock.subscribe : subscribeIdle,
    coarseClock.readNow,
    coarseClock.readNow,
  );
}
