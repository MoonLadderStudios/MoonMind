import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen } from "../utils/test-utils";
import {
  AttachmentImagePreview,
  isImageAttachment,
} from "./AttachmentImagePreview";

const OBJECT_URL = "blob:mock/preview";

describe("isImageAttachment", () => {
  it("uses the declared content type first", () => {
    expect(isImageAttachment("image/png", "notes.txt")).toBe(true);
    expect(isImageAttachment("application/pdf", "shot.png")).toBe(false);
  });

  it("falls back to the filename when the drop carried no content type", () => {
    expect(isImageAttachment("", "screenshot.PNG")).toBe(true);
    expect(isImageAttachment(null, "diagram.webp")).toBe(true);
    expect(isImageAttachment(undefined, "notes.txt")).toBe(false);
  });
});

describe("AttachmentImagePreview", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn(() => OBJECT_URL),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders no thumbnail for a non-image attachment", () => {
    const { container } = render(
      <AttachmentImagePreview
        filename="notes.pdf"
        contentType="application/pdf"
        href="/api/artifacts/art-1/download"
      />,
    );

    expect(container.querySelector(".attachment-thumbnail")).toBeNull();
  });

  it("previews a pending screenshot through an object URL and revokes it on unmount", () => {
    const file = new File(["png-bytes"], "screenshot.png", { type: "image/png" });
    const { unmount } = render(
      <AttachmentImagePreview filename="screenshot.png" file={file} />,
    );

    const thumbnail = screen.getByRole("button", { name: "Preview screenshot.png" });
    const image = thumbnail.querySelector("img");
    expect(image?.getAttribute("src")).toBe(OBJECT_URL);
    expect(URL.createObjectURL).toHaveBeenCalledWith(file);

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(OBJECT_URL);
  });

  it("previews a persisted attachment from its artifact download URL", () => {
    render(
      <AttachmentImagePreview
        filename="objective.png"
        contentType="image/png"
        href="/api/artifacts/art-9/download"
      />,
    );

    const image = screen
      .getByRole("button", { name: "Preview objective.png" })
      .querySelector("img");
    expect(image?.getAttribute("src")).toBe("/api/artifacts/art-9/download");
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("opens the full image in a lightbox and closes it from the button, backdrop and Escape", () => {
    render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-3/download"
        download="screenshot.png"
        detail="Step 1 · 24.1 KB"
      />,
    );

    const thumbnail = screen.getByRole("button", { name: "Preview screenshot.png" });
    fireEvent.click(thumbnail);

    const dialog = screen.getByRole("dialog", { name: "Preview of screenshot.png" });
    const fullImage = screen.getByAltText("screenshot.png") as HTMLImageElement;
    expect(fullImage.getAttribute("src")).toBe("/api/artifacts/art-3/download");
    expect(fullImage.className).toBe("attachment-lightbox-image");
    expect(screen.getByText("Step 1 · 24.1 KB")).toBeTruthy();
    const download = screen.getByRole("link", { name: "Download" });
    expect(download.getAttribute("href")).toBe("/api/artifacts/art-3/download");
    expect(download.getAttribute("download")).toBe("screenshot.png");

    fireEvent.click(
      screen.getByRole("button", { name: "Close preview of screenshot.png" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(thumbnail);
    fireEvent.click(screen.getByRole("dialog"));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(thumbnail);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(dialog.isConnected).toBe(false);
  });

  it("keeps a click inside the lightbox panel from closing it", () => {
    render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-3/download"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview screenshot.png" }));
    fireEvent.click(screen.getByAltText("screenshot.png"));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("returns focus to the thumbnail after the preview closes", () => {
    render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-3/download"
      />,
    );

    const thumbnail = screen.getByRole("button", { name: "Preview screenshot.png" });
    thumbnail.focus();
    fireEvent.click(thumbnail);
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Close preview of screenshot.png" }),
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.activeElement).toBe(thumbnail);
  });
});
