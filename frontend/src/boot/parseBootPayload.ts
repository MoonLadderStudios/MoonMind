import { z } from "zod";

export const BootPayloadSchema = z.object({
  page: z.string(),
  apiBase: z.string().default("/api"),
  features: z.record(z.string(), z.boolean()).optional(),
  initialData: z.unknown().optional(),
});

export type BootPayload = z.infer<typeof BootPayloadSchema>;

export function parseBootPayload(elementId = "moonmind-ui-boot"): BootPayload {
  const el = document.getElementById(elementId);
  if (!el) {
    throw new Error(`Boot element #${elementId} not found`);
  }

  try {
    const rawText = el.textContent;
    if (!rawText || rawText.trim() === "") {
      throw new Error(`Boot element #${elementId} has no content.`);
    }
    const raw = JSON.parse(rawText);
    return BootPayloadSchema.parse(raw);
  } catch (e) {
    console.error("Failed to parse boot payload:", e);
    throw new Error("Invalid boot payload", { cause: e });
  }
}
