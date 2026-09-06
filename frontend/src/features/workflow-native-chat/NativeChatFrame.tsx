// Mounts the native Omnigent application through the server-generated,
// same-origin scoped chat URL (MoonLadderStudios/MoonMind#3639).
//
// MoonMind supplies only the frame: it never recreates the transcript, composer,
// queue, approvals, tools, or session lifecycle. The frame fills the primary
// pane, exposes an accessible name, shows a neutral loading placeholder (never
// the legacy composer), and reports native-application load/liveness failures up
// so the route can offer an explicit, actionable fallback.
import { useEffect, useRef, useState } from 'react';
import { isNativeChatReadySignal, nativeChatFatalReason, NATIVE_CHAT_READY_TIMEOUT_MS } from './nativeChatProtocol';

export type NativeChatFrameSignal = 'ready' | 'disconnected' | 'incompatible';

interface NativeChatFrameProps {
  chatBindingId: string;
  /** Same-origin, MoonMind-scoped embedded chat URL from the binding. */
  src: string;
  /** Accessible name for the iframe (screen-reader and focus target). */
  title: string;
  readOnly?: boolean;
  /** Time to wait for authorized conversation rendering. */
  loadTimeoutMs?: number;
  onSignal?: (signal: NativeChatFrameSignal) => void;
}

export function NativeChatFrame({
  chatBindingId,
  src,
  title,
  readOnly = false,
  loadTimeoutMs = NATIVE_CHAT_READY_TIMEOUT_MS,
  onSignal,
}: NativeChatFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const loadedRef = useRef(false);
  const [loaded, setLoaded] = useState(false);

  // A readiness timeout is treated as a native UI compatibility failure so
  // the route surfaces the full-page escape hatch instead of hanging on the
  // placeholder forever. Only an authorized conversation render satisfies it.
  useEffect(() => {
    loadedRef.current = false;
    setLoaded(false);
    const timer = window.setTimeout(() => {
      if (loadedRef.current) {
        return;
      }
      onSignal?.('incompatible');
    }, loadTimeoutMs);
    return () => window.clearTimeout(timer);
    // Reset whenever the mounted session changes.
  }, [src, chatBindingId, loadTimeoutMs, onSignal]);

  // Liveness and compatibility signals from the same-origin native application.
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) {
        return;
      }
      const frameWindow = iframeRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) {
        return;
      }
      if (isNativeChatReadySignal(event.data, chatBindingId)) {
        loadedRef.current = true;
        setLoaded(true);
        onSignal?.('ready');
      } else if (nativeChatFatalReason(event.data, chatBindingId) !== null) {
        onSignal?.('incompatible');
      }
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [chatBindingId, src, onSignal]);

  return (
    <div className="wf-native-chat__frame" data-loaded={loaded ? 'true' : 'false'}>
      {loaded ? null : (
        <div className="wf-native-chat__loading" role="status" aria-live="polite">
          <span className="wf-native-chat__spinner" aria-hidden="true" />
          <span>Loading conversation…</span>
        </div>
      )}
      <iframe
        ref={iframeRef}
        className="wf-native-chat__iframe"
        src={src}
        title={title}
        data-readonly={readOnly ? 'true' : 'false'}
        referrerPolicy="same-origin"
        allow="clipboard-read; clipboard-write"
        onError={() => onSignal?.('incompatible')}
      />
    </div>
  );
}
