import { afterEach, beforeEach, describe, expect, it } from "vitest";

import "../styles/dashboard.css";

let host: HTMLDivElement;
let rootClasses: string;

beforeEach(() => {
  rootClasses = document.documentElement.className;
  document.documentElement.classList.remove("dark");
  host = document.createElement("div");
  document.body.appendChild(host);
});

afterEach(() => {
  host.remove();
  document.documentElement.className = rootClasses;
});

describe("Tailwind dashboard compatibility", () => {
  it("resolves dashboard color tokens and the class-based dark variant", () => {
    host.innerHTML = '<div class="text-mm-ink bg-mm-panel rounded-mm">Token</div><div class="text-mm-ink dark:text-white">Variant</div>';
    const token = host.children[0]!;
    const variant = host.children[1]!;
    expect(getComputedStyle(token).color).toBe("rgb(18, 20, 32)");
    expect(getComputedStyle(token).backgroundColor).toBe("rgb(255, 255, 255)");
    expect(getComputedStyle(token).borderRadius).toBe("14.4px");
    expect(getComputedStyle(variant).color).toBe("rgb(18, 20, 32)");
    document.documentElement.classList.add("dark");
    expect(getComputedStyle(token).color).toBe("rgb(237, 236, 255)");
    expect(getComputedStyle(token).backgroundColor).toBe("rgb(20, 18, 34)");
    expect(getComputedStyle(variant).color).toBe("rgb(255, 255, 255)");
  });

  it("preserves the existing small shadow and backdrop blur", () => {
    host.className = "shadow-sm backdrop-blur-sm";
    expect(getComputedStyle(host).boxShadow).toContain("rgba(0, 0, 0, 0.05) 0px 1px 2px 0px");
    expect(getComputedStyle(host).backdropFilter).toBe("blur(4px)");
  });

  it("keeps user-agent heading styles when Preflight is disabled", () => {
    host.innerHTML = "<h1>Unstyled heading</h1>";
    expect(parseFloat(getComputedStyle(host.children[0]!).fontSize)).toBeGreaterThan(16);
  });

  it("generates layout utilities from frontend sources", () => {
    host.className = "grid grid-cols-2 gap-4";
    host.style.width = "320px";
    host.innerHTML = "<div>First</div><div>Second</div>";
    const left = host.children[0]!.getBoundingClientRect();
    const right = host.children[1]!.getBoundingClientRect();
    expect(getComputedStyle(host).display).toBe("grid");
    expect(Math.round(left.width)).toBe(152);
    expect(Math.round(right.left - left.right)).toBe(16);
  });
});
