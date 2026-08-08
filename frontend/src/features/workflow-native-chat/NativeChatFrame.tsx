// Mounts the native Omnigent application through the server-generated,
// same-origin scoped chat URL (MoonLadderStudios/MoonMind#3639).
//
// MoonMind supplies only the frame: it never recreates the transcript, composer,
// queue, approvals, tools, or session lifecycle. The frame fills the primary
// pane, exposes an accessible name, shows a neutral loading placeholder (never
// the legacy composer), and reports native-application load/liveness failures up
// so the route can offer an explicit, actionable fallback.
import { useEffect, useRef, useState } from 'react';

export type NativeChatFrameSignal = 'ready' | 'disconnected' | 'incompatible';

/** Message shape the embedded native application posts to the host frame. */
const NATIVE_CHAT_MESSAGE_TYPE = 'moonmind:workflow-chat';

interface NativeChatFrameProps {
  /** Same-origin, MoonMind-scoped embedded chat URL from the binding. */
  src: string;
  /** Accessible name for the iframe (screen-reader and focus target). */
  title: string;
  readOnly?: boolean;
  /** Time to wait for the frame to load before treating it as incompatible. */
  loadTimeoutMs?: number;
  onSignal?: (signal: NativeChatFrameSignal) => void;
}

function isNativeChatSignal(value: unknown): value is NativeChatFrameSignal {
  return value === 'ready' || value === 'disconnected' || value === 'incompatible';
}

export function NativeChatFrame({
  src,
  title,
  readOnly = false,
  loadTimeoutMs = 20000,
  onSignal,
}: NativeChatFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const loadedRef = useRef(false);
  const [loaded, setLoaded] = useState(false);

  const markLoaded = () => {
    loadedRef.current = true;
    setLoaded(true);
  };

  // A load timeout is treated as a native UI compatibility/version failure so
  // the route surfaces the full-page escape hatch instead of hanging on the
  // placeholder forever. A frame that has already loaded is left alone.
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
  }, [src, loadTimeoutMs, onSignal]);

  // Liveness and compatibility signals from the same-origin native application.
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) {
        return;
      }
      const frameWindow = iframeRef.current?.contentWindow;
      if (frameWindow && event.source !== frameWindow) {
        return;
      }
      const data = event.data as { type?: unknown; status?: unknown } | null;
      if (!data || data.type !== NATIVE_CHAT_MESSAGE_TYPE) {
        return;
      }
      if (isNativeChatSignal(data.status)) {
        if (data.status === 'ready') {
          loadedRef.current = true;
          setLoaded(true);
        }
        onSignal?.(data.status);
      }
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [onSignal]);

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
        onLoad={() => {
          markLoaded();
          onSignal?.('ready');
        }}
        onError={() => onSignal?.('incompatible')}
      />
    </div>
  );
}
