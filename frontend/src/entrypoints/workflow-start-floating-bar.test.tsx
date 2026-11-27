import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
import {
  CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS,
  CREATE_SUBMIT_BAR_NARROW_QUERY,
} from "../lib/createFloatingSubmitBar";
import { renderWithClient } from "../utils/test-utils";
import { WorkflowStartPage, submitBarSummaryText } from "./workflow-start";

// MoonMind#5471: the Create submit bar must not cover the field being edited
// on a phone. Real layout, focusability and keyboard behaviour run in
// frontend/src/browser/createFloatingSubmitBar.browser.test.tsx.

const payload: BootPayload = {
  page: "workflow-start",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: {
        temporal: { create: "/api/executions", artifactCreate: "/api/artifacts" },
        github: { branches: "/api/github/branches?repository={repository}" },
      },
      system: {
        defaultRepository: "MoonLadderStudios/MoonMind",
        defaultAgentRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "pr",
        defaultTaskModelByRuntime: { codex_cli: "gpt-5.4" },
        defaultTaskEffortByRuntime: { codex_cli: "medium" },
        supportedAgentRuntimes: ["codex_cli"],
        providerProfiles: { list: "/api/v1/provider-profiles" },
      },
    },
  },
};

function jsonResponse(value: unknown): Response {
  return { ok: true, json: async () => value } as Response;
}

function submissionBar(): HTMLElement {
  return screen.getByRole("group", { name: "Workflow submission controls" });
}

function summaryToggle(): HTMLButtonElement {
  return within(submissionBar()).getByRole("button", {
    name: /^Repository and publish mode:/,
  }) as HTMLButtonElement;
}

function stubViewportWidth(narrow: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: narrow && query === CREATE_SUBMIT_BAR_NARROW_QUERY,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

function stubVisualViewport(height: number, scale = 1) {
  // A software keyboard only opens on a touch device.
  Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 5 });
  const viewport = Object.assign(new EventTarget(), { height, scale });
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    writable: true,
    value: viewport,
  });
  return viewport;
}

async function settleFocus() {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, CREATE_SUBMIT_BAR_FOCUS_SETTLE_MS + 50));
  });
}

