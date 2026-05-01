import { useEffect } from "react";

import { getLiquidGL, type LiquidGLOptions } from "./index";

const INITIALIZED_ATTR = "data-liquid-gl-initialized";
const TARGET_ATTR = "data-liquid-gl-target-id";
const INIT_RETRY_DELAY_MS = 120;
const INIT_STALL_TIMEOUT_MS = 1800;
const MAX_INIT_ATTEMPTS = 20;

type LiquidGLInstance = { destroy?: () => void } | HTMLElement;

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

    let animationFrameId: number | null = null;
    let retryTimerId: number | null = null;
    let stallTimerId: number | null = null;
    let disposed = false;
    let attempts = 0;
    let initializedElement: HTMLElement | null = null;
    let attributedTargetElement: HTMLElement | null = null;
    let liquidGLInstances: LiquidGLInstance[] = [];

    const clearScheduledWork = () => {
      if (animationFrameId !== null) {
        window.cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
      }
      if (retryTimerId !== null) {
        window.clearTimeout(retryTimerId);
        retryTimerId = null;
      }
      if (stallTimerId !== null) {
        window.clearTimeout(stallTimerId);
        stallTimerId = null;
      }
    };

    const destroyInstances = () => {
      liquidGLInstances.forEach((instance) => {
        if (instance instanceof HTMLElement) {
          return;
        }
        instance.destroy?.();
      });
      liquidGLInstances = [];
      initializedElement?.removeAttribute(INITIALIZED_ATTR);
      initializedElement = null;
      attributedTargetElement?.removeAttribute(TARGET_ATTR);
      attributedTargetElement = null;
    };

    const keepFallbackVisible = (element: HTMLElement) => {
      if (options.reveal !== false) {
        return;
      }
      element.style.opacity = "1";
      element.style.visibility = "visible";
      element.style.pointerEvents = "auto";
    };

    const resolveTargetElement = (): HTMLElement | null => {
      if (typeof options.target === "string") {
        return document.querySelector<HTMLElement>(options.target);
      }
      return options.target instanceof HTMLElement ? options.target : null;
    };

    const resolveVendorTarget = (element: HTMLElement): string => {
      if (typeof options.target === "string") {
        return options.target;
      }

      let targetId = element.getAttribute(TARGET_ATTR);
      if (!targetId) {
        targetId = `liquid-gl-target-${Math.random().toString(36).slice(2, 10)}`;
        element.setAttribute(TARGET_ATTR, targetId);
      }
      attributedTargetElement = element;
      return `[${TARGET_ATTR}="${targetId}"]`;
    };

    const markInitialized = (element: HTMLElement) => {
      initializedElement = element;
      keepFallbackVisible(element);
      element.setAttribute(INITIALIZED_ATTR, "true");
      if (stallTimerId !== null) {
        window.clearTimeout(stallTimerId);
        stallTimerId = null;
      }
    };

    const scheduleAttempt = (delayMs = 0) => {
      if (disposed || attempts >= MAX_INIT_ATTEMPTS) {
        return;
      }
      retryTimerId = window.setTimeout(() => {
        retryTimerId = null;
        animationFrameId = window.requestAnimationFrame(() => {
          animationFrameId = null;
          attemptInitialization();
        });
      }, delayMs);
    };

    const attemptInitialization = () => {
      if (disposed) {
        return;
      }

      const element = resolveTargetElement();
      if (!element) {
        attempts += 1;
        scheduleAttempt(INIT_RETRY_DELAY_MS);
        return;
      }
      if (element.hasAttribute(INITIALIZED_ATTR)) {
        return;
      }

      const liquidGL = getLiquidGL();
      if (!liquidGL) {
        attempts += 1;
        scheduleAttempt(INIT_RETRY_DELAY_MS);
        return;
      }

      let didInitialize = false;
      const vendorTarget = resolveVendorTarget(element);

      try {
        const result = liquidGL({
          ...options,
          target: vendorTarget,
          on: {
            ...options.on,
            init: (lens) => {
              if (disposed) {
                return;
              }
              didInitialize = true;
              markInitialized(element);
              options.on?.init?.(lens);
            },
          },
        });
        liquidGLInstances = Array.isArray(result) ? result : result ? [result] : [];
      } catch (error) {
        console.warn("liquidGL initialization failed", error);
        destroyInstances();
        keepFallbackVisible(element);
        attempts += 1;
        scheduleAttempt(INIT_RETRY_DELAY_MS);
        return;
      }

      if (didInitialize) {
        return;
      }

      if (liquidGLInstances.length === 0) {
        attempts += 1;
        scheduleAttempt(INIT_RETRY_DELAY_MS);
        return;
      }

      const isFallbackResult = liquidGLInstances.every(
        (instance) => instance instanceof HTMLElement,
      );
      if (isFallbackResult || options.reveal === false) {
        markInitialized(element);
        return;
      }

      stallTimerId = window.setTimeout(() => {
        if (disposed || didInitialize) {
          return;
        }
        destroyInstances();
        attempts += 1;
        scheduleAttempt(INIT_RETRY_DELAY_MS);
      }, INIT_STALL_TIMEOUT_MS);
    };

    scheduleAttempt();

    return () => {
      disposed = true;
      clearScheduledWork();
      destroyInstances();
    };
  }, [enabled, options]);
}
