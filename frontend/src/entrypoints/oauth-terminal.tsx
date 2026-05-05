import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { BootPayload } from '../boot/parseBootPayload';
import { formatStatusLabel } from '../utils/formatters';

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

type OAuthSessionStatus =
  | 'pending'
  | 'starting'
  | 'bridge_ready'
  | 'awaiting_user'
  | 'verifying'
  | 'registering_profile'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired';

type OAuthSessionResponse = {
  session_id: string;
  runtime_id?: string | null;
  profile_id?: string | null;
  status: OAuthSessionStatus;
  expires_at?: string | null;
  terminal_session_id?: string | null;
  terminal_bridge_id?: string | null;
  session_transport?: string | null;
  failure_reason?: string | null;
  profile_summary?: ProviderProfileSummary | null;
};

type ProviderProfileSummary = {
  profile_id: string;
  runtime_id: string;
  provider_id: string;
  provider_label?: string | null;
  credential_source: string;
  runtime_materialization_mode: string;
  account_label?: string | null;
  enabled: boolean;
  is_default: boolean;
  rate_limit_policy: string;
};

type TerminalContextMenuState = {
  selectedText: string;
  x: number;
  y: number;
};

const TERMINAL_ATTACHABLE_STATUSES: readonly OAuthSessionStatus[] = [
  'bridge_ready',
  'awaiting_user',
  'verifying',
];
const TERMINAL_FINAL_STATUSES: readonly OAuthSessionStatus[] = [
  'succeeded',
  'failed',
  'cancelled',
  'expired',
];
const TERMINAL_FINALIZE_STATUSES: readonly OAuthSessionStatus[] = [
  'awaiting_user',
  'verifying',
  'registering_profile',
];
const PROVIDER_PROFILE_REFRESH_STORAGE_KEY = 'moonmind:provider-profile-updated';
const TERMINAL_READY_POLL_MS = 1000;

function copyTextWithLegacyCommand(text: string): void {
  if (typeof document === 'undefined' || !document.body) {
    return;
  }
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.setAttribute('readonly', '');
  textArea.style.left = '-9999px';
  textArea.style.position = 'fixed';
  textArea.style.top = '0';
  document.body.appendChild(textArea);
  textArea.select();
  try {
    document.execCommand('copy');
  } catch {
    // Some browsers block programmatic copy; the visible selection remains intact.
  } finally {
    document.body.removeChild(textArea);
  }
}

function copyTextToClipboard(text: string): void {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    copyTextWithLegacyCommand(text);
    return;
  }
  try {
    const maybePromise = navigator.clipboard.writeText(text);
    if (typeof maybePromise?.catch === 'function') {
      maybePromise.catch(() => copyTextWithLegacyCommand(text));
    }
  } catch {
    // Clipboard permissions can vary by browser/context; keep terminal input stable.
    copyTextWithLegacyCommand(text);
  }
}

async function readTextFromClipboard(): Promise<string> {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.readText !== 'function'
  ) {
    return '';
  }
  try {
    return await navigator.clipboard.readText();
  } catch {
    // Browser clipboard permissions vary; native paste events remain the fallback path.
    return '';
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

function oauthStatusLabel(status: OAuthSessionStatus): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function isTerminalAttachable(session: OAuthSessionResponse): boolean {
  return (
    TERMINAL_ATTACHABLE_STATUSES.includes(session.status) &&
    Boolean(session.terminal_session_id) &&
    Boolean(session.terminal_bridge_id)
  );
}

function contextMenuPositionForEvent(
  event: MouseEvent,
  fallbackElement: HTMLElement,
): Pick<TerminalContextMenuState, 'x' | 'y'> {
  if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
    if (event.clientX !== 0 || event.clientY !== 0) {
      return { x: event.clientX, y: event.clientY };
    }
  }

  const rect = fallbackElement.getBoundingClientRect();
  return {
    x: rect.left + Math.min(24, Math.max(rect.width / 2, 0)),
    y: rect.top + Math.min(24, Math.max(rect.height / 2, 0)),
  };
}

