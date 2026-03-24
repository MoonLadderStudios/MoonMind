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
    const raw = JSON.parse(el.textContent || "{}");
    return BootPayloadSchema.parse(raw);
  } catch (e) {
    console.error("Failed to parse boot payload:", e);
    throw new Error("Invalid boot payload", { cause: e });
  }
}
