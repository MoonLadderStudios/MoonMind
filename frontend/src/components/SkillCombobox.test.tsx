import { useState } from "react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { fireEvent, render, screen, within } from "../utils/test-utils";
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
    fireEvent.click(toggle);

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
});