describe("Create floating submit bar", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, "Workflow Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    fetchSpy = vi.spyOn(window, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/github/branches")) {
        return jsonResponse({
          items: [{ value: "main", label: "main", source: "github" }],
          defaultBranch: "main",
          error: null,
        });
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return jsonResponse([
          {
            profile_id: "profile:codex-default",
            runtime_id: "codex_cli",
            account_label: "Codex Default",
            is_default: true,
          },
        ]);
      }
      if (url.startsWith("/api/workflows/skills")) {
        return jsonResponse({ items: {} });
      }
      return jsonResponse({ items: [] });
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    Reflect.deleteProperty(window, "matchMedia");
    Reflect.deleteProperty(window, "visualViewport");
    Reflect.deleteProperty(navigator, "maxTouchPoints");
  });

  it("summarises the repository name and publish mode", () => {
    expect(submitBarSummaryText("MoonLadderStudios/MoonMind", "pr")).toBe("MoonMind · PR");
    expect(submitBarSummaryText("https://github.com/octo/tools.git", "branch")).toBe(
      "tools · Branch",
    );
    expect(submitBarSummaryText(" ", "none")).toBe("No repository · None");
    expect(submitBarSummaryText("octo/app", "auto")).toBe("app · Auto");
    expect(submitBarSummaryText("octo/app", "pr_with_merge_automation")).toBe(
      "app · PR with Merge Automation",
    );
  });

  describe("on a phone-width viewport", () => {
    beforeEach(() => stubViewportWidth(true));

    it("hides while a form field is edited, without flickering between fields", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      const instructions = await screen.findByLabelText("Instructions");
      const harness = screen.getByLabelText("Harness");
      const bar = submissionBar();
      expect(bar.dataset.placement).toBe("floating");
      expect(bar.hasAttribute("inert")).toBe(false);

      const placements: string[] = [];
      const observer = new MutationObserver(() => placements.push(bar.dataset.placement ?? ""));
      observer.observe(bar, { attributes: true, attributeFilter: ["data-placement"] });

      act(() => instructions.focus());
      expect(bar.dataset.placement).toBe("hidden");
      expect(bar.hasAttribute("inert")).toBe(true);

      act(() => harness.focus());
      await settleFocus();
      expect(bar.dataset.placement).toBe("hidden");

      act(() => harness.blur());
      expect(bar.dataset.placement).toBe("hidden");
      await settleFocus();
      expect(bar.dataset.placement).toBe("floating");
      expect(bar.hasAttribute("inert")).toBe(false);
      observer.disconnect();
      expect(placements).toEqual(["hidden", "floating"]);
    });

    it("stays visible while its own controls have focus, even with the keyboard open", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      fireEvent.click(summaryToggle());
      const repository = within(bar).getByLabelText("Repository");
      const viewport = stubVisualViewport(300);

      act(() => repository.focus());
      act(() => {
        viewport.dispatchEvent(new Event("resize"));
      });
      await settleFocus();
      expect(bar.dataset.placement).toBe("floating");
      for (const label of ["Branch", "Publish Mode"]) {
        act(() => within(bar).getByLabelText(label).focus());
        await settleFocus();
        expect(bar.dataset.placement).toBe("floating");
      }
    });

    it("collapses to one summary line that expands to the full controls", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      const repository = within(bar).getByLabelText("Repository") as HTMLInputElement;
      const publish = within(bar).getByLabelText("Publish Mode") as HTMLSelectElement;
      const toggle = summaryToggle();

      expect(bar.dataset.compact).toBe("collapsed");
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(toggle.textContent).toContain(submitBarSummaryText(repository.value, publish.value));
      expect(within(bar).getByRole("button", { name: "Start Workflow" })).toBeTruthy();

      fireEvent.click(toggle);
      expect(bar.dataset.compact).toBe("expanded");
      expect(toggle.getAttribute("aria-expanded")).toBe("true");

      // A choice collapses the bar and returns focus to the summary.
      act(() => publish.focus());
      fireEvent.change(publish, { target: { value: "branch" } });
      expect(bar.dataset.compact).toBe("collapsed");
      expect(document.activeElement).toBe(toggle);
      expect(toggle.textContent).toContain(`${submitBarSummaryText(repository.value, "branch")}`);

      // So does a tap outside the bar.
      fireEvent.click(toggle);
      expect(bar.dataset.compact).toBe("expanded");
      fireEvent.pointerDown(screen.getByLabelText("Instructions"));
      expect(bar.dataset.compact).toBe("collapsed");
    });

    it("keeps submit outcomes and the publish-mode error in the collapsed bar", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      const instructions = await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      const repository = within(bar).getByLabelText("Repository") as HTMLInputElement;
      const publish = within(bar).getByLabelText("Publish Mode") as HTMLSelectElement;

      fireEvent.click(summaryToggle());
      fireEvent.change(publish, { target: { value: "pr" } });
      fireEvent.click(summaryToggle());
      fireEvent.change(repository, { target: { value: "" } });
      fireEvent.click(summaryToggle());
      expect(bar.dataset.compact).toBe("collapsed");
      const notice = "PR publishing needs a repository. Add one, or choose None to keep results save-only.";
      expect(within(bar).getAllByText(notice)).toHaveLength(1);

      fireEvent.input(instructions, { target: { value: "Write a report." } });
      fireEvent.click(within(bar).getByRole("button", { name: "Start Workflow" }));

      // The rejection names Repository, so the bar opens to focus it.
      await waitFor(() => expect(document.activeElement).toBe(repository));
      expect(bar.dataset.compact).toBe("expanded");
      expect(within(bar).getByRole("alert").textContent).toBe(notice);
      expect(bar.dataset.placement).toBe("floating");
    });

    it("keeps a new submit error visible while its field has focus until the operator types", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      const instructions = await screen.findByLabelText("Instructions");
      const bar = submissionBar();

      fireEvent.click(within(bar).getByRole("button", { name: "Start Workflow" }));
      await waitFor(() => expect(document.activeElement).toBe(instructions));
      await settleFocus();
      expect(within(bar).getByRole("alert").textContent).toBe(
        "Primary step must include instructions or an explicit skill.",
      );
      expect(bar.dataset.compact).toBe("collapsed");
      expect(bar.dataset.placement).toBe("floating");

      fireEvent.input(instructions, { target: { value: "Fix the bug." } });
      expect(bar.dataset.placement).toBe("hidden");
    });

    it("docks in the page flow once the end of the form reaches the bar", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      const instructions = await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      const slot = bar.previousElementSibling as HTMLElement;
      expect(slot.classList.contains("queue-floating-bar-slot")).toBe(true);
      // jsdom has no layout: give the bar a height and move the slot.
      Object.defineProperty(bar, "offsetHeight", { configurable: true, value: 60 });
      let slotTop = 2000;
      const rectAt = (top: number) => ({ top, bottom: top + 60, left: 0, right: 390, width: 390, height: 60, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
      slot.getBoundingClientRect = () => rectAt(slotTop);
      bar.getBoundingClientRect = () => rectAt(slotTop);
      const scroll = async () => {
        await act(async () => {
          window.dispatchEvent(new Event("scroll"));
          await new Promise((resolve) => window.requestAnimationFrame(() => resolve(null)));
        });
      };

      await scroll();
      expect(bar.dataset.placement).toBe("floating");
      expect(slot.style.blockSize).toBe("60px");
      expect(slot.hidden).toBe(false);

      slotTop = window.innerHeight - 60;
      await scroll();
      expect(bar.dataset.placement).toBe("docked");
      expect(slot.hidden).toBe(true);

      // A docked bar covers nothing, so editing does not hide it.
      act(() => instructions.focus());
      expect(bar.dataset.placement).toBe("docked");
      act(() => instructions.blur());
      await settleFocus();

      slotTop = window.innerHeight;
      await scroll();
      expect(bar.dataset.placement).toBe("floating");
      expect(slot.hidden).toBe(false);
    });
  });

  describe("on a desktop-width viewport", () => {
    beforeEach(() => stubViewportWidth(false));

    it("keeps the full bar in place while fields are edited", async () => {
      renderWithClient(<WorkflowStartPage payload={payload} />);
      const instructions = await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      expect(bar.dataset.compact).toBeUndefined();
      expect(
        within(bar).queryByRole("button", { name: /^Repository and publish mode:/ }),
      ).toBeNull();

      act(() => instructions.focus());
      await settleFocus();
      expect(bar.dataset.placement).toBe("floating");
    });

    it("hides on a touch device when its keyboard shrinks the visual viewport, but not for pinch zoom", async () => {
      const viewport = stubVisualViewport(window.innerHeight);
      renderWithClient(<WorkflowStartPage payload={payload} />);
      await screen.findByLabelText("Instructions");
      const bar = submissionBar();
      const resize = async (height: number, scale = 1) => {
        Object.assign(viewport, { height, scale });
        await act(async () => {
          viewport.dispatchEvent(new Event("resize"));
          await new Promise((resolve) => window.requestAnimationFrame(() => resolve(null)));
        });
      };

      await resize(window.innerHeight / 2, 2);
      expect(bar.dataset.placement).toBe("floating");
      await resize(window.innerHeight * 0.5);
      expect(bar.dataset.placement).toBe("hidden");
      expect(bar.hasAttribute("inert")).toBe(true);
      await resize(window.innerHeight);
      expect(bar.dataset.placement).toBe("floating");

      // Without touch input a short visual viewport is not a keyboard.
      Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
      await resize(window.innerHeight * 0.5);
      expect(bar.dataset.placement).toBe("floating");
    });
  });
});
