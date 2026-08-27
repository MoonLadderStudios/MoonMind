import { describe, expect, it } from "vitest";

import {
  dragEventHasFiles,
  droppedFiles,
  partitionDroppedAttachments,
  unsupportedAttachmentMessage,
} from "./attachmentDrop";

function dataTransfer(init: {
  types?: string[];
  files?: File[];
  items?: Array<{ kind: string; getAsFile: () => File | null }>;
}): DataTransfer {
  return {
    types: init.types ?? [],
    files: init.files ?? [],
    items: init.items ?? [],
  } as unknown as DataTransfer;
}

describe("dragEventHasFiles", () => {
  it("detects file drags", () => {
    expect(dragEventHasFiles(dataTransfer({ types: ["Files"] }))).toBe(true);
  });

  it("ignores text drags so the instructions textarea keeps native handling", () => {
    expect(
      dragEventHasFiles(dataTransfer({ types: ["text/plain", "text/html"] })),
    ).toBe(false);
    expect(dragEventHasFiles(null)).toBe(false);
    expect(dragEventHasFiles(undefined)).toBe(false);
  });
});

describe("droppedFiles", () => {
  it("reads the files list", () => {
    const file = new File(["a"], "shot.png", { type: "image/png" });
    expect(droppedFiles(dataTransfer({ files: [file] }))).toEqual([file]);
  });

  it("falls back to dataTransfer items when files is empty", () => {
    const file = new File(["a"], "shot.png", { type: "image/png" });
    const transfer = dataTransfer({
      items: [
        { kind: "string", getAsFile: () => null },
        { kind: "file", getAsFile: () => file },
      ],
    });
    expect(droppedFiles(transfer)).toEqual([file]);
  });

  it("returns nothing without a data transfer", () => {
    expect(droppedFiles(null)).toEqual([]);
  });
});

describe("partitionDroppedAttachments", () => {
  it("accepts only policy content types, matching the submit-time validator", () => {
    const png = new File(["a"], "shot.png", { type: "image/png" });
    const gif = new File(["b"], "loop.gif", { type: "image/gif" });
    const typeless = new File(["c"], "mystery.png", { type: "" });

    const { accepted, rejected } = partitionDroppedAttachments(
      [png, gif, typeless],
      ["image/png", "application/pdf"],
    );

    expect(accepted).toEqual([png]);
    expect(rejected).toEqual([gif, typeless]);
  });

  it("matches content types case-insensitively", () => {
    const png = new File(["a"], "shot.PNG", { type: "IMAGE/PNG" });
    const { accepted } = partitionDroppedAttachments([png], ["Image/PNG"]);
    expect(accepted).toEqual([png]);
  });
});

describe("unsupportedAttachmentMessage", () => {
  it("names every rejected file", () => {
    expect(
      unsupportedAttachmentMessage([
        new File(["a"], "loop.gif", { type: "image/gif" }),
        new File(["b"], "notes.txt", { type: "text/plain" }),
      ]),
    ).toBe(
      "Unsupported file type for loop.gif. Unsupported file type for notes.txt.",
    );
  });
});
