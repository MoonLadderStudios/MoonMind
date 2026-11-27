import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * Phone layouts collapse the Create submit bar to one line and slide it away
 * while a form field is edited, so it never sits between the field and the
 * software keyboard.
 */
export const CREATE_SUBMIT_BAR_NARROW_QUERY = "(max-width: 640px)";

// Tabbing between fields fires focusout before the next focusin. Waiting this
// long before showing the bar again keeps it from flickering between fields.
export const CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS = 150;

// A software keyboard shrinks the visual viewport well below the layout
// viewport. Pinch zoom shrinks it too, but raises `scale` by the same factor,
// so the scaled height separates the two. Only touch devices raise one; on a
// desktop a short visual viewport comes from resizing or embedding instead.
const KEYBOARD_VIEWPORT_RATIO = 0.75;

// Once docked, the bar stays docked until its slot sits this far below the
// floating position, so a few pixels of height difference cannot flip it.
const DOCK_RELEASE_PX = 12;

const NON_TEXT_INPUT_TYPES = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "hidden",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

/** Fields that take typed text or open a picker over the lower screen. */
export function isTextEntryField(element: Element | null): boolean {
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
    return true;
  }
  if (element instanceof HTMLInputElement) {
    return !NON_TEXT_INPUT_TYPES.has(element.type);
  }
  return element instanceof HTMLElement && element.isContentEditable === true;
}

/** Tracks a CSS media query; false where matchMedia is unavailable (jsdom). */
export function useMediaQueryMatch(query: string): boolean {
  const [matches, setMatches] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return undefined;
    }
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener?.("change", update);
    return () => list.removeEventListener?.("change", update);
  }, [query]);
  return matches;
}

/**
 * Owns the Create submit bar's position: hidden while a form field is being
 * edited, and docked in the page flow once the form's end is scrolled into
 * view.
 *
 * Docking measures an in-flow slot that holds the floating bar's height. The
 * slot sits where the docked bar goes, so switching states never moves the
 * fields above it and the document height stays the same; the bar docks when
 * the slot has risen to the floating bar's own position, so it does not jump.
 *
 * `revealKey` keeps a new submit outcome visible even while a field has focus
 * (a failed check focuses the field it names) until the operator types into
 * a form field again.
 */