async function readErrorDetail(response: Response, fallback: string): Promise<string> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  return safeDisplayText(typeof payload?.detail === 'string' ? payload.detail : fallback);
}

function safeDisplayText(text: string | null | undefined): string {
  return (text ?? '')
    .replace(/(token|password|secret|api[_-]?key)=\S+/gi, '$1=[REDACTED]')
    .replace(/\/home\/[^/\s]+\/\.(codex|claude)\/[^\s]+/gi, '[REDACTED_AUTH_PATH]');
}

function notifyProviderProfileRefresh(session: OAuthSessionResponse): void {
  const profileId = session.profile_summary?.profile_id ?? session.profile_id;
  if (!profileId) {
    return;
  }
  const value = JSON.stringify({
    profileId,
    sessionId: session.session_id,
    updatedAt: Date.now(),
  });
  window.localStorage.setItem(PROVIDER_PROFILE_REFRESH_STORAGE_KEY, value);
  window.dispatchEvent(
    new StorageEvent('storage', {
      key: PROVIDER_PROFILE_REFRESH_STORAGE_KEY,
      newValue: value,
    }),
  );
}

export function OAuthTerminalPage({ payload }: { payload: BootPayload }) {
  const sessionId = useMemo(() => readSessionId(payload), [payload]);
  const [status, setStatus] = useState('Preparing terminal');
  const [session, setSession] = useState<OAuthSessionResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [pastedInput, setPastedInput] = useState('');
  const [contextMenu, setContextMenu] = useState<TerminalContextMenuState | null>(null);
  const terminalElementRef = useRef<HTMLDivElement | null>(null);
  const pastedInputRef = useRef<HTMLTextAreaElement | null>(null);
  const contextMenuItemRef = useRef<HTMLButtonElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const sendTerminalInput = (data: string) => {
    if (!data) {
      return;
    }
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }));
    }
  };

  const copyTerminalSelection = () => {
    const selectedText = terminalRef.current?.getSelection() ?? '';
    if (selectedText) {
      copyTextToClipboard(selectedText);
    }
  };

  const sendPastedInput = () => {
    if (!pastedInput) {
      pastedInputRef.current?.focus();
      return;
    }
    if (socketRef.current?.readyState !== WebSocket.OPEN) {
      pastedInputRef.current?.focus();
      return;
    }
    const input = pastedInput.endsWith('\n') ? pastedInput : `${pastedInput}\n`;
    sendTerminalInput(input);
    setPastedInput('');
  };

  const pasteClipboardToTerminal = async () => {
    const text = await readTextFromClipboard();
    if (text) {
      setPastedInput(text);
    }
    pastedInputRef.current?.focus();
  };

  const refreshSessionFromResponse = (nextSession: OAuthSessionResponse) => {
    setSession(nextSession);
    setStatus(oauthStatusLabel(nextSession.status));
    if (nextSession.status === 'succeeded') {
      notifyProviderProfileRefresh(nextSession);
    }
  };

  const runSessionAction = async (
    action: 'finalize' | 'cancel' | 'reconnect',
    fallback: string,
  ) => {
    if (!sessionId) {
      return;
    }
    setActionError(null);
    setActionPending(action);
    try {
      const response = await fetch(
        `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}/${action}`,
        { method: 'POST' },
      );
      if (!response.ok) {
        throw new Error(await readErrorDetail(response, fallback));
      }
      const payload = (await response.json().catch(() => null)) as OAuthSessionResponse | null;
      if (payload?.session_id) {
        refreshSessionFromResponse(payload);
      } else if (action === 'cancel') {
        setSession((current) =>
          current ? { ...current, status: 'cancelled' } : current,
        );
        setStatus('Cancelled');
      }
    } catch (error) {
      setActionError(
        safeDisplayText(error instanceof Error ? error.message : fallback),
      );
    } finally {
      setActionPending(null);
    }
  };

  useEffect(() => {
    if (!contextMenu) {
      return undefined;
    }
    const closeContextMenu = () => {
      setContextMenu(null);
    };
    const closeContextMenuOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeContextMenu();
      }
    };
    contextMenuItemRef.current?.focus({ preventScroll: true });
    window.addEventListener('click', closeContextMenu);
    window.addEventListener('contextmenu', closeContextMenu, true);
    window.addEventListener('keydown', closeContextMenuOnEscape);
    window.addEventListener('resize', closeContextMenu);
    return () => {
      window.removeEventListener('click', closeContextMenu);
      window.removeEventListener('contextmenu', closeContextMenu, true);
      window.removeEventListener('keydown', closeContextMenuOnEscape);
      window.removeEventListener('resize', closeContextMenu);
    };
  }, [contextMenu]);

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
      if (!selectedText || !event.clipboardData) {
        return;
      }
      event.preventDefault();
      event.clipboardData.setData('text/plain', selectedText);
    };
    terminalElement.addEventListener('copy', copySelectionListener);
    const pasteListener = (event: ClipboardEvent) => {
      const pastedText = event.clipboardData?.getData('text/plain') ?? '';
      if (!pastedText) {
        return;
      }
      event.preventDefault();
      sendTerminalInput(pastedText);
    };
    terminalElement.addEventListener('paste', pasteListener, true);
    const keydownListener = (event: KeyboardEvent) => {
      const usesShortcutModifier = event.metaKey || event.ctrlKey;
      if (!usesShortcutModifier) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === 'c') {
        const selectedText = terminal.getSelection();
        if (!selectedText) {
          return;
        }
        event.preventDefault();
        copyTextToClipboard(selectedText);
        return;
      }
    };
    terminalElement.addEventListener('keydown', keydownListener);
    const clickListener = () => {
      terminal.focus();
    };
    terminalElement.addEventListener('click', clickListener);
    const contextMenuListener = (event: MouseEvent) => {
      const selectedText = terminal.getSelection();
      if (!selectedText) {
        setContextMenu(null);
        return;
      }
      event.preventDefault();
      const position = contextMenuPositionForEvent(event, terminalElement);
      setContextMenu({
        selectedText,
        x: position.x,
        y: position.y,
      });
    };
    terminalElement.addEventListener('contextmenu', contextMenuListener);

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
        terminalElement.removeEventListener('paste', pasteListener, true);
        terminalElement.removeEventListener('keydown', keydownListener);
        terminalElement.removeEventListener('click', clickListener);
        terminalElement.removeEventListener('contextmenu', contextMenuListener);
        terminal.dispose();
        terminalRef.current = null;
        fitAddonRef.current = null;
      };
    }

    let closed = false;
    const inputDisposable = terminal.onData((data) => {
      sendTerminalInput(data);
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
        const sessionEndpoint = `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}`;
        const waitForTerminalReadiness = async (): Promise<boolean> => {
          while (!closed) {
            const sessionResponse = await fetch(sessionEndpoint, {
              headers: { Accept: 'application/json' },
            });
            if (!sessionResponse.ok) {
              const detail = await readErrorDetail(
                sessionResponse,
                `Session lookup failed: ${sessionResponse.status}`,
              );
              throw new Error(detail);
            }
            const session = (await sessionResponse.json()) as OAuthSessionResponse;
            setSession(session);
            if (isTerminalAttachable(session)) {
              return true;
            }
            if (TERMINAL_FINAL_STATUSES.includes(session.status)) {
              setStatus(oauthStatusLabel(session.status));
              return false;
            }
            setStatus(
              TERMINAL_ATTACHABLE_STATUSES.includes(session.status)
                ? 'Preparing terminal bridge'
                : `OAuth ${oauthStatusLabel(session.status)}`,
            );
            await new Promise((resolve) => window.setTimeout(resolve, TERMINAL_READY_POLL_MS));
          }
          return false;
        };

        const isReadyToAttach = await waitForTerminalReadiness();
        if (closed || !isReadyToAttach) {
          return;
        }
        setStatus('Connecting terminal');
        const response = await fetch(`${sessionEndpoint}/terminal/attach`, { method: 'POST' });
        if (!response.ok) {
          const detail = await readErrorDetail(response, `Attach failed: ${response.status}`);
          throw new Error(detail);
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
      terminalElement.removeEventListener('paste', pasteListener, true);
      terminalElement.removeEventListener('keydown', keydownListener);
      terminalElement.removeEventListener('click', clickListener);
      terminalElement.removeEventListener('contextmenu', contextMenuListener);
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
          <button type="button" className="secondary" onClick={pasteClipboardToTerminal}>
            Paste from clipboard
          </button>
          <span className="oauth-terminal-status">{formatStatusLabel(status)}</span>
        </div>
      </header>
      {session ? (
        <section className="oauth-terminal-session-panel" aria-label="OAuth session details">
          <dl>
            <div>
              <dt>Profile</dt>
              <dd>{session.profile_summary?.profile_id ?? session.profile_id ?? 'Unknown profile'}</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>{formatStatusLabel(session.runtime_id ?? 'unknown')}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{oauthStatusLabel(session.status)}</dd>
            </div>
            {session.profile_summary ? (
              <div>
                <dt>Account</dt>
                <dd>
                  {session.profile_summary.account_label ??
                    session.profile_summary.provider_label ??
                    session.profile_summary.provider_id}
                </dd>
              </div>
            ) : null}
            {session.failure_reason ? (
              <div>
                <dt>Failure</dt>
                <dd>{safeDisplayText(session.failure_reason)}</dd>
              </div>
            ) : null}
          </dl>
          <div className="oauth-terminal-session-actions">
            {TERMINAL_FINALIZE_STATUSES.includes(session.status) ? (
              <button
                type="button"
                className="primary"
                onClick={() => runSessionAction('finalize', 'Failed to finalize OAuth session.')}
                disabled={actionPending !== null}
              >
                Finalize Provider Profile
              </button>
            ) : null}
            {['pending', 'starting', 'bridge_ready', 'awaiting_user', 'verifying'].includes(
              session.status,
            ) ? (
              <button
                type="button"
                className="secondary"
                onClick={() => runSessionAction('cancel', 'Failed to cancel OAuth session.')}
                disabled={actionPending !== null}
              >
                Cancel
              </button>
            ) : null}
            {['failed', 'cancelled', 'expired'].includes(session.status) ? (
              <button
                type="button"
                className="secondary"
                onClick={() => runSessionAction('reconnect', 'Failed to reconnect OAuth session.')}
                disabled={actionPending !== null}
              >
                Reconnect
              </button>
            ) : null}
          </div>
          {actionError ? <p role="alert">{actionError}</p> : null}
        </section>
      ) : null}
      <section className="oauth-terminal-paste-box">
        <label className="oauth-terminal-paste-label" htmlFor="oauth-terminal-paste-input">
          Paste authentication code
        </label>
        <div className="oauth-terminal-paste-controls">
          <textarea
            id="oauth-terminal-paste-input"
            ref={pastedInputRef}
            className="oauth-terminal-paste-input"
            rows={3}
            placeholder="Paste the returned authentication code here, then send it to the terminal."
            value={pastedInput}
            onChange={(event) => setPastedInput(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                event.preventDefault();
                sendPastedInput();
              }
            }}
          />
          <button type="button" className="primary" onClick={sendPastedInput}>
            Send to terminal
          </button>
        </div>
      </section>
      <section className="oauth-terminal-surface" aria-label="OAuth terminal output">
        <div ref={terminalElementRef} className="oauth-terminal-xterm" />
      </section>
      {contextMenu ? (
        <div
          className="oauth-terminal-context-menu"
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <button
            ref={contextMenuItemRef}
            type="button"
            role="menuitem"
            onClick={() => {
              copyTextToClipboard(contextMenu.selectedText);
              setContextMenu(null);
            }}
          >
            Copy selection
          </button>
        </div>
      ) : null}
    </main>
  );
}

export default OAuthTerminalPage;
