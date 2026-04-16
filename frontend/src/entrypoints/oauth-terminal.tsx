import { useEffect, useMemo, useRef, useState } from 'react';

import type { BootPayload } from '../boot/parseBootPayload';

type OAuthTerminalInitialData = {
  sessionId?: string;
};

type AttachResponse = {
  session_id: string;
  terminal_session_id: string;
  terminal_bridge_id: string;
  websocket_url: string;
  attach_token: string;
};

function readSessionId(payload: BootPayload): string {
  const initialData = payload.initialData as OAuthTerminalInitialData | undefined;
  if (initialData?.sessionId) {
    return initialData.sessionId;
  }
  return new URLSearchParams(window.location.search).get('session_id') ?? '';
}

function websocketUrlFromAttach(websocketUrl: string): string {
  const base = window.location.origin.replace(/^http/, 'ws');
  if (websocketUrl.startsWith('ws://') || websocketUrl.startsWith('wss://')) {
    return websocketUrl;
  }
  return `${base}${websocketUrl}`;
}

export function OAuthTerminalPage({ payload }: { payload: BootPayload }) {
  const sessionId = useMemo(() => readSessionId(payload), [payload]);
  const [status, setStatus] = useState('Preparing terminal');
  const [terminalLines, setTerminalLines] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setStatus('Missing OAuth session');
      return;
    }

    let closed = false;
    async function attach() {
      try {
        const response = await fetch(
          `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}/terminal/attach`,
          { method: 'POST' },
        );
        if (!response.ok) {
          throw new Error(`Attach failed: ${response.status}`);
        }
        const attachPayload = (await response.json()) as AttachResponse;
        if (closed) {
          return;
        }
        const socket = new WebSocket(websocketUrlFromAttach(attachPayload.websocket_url));
        socketRef.current = socket;
        socket.onopen = () => {
          setStatus('Connected');
          socket.send(JSON.stringify({ type: 'heartbeat' }));
        };
        socket.onmessage = (event) => {
          const data = String(event.data);
          setTerminalLines((lines) => [...lines, data]);
        };
        socket.onclose = () => {
          setStatus('Closed');
        };
        socket.onerror = () => {
          setStatus('Terminal connection failed');
        };
      } catch (error) {
        setStatus(error instanceof Error ? error.message : 'Terminal attach failed');
      }
    }

    void attach();
    return () => {
      closed = true;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [sessionId]);

  const sendInput = () => {
    const socket = socketRef.current;
    if (!input || !socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify({ type: 'input', data: input }));
    setInput('');
  };

  return (
    <main className="oauth-terminal-page">
      <header className="oauth-terminal-header">
        <div>
          <p className="eyebrow">OAuth enrollment</p>
          <h1>Provider Login Terminal</h1>
        </div>
        <span className="oauth-terminal-status">{status}</span>
      </header>
      <section className="oauth-terminal-surface" aria-label="OAuth terminal output">
        {terminalLines.length > 0 ? (
          terminalLines.map((line, index) => <pre key={`${index}-${line}`}>{line}</pre>)
        ) : (
          <p>Waiting for provider login output.</p>
        )}
      </section>
      <form
        className="oauth-terminal-input"
        onSubmit={(event) => {
          event.preventDefault();
          sendInput();
        }}
      >
        <label htmlFor="oauth-terminal-command">Terminal input</label>
        <div>
          <input
            id="oauth-terminal-command"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            autoComplete="off"
          />
          <button type="submit">Send</button>
        </div>
      </form>
    </main>
  );
}

export default OAuthTerminalPage;
