/** Composite declared colors as the browser paints them (including color-mix). */
export function paint(...layers: string[]): number[] {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d', { willReadFrequently: true })!;
  for (const color of layers) {
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
  }
  return Array.from(context.getImageData(0, 0, 1, 1).data);
}

export function composite(...layers: string[]): number[] {
  return paint(...layers).slice(0, 3);
}

export function luminance(rgb: number[]): number {
  return rgb.reduce((sum, channel, index) => {
    const value = channel / 255;
    return sum + (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index]!;
  }, 0);
}

export function contrast(foreground: string, ...backgrounds: string[]): number {
  const a = luminance(composite(...backgrounds, foreground));
  const b = luminance(composite(...backgrounds));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

export function textContrast(element: Element, surface: string): number {
  const style = getComputedStyle(element);
  return contrast(style.color, surface, style.backgroundColor);
}
