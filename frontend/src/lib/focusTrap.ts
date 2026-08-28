/**
 * Shared modal focus containment.
 *
 * A visible `aria-modal` surface is the active interaction boundary: Tab and
 * Shift+Tab must cycle inside it instead of walking into the page behind the
 * backdrop. The dashboard action dialog and the attachment lightbox share this
 * loop so the rule is defined once rather than restated per modal.
 */

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function focusableElementsWithin(
  container: HTMLElement | null | undefined,
): HTMLElement[] {
  return Array.from(
    container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
  ).filter((element) => !element.hasAttribute("disabled"));
}

interface TabKeyEvent {
  key: string;
  shiftKey: boolean;
  preventDefault: () => void;
}

/**
 * Wraps Tab from the last focusable element back to the first (and Shift+Tab
 * the other way) inside `container`. Returns true when the event was handled so
 * callers can skip their own Tab handling.
 */
export function trapTabWithin(
  container: HTMLElement | null | undefined,
  event: TabKeyEvent,
): boolean {
  if (event.key !== "Tab") {
    return false;
  }
  const elements = focusableElementsWithin(container);
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (!first || !last) {
    return false;
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}
