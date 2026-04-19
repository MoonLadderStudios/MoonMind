import html2canvas from "html2canvas";

import "./liquidGL.vendor.js";

type LiquidGLLens = {
  el: HTMLElement;
  updateMetrics?: () => void;
  destroy?: () => void;
  [key: string]: unknown;
};

type LiquidGLCallbacks = {
  init?: (lens: LiquidGLLens) => void;
};

export type LiquidGLOptions = {
  target: string | HTMLElement;
  snapshot?: string;
  resolution?: number;
  refraction?: number;
  bevelDepth?: number;
  bevelWidth?: number;
  frost?: number;
  shadow?: boolean;
  specular?: boolean;
  reveal?: "fade" | false;
  tilt?: boolean;
  tiltFactor?: number;
  magnify?: number;
  on?: LiquidGLCallbacks;
};

type LiquidGLFn = ((options: LiquidGLOptions) => LiquidGLLens | LiquidGLLens[] | undefined) & {
  registerDynamic: (elements: HTMLElement | HTMLElement[] | NodeList) => void;
  syncWith: (config?: Record<string, unknown>) => unknown;
};

declare global {
  interface Window {
    html2canvas?: typeof html2canvas;
    liquidGL?: LiquidGLFn;
    __liquidGLRenderer__?: unknown;
    __liquidGLNoWebGL__?: boolean;
  }
}

if (typeof window !== "undefined" && !window.html2canvas) {
  window.html2canvas = html2canvas;
}

export function getLiquidGL(): LiquidGLFn | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.liquidGL;
}

export function initLiquidGL(
  options: LiquidGLOptions,
): LiquidGLLens | LiquidGLLens[] | undefined {
  const fn = getLiquidGL();
  if (!fn) {
    return undefined;
  }
  return fn(options);
}
