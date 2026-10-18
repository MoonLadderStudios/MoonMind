import { useSyncExternalStore } from 'react';

/**
 * One shared one-second clock for live elapsed-time displays. The interval runs
 * only while at least one active display is subscribed, so a page with nothing
 * running keeps no timer, and many ticking rows share a single interval.
 */
const TICK_MS = 1000;

let nowMs = Date.now();
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    nowMs = Date.now();
    timer = setInterval(() => {
      nowMs = Date.now();
      listeners.forEach((notify) => notify());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function subscribeIdle(): () => void {
  return () => {};
}

function readNow(): number {
  return nowMs;
}

/**
 * Current time in milliseconds, re-rendering every second while `active`.
 * Inactive callers get a value that does not tick and should not use it for
 * display; pass `active` from the same status that decides whether a duration
 * is live.
 */
export function useLiveClock(active: boolean): number {
  return useSyncExternalStore(active ? subscribe : subscribeIdle, readNow, readNow);
}
