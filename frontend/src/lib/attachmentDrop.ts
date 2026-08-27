/**
 * Helpers for dropping files onto a workflow step.
 *
 * Drag-and-drop is a second entry point into the same attachment pipeline the
 * file picker feeds, so it filters with exactly the rule the submit-time
 * validator uses (an exact, case-insensitive content-type match against the
 * attachment policy). Anything the policy would reject later is rejected at the
 * drop instead, with the same wording.
 */

export interface DroppedAttachmentPartition {
  accepted: File[];
  rejected: File[];
}

/**
 * True when a drag carries files. Text and element drags (for example dragging
 * a selection inside the instructions textarea) must keep their native
 * behaviour, so callers use this to decide whether to intercept the event.
 */
export function dragEventHasFiles(
  dataTransfer: DataTransfer | null | undefined,
): boolean {
  const types = dataTransfer?.types;
  if (!types) {
    return false;
  }
  return Array.from(types as ArrayLike<string>).includes("Files");
}

/** Extracts the dropped files, tolerating browsers that only populate `items`. */
export function droppedFiles(dataTransfer: DataTransfer | null | undefined): File[] {
  if (!dataTransfer) {
    return [];
  }
  const files = Array.from(dataTransfer.files || []);
  if (files.length > 0) {
    return files;
  }
  const items = Array.from(dataTransfer.items || []);
  return items
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
}

export function partitionDroppedAttachments(
  files: readonly File[],
  allowedContentTypes: readonly string[],
): DroppedAttachmentPartition {
  const allowed = allowedContentTypes.map((type) => type.trim().toLowerCase());
  const accepted: File[] = [];
  const rejected: File[] = [];
  files.forEach((file) => {
    const type = String(file.type || "")
      .trim()
      .toLowerCase();
    if (allowed.includes(type)) {
      accepted.push(file);
    } else {
      rejected.push(file);
    }
  });
  return { accepted, rejected };
}

export function unsupportedAttachmentMessage(rejected: readonly File[]): string {
  return rejected
    .map((file) => `Unsupported file type for ${file.name || "attachment"}.`)
    .join(" ");
}
