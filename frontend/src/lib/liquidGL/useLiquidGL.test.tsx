import { act, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getLiquidGL } from "./index";
import { useLiquidGL } from "./useLiquidGL";

vi.mock("./index", () => ({
  getLiquidGL: vi.fn(),
}));

type LiquidGLMock = NonNullable<ReturnType<typeof getLiquidGL>>;
type LiquidGLMockOptions = Parameters<LiquidGLMock>[0];
type LiquidGLMockResult = Exclude<ReturnType<LiquidGLMock>, undefined>;
type LiquidGLMockLens = Extract<LiquidGLMockResult, { el: HTMLElement }>;

function LiquidGLHarness() {
  useLiquidGL({
    options: {
      target: ".liquid-glass-panel",
    },
  });

  return <div className="liquid-glass-panel" />;
}

describe("useLiquidGL", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("marks the element initialized only after liquidGL finishes initializing", async () => {
    const destroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn((options: LiquidGLMockOptions) => {
        const lens: LiquidGLMockLens = { el: document.createElement("div"), destroy };
        options.on?.init?.(lens);
        return lens;
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    const { unmount } = render(<LiquidGLHarness />);

    await waitFor(() => {
      expect(liquidGL).toHaveBeenCalledWith({
        target: ".liquid-glass-panel",
        on: expect.objectContaining({ init: expect.any(Function) }),
      });
    });

    const element = document.querySelector(".liquid-glass-panel");
    expect(element?.getAttribute("data-liquid-gl-initialized")).toBe("true");

    unmount();

    expect(destroy).toHaveBeenCalledTimes(1);
  });

  it("destroys every initialized liquidGL instance from a multi-target result", async () => {
    const firstDestroy = vi.fn();
    const secondDestroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn((options: LiquidGLMockOptions) => {
        const lenses: LiquidGLMockLens[] = [
          { el: document.createElement("div"), destroy: firstDestroy },
          { el: document.createElement("div"), destroy: secondDestroy },
        ];
        options.on?.init?.(lenses[0]!);
        return lenses;
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    const { unmount } = render(<LiquidGLHarness />);

    await waitFor(() => {
      expect(liquidGL).toHaveBeenCalledTimes(1);
    });

    unmount();

    expect(firstDestroy).toHaveBeenCalledTimes(1);
    expect(secondDestroy).toHaveBeenCalledTimes(1);
  });

  it("leaves the CSS fallback shell untouched when liquidGL is unavailable", async () => {
    vi.mocked(getLiquidGL).mockReturnValue(undefined);

    render(<LiquidGLHarness />);

    await waitFor(() => {
      expect(getLiquidGL).toHaveBeenCalled();
    });

    const element = document.querySelector(".liquid-glass-panel");
    expect(element?.hasAttribute("data-liquid-gl-initialized")).toBe(false);
  });

  it("leaves the CSS fallback shell untouched when liquidGL initialization fails", async () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const liquidGL = Object.assign(
      vi.fn(() => {
        throw new Error("liquidGL unavailable");
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    render(<LiquidGLHarness />);

    await waitFor(() => {
      expect(liquidGL).toHaveBeenCalled();
    });

    const element = document.querySelector(".liquid-glass-panel");
    expect(element?.hasAttribute("data-liquid-gl-initialized")).toBe(false);
    expect(consoleWarn).toHaveBeenCalledWith(
      "liquidGL initialization failed",
      expect.any(Error),
    );

    consoleWarn.mockRestore();
  });

  it("retries when the liquidGL target is not yet present", async () => {
    vi.useFakeTimers();
    const destroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn((options: LiquidGLMockOptions) => {
        const lens: LiquidGLMockLens = { el: document.createElement("div"), destroy };
        options.on?.init?.(lens);
        return lens;
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    function DelayedTargetHarness({ show }: { show: boolean }) {
      useLiquidGL({
        options: {
          target: ".liquid-glass-panel",
        },
      });
      return show ? <div className="liquid-glass-panel" /> : null;
    }

    const view = render(<DelayedTargetHarness show={false} />);

    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    expect(liquidGL).not.toHaveBeenCalled();

    view.rerender(<DelayedTargetHarness show />);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(liquidGL).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".liquid-glass-panel")?.getAttribute("data-liquid-gl-initialized")).toBe(
      "true",
    );
  });

  it("destroys stalled liquidGL instances and retries until the target is revealed", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    const destroy = vi.fn(() => {
      const element = document.querySelector<HTMLElement>(".liquid-glass-panel");
      if (element) {
        element.style.opacity = "";
      }
    });
    const liquidGL = Object.assign(
      vi.fn((options: LiquidGLMockOptions) => {
        attempts += 1;
        const element = document.querySelector<HTMLElement>(".liquid-glass-panel");
        if (element) {
          element.style.opacity = attempts > 1 ? "1" : "0";
        }
        const lens: LiquidGLMockLens = { el: document.createElement("div"), destroy };
        if (attempts > 1) {
          options.on?.init?.(lens);
        }
        return lens;
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    render(<LiquidGLHarness />);

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    expect(liquidGL).toHaveBeenCalledTimes(2);

    const element = document.querySelector<HTMLElement>(".liquid-glass-panel");
    expect(destroy).toHaveBeenCalledTimes(1);
    expect(element?.getAttribute("data-liquid-gl-initialized")).toBe("true");
    expect(element?.style.opacity).toBe("1");
  });

  it("treats non-revealing renderer returns as initialized without retrying", async () => {
    vi.useFakeTimers();
    const destroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn(() => ({ el: document.createElement("div"), destroy })),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    function NoRevealHarness() {
      useLiquidGL({
        options: {
          target: ".liquid-glass-panel",
          reveal: false,
        },
      });

      return <div className="liquid-glass-panel" />;
    }

    render(<NoRevealHarness />);

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    expect(liquidGL).toHaveBeenCalledTimes(1);
    expect(destroy).not.toHaveBeenCalled();
    expect(document.querySelector(".liquid-glass-panel")?.getAttribute("data-liquid-gl-initialized")).toBe(
      "true",
    );
  });

  it("adapts HTMLElement targets into a selector before calling liquidGL", async () => {
    const target = document.createElement("div");
    target.className = "liquid-glass-panel";
    document.body.appendChild(target);
    const destroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn((options: LiquidGLMockOptions) => {
        const lens: LiquidGLMockLens = { el: document.createElement("div"), destroy };
        options.on?.init?.(lens);
        return lens;
      }),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    function ElementTargetHarness() {
      useLiquidGL({
        options: {
          target,
        },
      });

      return null;
    }

    const { unmount } = render(<ElementTargetHarness />);

    await waitFor(() => {
      expect(liquidGL).toHaveBeenCalledTimes(1);
    });

    expect(liquidGL).toHaveBeenCalledWith({
      target: expect.stringContaining('[data-liquid-gl-target-id="'),
      on: expect.objectContaining({ init: expect.any(Function) }),
    });
    expect(target.getAttribute("data-liquid-gl-initialized")).toBe("true");

    unmount();

    expect(destroy).toHaveBeenCalledTimes(1);
    expect(target.hasAttribute("data-liquid-gl-target-id")).toBe(false);
    target.remove();
  });

  it("treats DOM fallback returns as successful initialization without retrying", async () => {
    vi.useFakeTimers();
    const liquidGL = Object.assign(
      vi.fn(() => document.querySelector(".liquid-glass-panel") as HTMLElement),
      {
        registerDynamic: vi.fn(),
        syncWith: vi.fn(),
      },
    );
    vi.mocked(getLiquidGL).mockReturnValue(liquidGL);

    render(<LiquidGLHarness />);

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    expect(liquidGL).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".liquid-glass-panel")?.getAttribute("data-liquid-gl-initialized")).toBe(
      "true",
    );
  });
});
