import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getLiquidGL } from "./index";
import { useLiquidGL } from "./useLiquidGL";

vi.mock("./index", () => ({
  getLiquidGL: vi.fn(),
}));

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
  });

  it("destroys initialized liquidGL instances when the component unmounts", async () => {
    const destroy = vi.fn();
    const liquidGL = Object.assign(
      vi.fn(() => ({ el: document.createElement("div"), destroy })),
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
      vi.fn(() => [
        { el: document.createElement("div"), destroy: firstDestroy },
        { el: document.createElement("div"), destroy: secondDestroy },
      ]),
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
      expect(getLiquidGL).toHaveBeenCalledTimes(1);
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
      expect(liquidGL).toHaveBeenCalledTimes(1);
    });

    const element = document.querySelector(".liquid-glass-panel");
    expect(element?.hasAttribute("data-liquid-gl-initialized")).toBe(false);
    expect(consoleWarn).toHaveBeenCalledWith(
      "liquidGL initialization failed",
      expect.any(Error),
    );

    consoleWarn.mockRestore();
  });
});
