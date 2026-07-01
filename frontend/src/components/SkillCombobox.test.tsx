import { useState } from "react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { fireEvent, render, screen, waitFor, within } from "../utils/test-utils";
import { SkillCombobox } from "./SkillCombobox";

const OPTIONS = ["auto", "moonspec-orchestrate", "pr-resolver", "jira-implement"];

function Harness({ initialValue = "" }: { initialValue?: string }): ReactElement {
  const [value, setValue] = useState(initialValue);
  return (
    <SkillCombobox
      value={value}
      options={OPTIONS}
      onChange={setValue}
      placeholder="auto (default), moonspec-orchestrate, ..."
      dataStepIndex="0"
      ariaLabel="Step 1 skill"
    />
  );
}

function HarnessWithLinkedDescription(): ReactElement {
  const [value, setValue] = useState("pr-resolver");
  return (
    <div className="stack queue-step-type-panel">
      <div className="field">
        <SkillCombobox
          value={value}
          options={OPTIONS}
          onChange={setValue}
          placeholder="auto (default), moonspec-orchestrate, ..."
          dataStepIndex="0"
          ariaLabel="Step 1 skill"
        />
      </div>
      <div className="notice small" data-testid="skill-schema-fallback-0">
        <strong>pr-resolver</strong>
        <span>: Resolves pull request review feedback.</span>
        <span> This Skill does not publish structured input fields.</span>
      </div>
    </div>
  );
}

describe("SkillCombobox", () => {
  it("renders the input with skill-step metadata for the workflow form", () => {
    render(<Harness />);

    const input = screen.getByRole("combobox", { name: "Step 1 skill" });
    expect(input.getAttribute("data-step-field")).toBe("skillId");
    expect(input.getAttribute("data-step-index")).toBe("0");
  });

  it("opens the dropdown when the toggle button is tapped and exposes all options", () => {
    render(<Harness />);

    const toggle = screen.getByRole("button", { name: "Show skill options" });
    fireEvent.pointerDown(toggle);

    const listbox = screen.getByRole("listbox");
    const optionTexts = within(listbox)
      .getAllByRole("option")
      .map((node) => node.textContent);
    expect(optionTexts).toEqual(OPTIONS);
  });

  it("filters options as the user types and selects on pointer down", () => {
    render(<Harness />);

    const input = screen.getByRole("combobox", { name: "Step 1 skill" });
    fireEvent.change(input, { target: { value: "moon" } });

    const filtered = within(screen.getByRole("listbox"))
      .getAllByRole("option")
      .map((node) => node.textContent);
    expect(filtered).toEqual(["moonspec-orchestrate"]);

    fireEvent.pointerDown(screen.getByRole("option", { name: "moonspec-orchestrate" }));

    expect((input as HTMLInputElement).value).toBe("moonspec-orchestrate");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens the dropdown when the input is focused on a touch tap", () => {
    render(<Harness />);

    const input = screen.getByRole("combobox", { name: "Step 1 skill" });
    fireEvent.pointerDown(input);
    fireEvent.focus(input);

    expect(screen.getByRole("listbox")).toBeTruthy();
  });

  it("keeps linked skill descriptions hidden until the info button is toggled", async () => {
    render(<HarnessWithLinkedDescription />);

    const description = screen.getByTestId("skill-schema-fallback-0") as HTMLElement;
    await waitFor(() => expect(description.hidden).toBe(true));

    const showToggle = screen.getByRole("button", { name: "Show skill description" });
    expect(showToggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(showToggle);

    expect(description.hidden).toBe(false);
    const hideToggle = screen.getByRole("button", { name: "Hide skill description" });
    expect(hideToggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(hideToggle, { key: "Escape" });

    expect(description.hidden).toBe(true);
  });
});