export function useCreateFloatingSubmitBar({
  formRef,
  barRef,
  slotRef,
  hideWhileEditing,
  revealKey,
}: {
  formRef: RefObject<HTMLFormElement | null>;
  barRef: RefObject<HTMLElement | null>;
  slotRef: RefObject<HTMLElement | null>;
  hideWhileEditing: boolean;
  revealKey: object | null;
}): { hidden: boolean; docked: boolean } {
  const [focus, setFocus] = useState({ fieldEditing: false, barFocused: false });
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [docked, setDocked] = useState(false);
  const [acknowledgedRevealKey, setAcknowledgedRevealKey] = useState<object | null>(null);
  const revealKeyRef = useRef(revealKey);
  const acknowledgedRevealKeyRef = useRef<object | null>(null);
  useEffect(() => {
    revealKeyRef.current = revealKey;
  }, [revealKey]);

  useEffect(() => {
    const form = formRef.current;
    if (!form) {
      return undefined;
    }
    let settleTimer: number | null = null;
    const cancelSettle = () => {
      if (settleTimer !== null) {
        window.clearTimeout(settleTimer);
        settleTimer = null;
      }
    };
    const readFocus = () => {
      const active = document.activeElement;
      const bar = barRef.current;
      const barFocused = Boolean(bar && active && bar.contains(active));
      const fieldEditing =
        !barFocused && Boolean(active && form.contains(active)) && isTextEntryField(active);
      setFocus((current) =>
        current.fieldEditing === fieldEditing && current.barFocused === barFocused
          ? current
          : { fieldEditing, barFocused },
      );
    };
    const settle = () => {
      cancelSettle();
      settleTimer = window.setTimeout(() => {
        settleTimer = null;
        readFocus();
      }, CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS);
    };
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      // Entering a field hides the bar at once and entering the bar shows it at
      // once; anything else settles first so passing through does not flash it.
      if (isTextEntryField(target) || (target && barRef.current?.contains(target))) {
        cancelSettle();
        readFocus();
      } else {
        settle();
      }
    };
    const onInput = (event: Event) => {
      if (event.target instanceof Node && barRef.current?.contains(event.target)) {
        return;
      }
      const key = revealKeyRef.current;
      if (key !== null && acknowledgedRevealKeyRef.current !== key) {
        acknowledgedRevealKeyRef.current = key;
        setAcknowledgedRevealKey(key);
      }
    };
    form.addEventListener("focusin", onFocusIn);
    form.addEventListener("focusout", settle);
    form.addEventListener("input", onInput);
    return () => {
      cancelSettle();
      form.removeEventListener("focusin", onFocusIn);
      form.removeEventListener("focusout", settle);
      form.removeEventListener("input", onInput);
    };
  }, [formRef, barRef]);

  const dockedRef = useRef(false);
  const keyboardOpenRef = useRef(false);
  const floatingGapRef = useRef(0);
  const floatingHeightRef = useRef(0);
  useEffect(() => {
    const bar = barRef.current;
    const slot = slotRef.current;
    if (!bar || !slot) {
      return undefined;
    }
    const update = () => {
      const viewport = window.visualViewport;
      const layoutHeight = document.documentElement.clientHeight || window.innerHeight;
      const keyboard =
        Boolean(viewport) &&
        navigator.maxTouchPoints > 0 &&
        viewport!.height * viewport!.scale < layoutHeight * KEYBOARD_VIEWPORT_RATIO;
      if (keyboard !== keyboardOpenRef.current) {
        keyboardOpenRef.current = keyboard;
        setKeyboardOpen(keyboard);
      }
      const barHeight = bar.offsetHeight;
      if (barHeight <= 0) {
        // Not laid out (for example under jsdom): keep the floating default.
        return;
      }
      const wasDocked = dockedRef.current;
      if (!wasDocked) {
        floatingGapRef.current = Number.parseFloat(getComputedStyle(bar).bottom) || 0;
        floatingHeightRef.current = barHeight;
      }
      // A docked bar occupies the slot's place in the flow. Both directions
      // compare against the floating height: the docked bar can wrap to a
      // different height, which must not flip it straight back.
      const slotTop = (wasDocked ? bar : slot).getBoundingClientRect().top;
      const floatingTop = layoutHeight - floatingGapRef.current - floatingHeightRef.current;
      const nextDocked = slotTop <= floatingTop + (wasDocked ? DOCK_RELEASE_PX : 0);
      if (!wasDocked) {
        const size = `${barHeight}px`;
        if (slot.style.blockSize !== size) {
          slot.style.blockSize = size;
        }
      }
      if (nextDocked !== wasDocked) {
        dockedRef.current = nextDocked;
        setDocked(nextDocked);
      }
    };
    let frameId: number | null = null;
    const schedule = () => {
      if (frameId !== null) {
        return;
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        update();
      });
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", schedule);
    viewport?.addEventListener("scroll", schedule);
    // Fields added or removed above the slot move it without a scroll, and
    // submit messages or the expanded controls change the bar's height.
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver === "function") {
      observer = new ResizeObserver(schedule);
      observer.observe(bar);
      if (formRef.current) {
        observer.observe(formRef.current);
      }
    }
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      viewport?.removeEventListener("resize", schedule);
      viewport?.removeEventListener("scroll", schedule);
      observer?.disconnect();
    };
  }, [formRef, barRef, slotRef]);

  const revealed = revealKey !== null && revealKey !== acknowledgedRevealKey;
  const editing = (hideWhileEditing && focus.fieldEditing) || keyboardOpen;
  return {
    hidden: editing && !focus.barFocused && !docked && !revealed,
    docked,
  };
}
