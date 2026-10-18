import { useState, type ReactNode } from "react";

// A collapsible Create option group. Its fields stay mounted while closed, so
// collapsing never changes what is submitted, and the summary states the values
// it holds. The browser owns the open state after mount, which lets the submit
// check open a closed group to focus an invalid field.
export function CreateOptionDisclosure({
  id,
  label,
  summary,
  defaultOpen = false,
  children,
}: {
  id: string;
  label: string;
  summary: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [initiallyOpen] = useState(defaultOpen);
  return (
    <details
      className="queue-create-option"
      data-create-disclosure={id}
      open={initiallyOpen}
    >
      <summary className="queue-create-option-summary">
        <strong>{label}</strong>{" "}
        <span className="small queue-create-option-summary-text" data-create-disclosure-summary>
          {summary}
        </span>
      </summary>
      <div className="stack queue-create-option-body">{children}</div>
    </details>
  );
}
