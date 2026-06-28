import { useState, type ReactNode } from 'react';
import { copyTextToClipboard } from '../../utils/clipboard';

/**
 * Shared monospace text / log viewer (MM-959).
 *
 * Replaces per-page stdout/stderr/diagnostics viewers that used inline color
 * styles and duplicated copy/wrap/download behavior. Supports static or live
 * text, loading/error/empty states, an artifact-fallback note, and a
 * collapsible disclosure that reports expansion so callers can lazily fetch
 * content. All presentation comes from `.log-panel*` classes — no inline color.
 */
export interface LogPanelProps {
  title: ReactNode;
  text?: string | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  /** Body text shown when isError is true. */
  errorMessage?: string;
  /** Body text shown when there is no content and not loading/erroring. */
  emptyMessage?: string;
  loadingMessage?: string;
  /** Direct download URL for the full artifact. */
  downloadUrl?: string | undefined;
  downloadFileName?: string | undefined;
  /** Optional note rendered when content is served from an artifact fallback. */
  artifactFallbackNote?: ReactNode;
  /** Render as a collapsible <details> disclosure (default true). */
  collapsible?: boolean;
  defaultExpanded?: boolean;
  /** Reports expansion changes so callers can lazily enable fetches. */
  onExpandedChange?: (expanded: boolean) => void;
  defaultWrap?: boolean;
  ariaLabel?: string | undefined;
  className?: string;
}

function LogPanelControls({
  wrap,
  setWrap,
  onCopy,
  copied,
  canCopy,
  downloadUrl,
  downloadFileName,
}: {
  wrap: boolean;
  setWrap: (next: boolean) => void;
  onCopy: () => void;
  copied: boolean;
  canCopy: boolean;
  downloadUrl?: string | undefined;
  downloadFileName?: string | undefined;
}) {
  return (
    <div className="log-panel__controls">
      <label className="log-panel__wrap-toggle">
        <input
          type="checkbox"
          checked={wrap}
          onChange={(event) => setWrap(event.target.checked)}
        />
        <span className="small">Wrap lines</span>
      </label>
      <button
        type="button"
        className="secondary small"
        onClick={onCopy}
        disabled={!canCopy}
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
      {downloadUrl ? (
        <a
          className="button secondary small"
          href={downloadUrl}
          download={downloadFileName}
          target="_blank"
          rel="noreferrer"
        >
          Download
        </a>
      ) : null}
    </div>
  );
}

function LogPanelOutput({
  text,
  isLoading,
  isError,
  errorMessage,
  emptyMessage,
  loadingMessage,
  wrap,
  ariaLabel,
}: {
  text?: string | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  errorMessage: string;
  emptyMessage: string;
  loadingMessage: string;
  wrap: boolean;
  ariaLabel?: string | undefined;
}) {
  let body: string;
  let stateClass = 'log-panel__output';
  if (isLoading) {
    body = loadingMessage;
    stateClass += ' log-panel__output--loading';
  } else if (isError) {
    body = errorMessage;
    stateClass += ' log-panel__output--error';
  } else if (!text) {
    body = emptyMessage;
    stateClass += ' log-panel__output--empty';
  } else {
    body = text;
  }

  return (
    <div className="log-panel__viewport">
      <pre
        className={stateClass}
        data-wrap={wrap ? 'on' : 'off'}
        aria-label={ariaLabel}
      >
        {body}
      </pre>
    </div>
  );
}

export function LogPanel({
  title,
  text,
  isLoading = false,
  isError = false,
  errorMessage = 'Error loading content.',
  emptyMessage = '(no output)',
  loadingMessage = 'Loading...',
  downloadUrl,
  downloadFileName,
  artifactFallbackNote,
  collapsible = true,
  defaultExpanded = false,
  onExpandedChange,
  defaultWrap = true,
  ariaLabel,
  className,
}: LogPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [wrap, setWrap] = useState(defaultWrap);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
    }
  };

  const handleExpandedChange = (next: boolean) => {
    setExpanded(next);
    onExpandedChange?.(next);
  };

  const classes = ['log-panel'];
  if (className) {
    classes.push(className);
  }

  const controls = (
    <LogPanelControls
      wrap={wrap}
      setWrap={setWrap}
      onCopy={handleCopy}
      copied={copied}
      canCopy={Boolean(text)}
      downloadUrl={downloadUrl}
      downloadFileName={downloadFileName}
    />
  );

  const output = (
    <LogPanelOutput
      text={text}
      isLoading={isLoading}
      isError={isError}
      errorMessage={errorMessage}
      emptyMessage={emptyMessage}
      loadingMessage={loadingMessage}
      wrap={wrap}
      ariaLabel={ariaLabel}
    />
  );

  const fallback = artifactFallbackNote ? (
    <p className="log-panel__fallback small">{artifactFallbackNote}</p>
  ) : null;

  if (!collapsible) {
    return (
      <section className={classes.join(' ')}>
        <header className="log-panel__header">
          <h3 className="log-panel__title">{title}</h3>
          {controls}
        </header>
        {fallback}
        {output}
      </section>
    );
  }

  return (
    <details className={classes.join(' ')} open={expanded}>
      <summary
        className="log-panel__summary"
        onClick={(event) => {
          // Drive expansion explicitly so behavior is deterministic across
          // environments (jsdom does not toggle <details> on summary click)
          // and so callers can lazily enable fetches via onExpandedChange.
          event.preventDefault();
          handleExpandedChange(!expanded);
        }}
      >
        {title}
      </summary>
      {expanded ? (
        <div className="log-panel__content">
          {controls}
          {fallback}
          {output}
        </div>
      ) : null}
    </details>
  );
}

export default LogPanel;
