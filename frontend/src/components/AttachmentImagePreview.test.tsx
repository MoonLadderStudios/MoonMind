import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen } from "../utils/test-utils";
import {
  AttachmentImagePreview,
  isImageAttachment,
  isSafeAttachmentPreviewUrl,
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

describe("isSafeAttachmentPreviewUrl", () => {
  it("allows the sources the dashboard actually produces", () => {
    expect(isSafeAttachmentPreviewUrl("/api/artifacts/art-1/download")).toBe(true);
    expect(isSafeAttachmentPreviewUrl("artifacts/art-1/download")).toBe(true);
    expect(isSafeAttachmentPreviewUrl("blob:mock/preview")).toBe(true);
    expect(isSafeAttachmentPreviewUrl("https://storage.example/a.png")).toBe(true);
    expect(isSafeAttachmentPreviewUrl("data:image/png;base64,AAAA")).toBe(true);
  });

  it("rejects empty, protocol-relative and executable sources", () => {
    expect(isSafeAttachmentPreviewUrl("")).toBe(false);
    expect(isSafeAttachmentPreviewUrl(null)).toBe(false);
    expect(isSafeAttachmentPreviewUrl("//evil.example/a.png")).toBe(false);
    expect(isSafeAttachmentPreviewUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeAttachmentPreviewUrl(" JavaScript:alert(1)")).toBe(false);
    // Browsers strip the tab before resolving, so the check must too.
    expect(isSafeAttachmentPreviewUrl("java\tscript:alert(1)")).toBe(false);
    expect(isSafeAttachmentPreviewUrl("data:text/html,<script>")).toBe(false);
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

  it("renders no thumbnail for an unsafe preview source", () => {
    const { container } = render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="javascript:alert(1)"
      />,
    );

    expect(container.querySelector(".attachment-thumbnail")).toBeNull();
  });

  it("suppresses the preview and reports the failure when the source cannot load", () => {
    const onPreviewError = vi.fn();
    const { container } = render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-broken/download"
        onPreviewError={onPreviewError}
      />,
    );

    const thumbnail = screen.getByRole("button", { name: "Preview screenshot.png" });
    fireEvent.error(thumbnail.querySelector("img") as HTMLImageElement);

    expect(onPreviewError).toHaveBeenCalledTimes(1);
    // The chip falls back to its generic icon and keeps its metadata actions.
    expect(container.querySelector(".attachment-thumbnail")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes an open lightbox when its image fails to load", () => {
    render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-broken/download"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview screenshot.png" }));
    fireEvent.error(screen.getByAltText("screenshot.png"));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Preview screenshot.png" }),
    ).toBeNull();
  });

  it("keeps Tab inside the open lightbox", () => {
    render(
      <AttachmentImagePreview
        filename="screenshot.png"
        contentType="image/png"
        href="/api/artifacts/art-3/download"
        download="screenshot.png"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview screenshot.png" }));
    const dialog = screen.getByRole("dialog", { name: "Preview of screenshot.png" });
    const downloadLink = screen.getByRole("link", { name: "Download" });
    const closeButton = screen.getByRole("button", {
      name: "Close preview of screenshot.png",
    });

    // Focus starts on the close button, which is the last focusable control.
    expect(document.activeElement).toBe(closeButton);
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(downloadLink);

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(closeButton);
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
