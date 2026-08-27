import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, ReactElement } from "react";
import { createPortal } from "react-dom";

import { trapTabWithin } from "../lib/focusTrap";

export interface AttachmentImagePreviewProps {
  /** Display name of the attachment, used for labels and the lightbox caption. */
  filename: string;
  /** Declared content type. Only `image/*` attachments render a preview. */
  contentType?: string | null | undefined;
  /** Pending (not yet uploaded) file. Previewed through an object URL. */
  file?: File | null | undefined;
  /** Download URL for an already-persisted artifact. */
  href?: string | null | undefined;
  /** Secondary caption line, e.g. `Step 1 · 24.1 KB`. */
  detail?: string | null | undefined;
  /** Filename applied to the lightbox download link. */
  download?: string | null | undefined;
  /**
   * Called when the preview source cannot be rendered. The component suppresses
   * itself so the surrounding chip keeps its generic icon, metadata and
   * download action; callers may use this to add an explicit unavailable note.
   */
  onPreviewError?: (() => void) | undefined;
}

const SAFE_PREVIEW_SCHEMES = new Set(["blob:", "http:", "https:"]);

/**
 * True when `url` may be rendered as an image source. Preview sources are
 * assembled from configuration and from browser-local object URLs, so an
 * unexpected scheme (`javascript:`, a non-image `data:` payload) must never
 * reach an `img` element or the lightbox download link.
 */
export function isSafeAttachmentPreviewUrl(
  url: string | null | undefined,
): boolean {
  // Browsers ignore ASCII whitespace and control characters when they resolve a
  // URL, so the scheme is read from the same stripped form they would see.
  const candidate = Array.from(String(url || ""))
    .filter((character) => character.charCodeAt(0) > 0x20)
    .join("");
  if (!candidate) {
    return false;
  }
  const scheme = /^[a-z][a-z0-9+.-]*:/i.exec(candidate)?.[0]?.toLowerCase();
  if (!scheme) {
    // Relative and root-relative sources resolve against the dashboard origin;
    // a protocol-relative source does not.
    return !candidate.startsWith("//");
  }
  if (scheme === "data:") {
    return /^data:image\//i.test(candidate);
  }
  return SAFE_PREVIEW_SCHEMES.has(scheme);
}

export function isImageAttachment(
  contentType: string | null | undefined,
  filename?: string | null,
): boolean {
  const type = String(contentType || "")
    .trim()
    .toLowerCase();
  if (type) {
    return type.startsWith("image/");
  }
  // Pending files dragged from some sources arrive without a content type;
  // fall back to the extension rather than dropping the thumbnail.
  return /\.(png|jpe?g|gif|webp|bmp|avif|svg)$/i.test(String(filename || ""));
}

/**
 * Resolves a displayable URL for an attachment: an object URL for a pending
 * `File`, or the artifact download URL for a persisted ref. Object URLs are
 * revoked when the file changes or the component unmounts.
 */
export function useAttachmentPreviewUrl(
  file: File | null | undefined,
  href: string | null | undefined,
): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      setObjectUrl(null);
      return undefined;
    }
    const created = URL.createObjectURL(file);
    setObjectUrl(created);
    return () => {
      URL.revokeObjectURL?.(created);
    };
  }, [file]);

  if (file) {
    return objectUrl;
  }
  return href || null;
}

interface AttachmentLightboxProps {
  src: string;
  filename: string;
  detail?: string | null | undefined;
  href?: string | null | undefined;
  download?: string | null | undefined;
  onClose: () => void;
  onError: () => void;
}

function AttachmentLightbox({
  src,
  filename,
  detail,
  href,
  download,
  onClose,
  onError,
}: AttachmentLightboxProps): ReactElement | null {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const backdropRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => {
      previousFocusRef.current?.focus();
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const handleBackdropClick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  // The lightbox is the active interaction boundary while it is open, so Tab
  // cycles inside it instead of reaching the form behind the backdrop.
  const handleTabKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    trapTabWithin(backdropRef.current, event);
  }, []);

  if (typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      ref={backdropRef}
      className="attachment-lightbox-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${filename}`}
      onClick={handleBackdropClick}
      onKeyDown={handleTabKeyDown}
    >
      <figure className="attachment-lightbox-panel">
        <img
          className="attachment-lightbox-image"
          src={src}
          alt={filename}
          onError={onError}
        />
        <figcaption className="attachment-lightbox-caption">
          <span className="attachment-lightbox-filename">{filename}</span>
          {detail ? (
            <span className="attachment-lightbox-detail">{detail}</span>
          ) : null}
          {href ? (
            <a
              className="attachment-lightbox-download"
              href={href}
              download={download || filename}
            >
              Download
            </a>
          ) : null}
        </figcaption>
        <button
          type="button"
          ref={closeRef}
          className="attachment-lightbox-close"
          aria-label={`Close preview of ${filename}`}
          title="Close preview"
          onClick={onClose}
        >
          <span aria-hidden="true">×</span>
        </button>
      </figure>
    </div>,
    document.body,
  );
}

/**
 * Small clickable thumbnail for an image attachment. Clicking it opens the full
 * image in a lightbox. Returns `null` when the attachment is not an image, when
 * no safe preview source is available, or when the source failed to load, so
 * callers keep their generic icon, metadata and download action.
 */
export function AttachmentImagePreview({
  filename,
  contentType,
  file,
  href,
  detail,
  download,
  onPreviewError,
}: AttachmentImagePreviewProps): ReactElement | null {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [isUnavailable, setIsUnavailable] = useState<boolean>(false);
  const previewUrl = useAttachmentPreviewUrl(file, href);
  const isImage = isImageAttachment(contentType ?? file?.type, filename);

  const handleClose = useCallback(() => setIsOpen(false), []);

  // A source that fails once stays suppressed until a different source arrives,
  // so a broken thumbnail cannot be clicked into a broken lightbox.
  const handleError = useCallback(() => {
    setIsUnavailable(true);
    setIsOpen(false);
    onPreviewError?.();
  }, [onPreviewError]);

  useEffect(() => {
    setIsUnavailable(false);
  }, [previewUrl]);

  useEffect(() => {
    if (!previewUrl && isOpen) {
      setIsOpen(false);
    }
  }, [isOpen, previewUrl]);

  if (!isImage || isUnavailable || !previewUrl) {
    return null;
  }
  if (!isSafeAttachmentPreviewUrl(previewUrl)) {
    return null;
  }
  const downloadHref = isSafeAttachmentPreviewUrl(href) ? href : null;

  return (
    <>
      <button
        type="button"
        className="attachment-thumbnail"
        aria-label={`Preview ${filename}`}
        title={`Preview ${filename}`}
        aria-haspopup="dialog"
        onClick={() => setIsOpen(true)}
      >
        <img
          className="attachment-thumbnail-image"
          src={previewUrl}
          alt=""
          aria-hidden="true"
          onError={handleError}
        />
      </button>
      {isOpen ? (
        <AttachmentLightbox
          src={previewUrl}
          filename={filename}
          detail={detail}
          href={downloadHref}
          download={download}
          onClose={handleClose}
          onError={handleError}
        />
      ) : null}
    </>
  );
}
