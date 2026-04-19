import { useEffect } from "react";

import { getLiquidGL, type LiquidGLOptions } from "./index";

const INITIALIZED_ATTR = "data-liquid-gl-initialized";
type LiquidGLInstance = { destroy?: () => void };

type UseLiquidGLArgs = {
  enabled?: boolean;
  options: LiquidGLOptions;
};

export function useLiquidGL({ enabled = true, options }: UseLiquidGLArgs): void {
  useEffect(() => {
    if (!enabled) {
      return;
    }
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    if (typeof options.target !== "string") {
      return;
    }

    let initializedElement: HTMLElement | null = null;
    let liquidGLInstances: LiquidGLInstance[] = [];

    const frame = window.requestAnimationFrame(() => {
      const element = document.querySelector<HTMLElement>(options.target as string);
      if (!element) {
        return;
      }
      if (element.hasAttribute(INITIALIZED_ATTR)) {
        return;
      }

      const liquidGL = getLiquidGL();
      if (!liquidGL) {
        return;
      }

      try {
        const result = liquidGL(options);
        liquidGLInstances = Array.isArray(result) ? result : result ? [result] : [];
        element.style.pointerEvents = "auto";
        element.setAttribute(INITIALIZED_ATTR, "true");
        initializedElement = element;
      } catch (error) {
        console.warn("liquidGL initialization failed", error);
      }
    });

    return () => {
      window.cancelAnimationFrame(frame);
      liquidGLInstances.forEach((instance) => {
        instance.destroy?.();
      });
      initializedElement?.removeAttribute(INITIALIZED_ATTR);
    };
  }, [enabled, options]);
}
