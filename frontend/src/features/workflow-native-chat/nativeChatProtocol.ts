/** Bounded startup deadline for the native iframe to announce readiness. */
export const NATIVE_CHAT_READY_TIMEOUT_MS = 15_000;

export const NATIVE_CHAT_READY_TYPE = 'moonmind.omnigent.chat.ready';
export const NATIVE_CHAT_FATAL_TYPE = 'moonmind.omnigent.chat.fatal';

/**
 * Validate a `postMessage` payload as a readiness signal for `bindingId`.
 *
 * The receiver additionally checks exact origin, iframe window, and mount
 * generation at the call site; this helper owns only the bounded payload shape
 * so it is unit-testable in isolation (MoonLadderStudios/MoonMind#4013
 * AC7/PLAN5). Payloads carry presentation state only — never provider
 * identity, transcript content, or credentials.
 */
export function isNativeChatReadySignal(data: unknown, bindingId: string): boolean {
  if (!data || typeof data !== 'object') return false;
  const record = data as Record<string, unknown>;
  return (
    record['type'] === NATIVE_CHAT_READY_TYPE &&
    record['chatBindingId'] === bindingId
  );
}

/** Return the bounded fatal reason when `data` is a fatal signal, else null. */
export function nativeChatFatalReason(data: unknown, bindingId: string): string | null {
  if (!data || typeof data !== 'object') return null;
  const record = data as Record<string, unknown>;
  if (
    record['type'] !== NATIVE_CHAT_FATAL_TYPE ||
    record['chatBindingId'] !== bindingId
  ) {
    return null;
  }
  const reason = typeof record['reason'] === 'string' ? record['reason'] : 'native_crash';
  return reason.slice(0, 128) || 'native_crash';
}
