// Computed-colour helpers for real-browser style checks. Browsers serialize
// computed colours differently (rgb(), color(srgb ...), oklch()), so every
// colour is normalised by painting it onto a 1x1 canvas.
import { commands, userEvent } from 'vitest/browser';

declare module 'vitest/browser' {
  interface BrowserCommands {
    pressPointer: (selector: string) => Promise<void>;
    releasePointer: () => Promise<void>;
  }
}

export type Rgba = [number, number, number, number];
export type Theme = 'light' | 'dark';

let paint: CanvasRenderingContext2D | null = null;

export function toRgba(color: string): Rgba {
  if (!paint) {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    paint = canvas.getContext('2d', { willReadFrequently: true });
    if (!paint) throw new Error('2d canvas is unavailable');
  }
  paint.clearRect(0, 0, 1, 1);
  paint.fillStyle = '#000';
  paint.fillStyle = color;
  paint.fillRect(0, 0, 1, 1);
  const [r = 0, g = 0, b = 0, a = 0] = paint.getImageData(0, 0, 1, 1).data;
  return [r, g, b, a / 255];
}

function over(top: Rgba, bottom: Rgba): Rgba {
  const alpha = top[3] + bottom[3] * (1 - top[3]);
  if (alpha === 0) return [0, 0, 0, 0];
  const channel = (i: 0 | 1 | 2) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / alpha;
  return [channel(0), channel(1), channel(2), alpha];
}

/** The opaque colour behind an element: its own and its ancestors' background colours composited. */
export function effectiveBackground(element: Element, { includeSelf = true } = {}): Rgba {
  const layers: Rgba[] = [];
  for (let node: Element | null = includeSelf ? element : element.parentElement; node; node = node.parentElement) {
    const layer = toRgba(getComputedStyle(node).backgroundColor);
    if (layer[3] > 0) layers.push(layer);
    if (layer[3] >= 1) break;
  }
  return layers.reduceRight<Rgba>((below, layer) => over(layer, below), [255, 255, 255, 1]);
}

function luminance([r, g, b]: Rgba): number {
  const linear = (value: number) => {
    const c = value / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

export function relativeLuminance(color: Rgba): number {
  return luminance(color);
}

export function contrastRatio(a: Rgba, b: Rgba): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Contrast of an element's text colour against the background behind it. */
export function textContrast(element: Element): number {
  const background = effectiveBackground(element);
  return contrastRatio(over(toRgba(getComputedStyle(element).color), background), background);
}

/** Contrast of a visible focus outline against what surrounds the element. */
export function outlineContrast(element: Element): number {
  const style = getComputedStyle(element);
  if (style.outlineStyle === 'none' || Number.parseFloat(style.outlineWidth) < 1) return 0;
  const surround = effectiveBackground(element, { includeSelf: false });
  return contrastRatio(over(toRgba(style.outlineColor), surround), surround);
}

export function sameColor(a: string, b: string): boolean {
  const [x, y] = [toRgba(a), toRgba(b)];
  return x.every((value, i) => Math.abs(value - (y[i] ?? 0)) <= (i === 3 ? 0.02 : 2));
}

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

/** Waits for the element's running CSS transitions to finish so computed styles show the end state. */
export async function settle(element: Element): Promise<void> {
  const finite = element.getAnimations().filter((animation) => {
    const iterations = animation.effect?.getComputedTiming().iterations;
    return iterations !== undefined && Number.isFinite(iterations);
  });
  await Promise.all(finite.map((animation) => animation.finished.catch(() => undefined)));
}

export async function hover(element: HTMLElement): Promise<void> {
  await userEvent.hover(element);
  await settle(element);
}

export async function unhover(element: HTMLElement): Promise<void> {
  await userEvent.unhover(element);
  await settle(element);
}

/** Holds the real mouse button down over `element` while `check` runs, so :active applies. */
export async function whilePressed<T>(element: HTMLElement, check: () => T): Promise<T> {
  const marker = `press-${Math.random().toString(36).slice(2)}`;
  element.dataset.pressTarget = marker;
  await commands.pressPointer(`[data-press-target="${marker}"]`);
  try {
    await settle(element);
    return check();
  } finally {
    await commands.releasePointer();
    delete element.dataset.pressTarget;
  }
}

/** Focuses `element` from the keyboard (Tab from the element before it) so :focus-visible applies. */
export async function keyboardFocus(element: HTMLElement): Promise<void> {
  const before = document.createElement('button');
  before.type = 'button';
  before.textContent = 'before';
  element.before(before);
  before.focus();
  await userEvent.tab();
  before.remove();
  await settle(element);
  if (document.activeElement !== element) {
    throw new Error(`Tab did not move focus to ${element.outerHTML.slice(0, 120)}`);
  }
}
