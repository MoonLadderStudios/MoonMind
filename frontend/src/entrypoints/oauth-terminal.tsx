import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
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

function copyTextToClipboard(text: string): void {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    return;
  }
  try {
    const maybePromise = navigator.clipboard.writeText(text);
    if (typeof maybePromise?.catch === 'function') {
      maybePromise.catch(() => undefined);
    }
  } catch {
    // Clipboard permissions can vary by browser/context; keep terminal input stable.
  }
}

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
  const terminalElementRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const copyTerminalSelection = () => {
    const selectedText = terminalRef.current?.getSelection() ?? '';
    if (selectedText) {
      copyTextToClipboard(selectedText);
    }
  };

  useEffect(() => {
    const terminalElement = terminalElementRef.current;
    if (!terminalElement) {
      return undefined;
    }

    const terminal = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 14,
      theme: {
        background: '#111827',
        foreground: '#e5e7eb',
      },
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalElement);
    fitAddon.fit();
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const copySelectionListener = (event: ClipboardEvent) => {
      const selectedText = terminal.getSelection();
      if (!selectedText) {
        return;
      }
      event.preventDefault();
      event.clipboardData?.setData('text/plain', selectedText);
    };
    terminalElement.addEventListener('copy', copySelectionListener);

    const sendResize = () => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }),
        );
      }
    };
    const resizeListener = () => {
      fitAddon.fit();
      sendResize();
    };
    window.addEventListener('resize', resizeListener);

    if (!sessionId) {
      setStatus('Missing OAuth session');
      terminal.writeln('Missing OAuth session');
      return () => {
        window.removeEventListener('resize', resizeListener);
        terminalElement.removeEventListener('copy', copySelectionListener);
        terminal.dispose();
        terminalRef.current = null;
        fitAddonRef.current = null;
      };
    }

    let closed = false;
    const inputDisposable = terminal.onData((data) => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'input', data }));
      }
    });

    const writeSocketMessage = (message: MessageEvent) => {
      const rawData = String(message.data);
      try {
        const frame = JSON.parse(rawData) as unknown;
        if (typeof frame === 'object' && frame !== null && 'type' in frame) {
          const typedFrame = frame as { type?: unknown; data?: unknown; detail?: unknown };
          if (typedFrame.type === 'output' && typeof typedFrame.data === 'string') {
            terminal.write(typedFrame.data);
            return;
          }
          if (typedFrame.type === 'error' && typeof typedFrame.detail === 'string') {
            terminal.writeln(`\r\n${typedFrame.detail}`);
            return;
          }
          return;
        }
      } catch {
        // Raw PTY streams are text, not JSON protocol frames.
      }
      terminal.write(rawData);
    };

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
          sendResize();
        };
        socket.onmessage = writeSocketMessage;
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
      window.removeEventListener('resize', resizeListener);
      terminalElement.removeEventListener('copy', copySelectionListener);
      inputDisposable.dispose();
      socketRef.current?.close();
      socketRef.current = null;
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [sessionId]);

  return (
    <main className="oauth-terminal-page">
      <header className="oauth-terminal-header">
        <div>
          <p className="eyebrow">OAuth enrollment</p>
          <h1>Provider Login Terminal</h1>
        </div>
        <div className="oauth-terminal-actions">
          <button type="button" className="secondary" onClick={copyTerminalSelection}>
            Copy selection
          </button>
          <span className="oauth-terminal-status">{status}</span>
        </div>
      </header>
      <section className="oauth-terminal-surface" aria-label="OAuth terminal output">
        <div ref={terminalElementRef} className="oauth-terminal-xterm" />
      </section>
    </main>
  );
}

export default OAuthTerminalPage;
