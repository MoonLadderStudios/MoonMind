import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  ChangeEvent,
  CSSProperties,
  FocusEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactElement,
} from "react";

export interface SkillComboboxProps {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  placeholder?: string;
  inputId?: string;
  dataStepField?: string;
  dataStepIndex?: string;
  ariaLabel?: string;
  inputClassName?: string;
}

const LINKED_SKILL_DESCRIPTION_SELECTOR = '[data-testid^="skill-schema-fallback-"]';

function filterOptions(options: readonly string[], query: string): string[] {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) {
    return [...options];
  }
  const startsWith: string[] = [];
  const includes: string[] = [];
  for (const option of options) {
    const lower = option.toLowerCase();
    if (lower.startsWith(trimmed)) {
      startsWith.push(option);
    } else if (lower.includes(trimmed)) {
      includes.push(option);
    }
  }
  return [...startsWith, ...includes];
}

export function SkillCombobox({
  value,
  options,
  onChange,
  placeholder,
  inputId,
  dataStepField,
  dataStepIndex,
  ariaLabel,
  inputClassName,
}: SkillComboboxProps): ReactElement {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [highlightedIndex, setHighlightedIndex] = useState<number>(-1);
  const [isDescriptionOpen, setIsDescriptionOpen] = useState<boolean>(false);
  const [hasLinkedDescription, setHasLinkedDescription] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const linkedDescriptionRef = useRef<HTMLElement | null>(null);
  const isDescriptionOpenRef = useRef<boolean>(isDescriptionOpen);
  const generatedListId = useId();
  const listboxId = `${generatedListId}-listbox`;
  const linkedDescriptionId = `${generatedListId}-description`;

  const filteredOptions = useMemo(
    () => filterOptions(options, value),
    [options, value],
  );

  useEffect(() => {
    setIsDescriptionOpen(false);
  }, [value]);

  const restoreLinkedDescription = useCallback(
    (linkedDescription: HTMLElement | null): void => {
      if (!linkedDescription) {
        return;
      }
      linkedDescription.hidden = false;
      linkedDescription.removeAttribute("aria-live");
      if (linkedDescription.id === linkedDescriptionId) {
        linkedDescription.removeAttribute("id");
      }
    },
    [linkedDescriptionId],
  );

  const syncLinkedDescription = useCallback((): void => {
    const container = containerRef.current;
    if (!container) {
      setHasLinkedDescription(false);
      restoreLinkedDescription(linkedDescriptionRef.current);
      linkedDescriptionRef.current = null;
      return;
    }
    const field = container.closest(".field");
    const candidate = field?.nextElementSibling;
    const linkedDescription =
      candidate instanceof HTMLElement &&
      candidate.matches(LINKED_SKILL_DESCRIPTION_SELECTOR)
        ? candidate
        : null;

    if (!linkedDescription) {
      setHasLinkedDescription(false);
      restoreLinkedDescription(linkedDescriptionRef.current);
      linkedDescriptionRef.current = null;
      return;
    }

    if (
      linkedDescriptionRef.current &&
      linkedDescriptionRef.current !== linkedDescription
    ) {
      restoreLinkedDescription(linkedDescriptionRef.current);
    }

    linkedDescriptionRef.current = linkedDescription;
    linkedDescription.id = linkedDescriptionId;
    linkedDescription.hidden = !isDescriptionOpenRef.current;
    linkedDescription.setAttribute("aria-live", "polite");
    setHasLinkedDescription(true);
  }, [linkedDescriptionId, restoreLinkedDescription]);

  useEffect(() => {
    syncLinkedDescription();
    const field = containerRef.current?.closest(".field");
    const panel = field?.parentElement;
    if (!panel) {
      return undefined;
    }

    const observer = new MutationObserver(() => {
      syncLinkedDescription();
    });
    observer.observe(panel, { childList: true });

    return () => {
      observer.disconnect();
    };
  }, [syncLinkedDescription]);

  useEffect(() => {
    isDescriptionOpenRef.current = isDescriptionOpen;
    if (linkedDescriptionRef.current) {
      linkedDescriptionRef.current.hidden = !isDescriptionOpen;
    }
  }, [isDescriptionOpen]);

  useEffect(() => {
    return () => {
      restoreLinkedDescription(linkedDescriptionRef.current);
      linkedDescriptionRef.current = null;
    };
  }, [restoreLinkedDescription]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const handlePointerDown = (event: PointerEvent | MouseEvent): void => {
      const container = containerRef.current;
      if (!container) {
        return;
      }
      const target = event.target as Node | null;
      if (target && !container.contains(target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      setHighlightedIndex(-1);
    }
  }, [isOpen]);

  useEffect(() => {
    if (highlightedIndex >= filteredOptions.length) {
      setHighlightedIndex(filteredOptions.length - 1);
    }
  }, [filteredOptions.length, highlightedIndex]);

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      onChange(event.target.value);
      setIsOpen(true);
    },
    [onChange],
  );

  const handleInputPointerDown = useCallback(() => {
    setIsOpen(true);
  }, []);

  const handleBlur = useCallback((event: FocusEvent<HTMLInputElement>) => {
    const container = containerRef.current;
    const next = event.relatedTarget as Node | null;
    if (container && next && container.contains(next)) {
      return;
    }
    setIsOpen(false);
  }, []);

  const handleToggle = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      event.preventDefault();
      setIsOpen((prev) => {
        const next = !prev;
        if (next) {
          inputRef.current?.focus();
        }
        return next;
      });
    },
    [],
  );

  const handleDescriptionToggle = useCallback(() => {
    setIsDescriptionOpen((prev) => !prev);
  }, []);

  const handleOptionPick = useCallback(
    (option: string) => {
      onChange(option);
      setIsOpen(false);
      inputRef.current?.focus();
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        }
        setHighlightedIndex((prev) => {
          if (filteredOptions.length === 0) {
            return -1;
          }
          const next = prev + 1;
          return next >= filteredOptions.length ? 0 : next;
        });
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        }
        setHighlightedIndex((prev) => {
          if (filteredOptions.length === 0) {
            return -1;
          }
          const next = prev <= 0 ? filteredOptions.length - 1 : prev - 1;
          return next;
        });
      } else if (event.key === "Enter") {
        if (
          isOpen &&
          highlightedIndex >= 0 &&
          highlightedIndex < filteredOptions.length
        ) {
          const chosen = filteredOptions[highlightedIndex];
          if (chosen !== undefined) {
            event.preventDefault();
            handleOptionPick(chosen);
          }
        }
      } else if (event.key === "Escape") {
        if (isOpen) {
          event.preventDefault();
          setIsOpen(false);
        }
      }
    },
    [filteredOptions, handleOptionPick, highlightedIndex, isOpen],
  );

  const listStyle: CSSProperties = {
    position: "absolute",
    insetInlineStart: 0,
    insetInlineEnd: 0,
    top: "calc(100% + 0.25rem)",
    zIndex: 30,
    maxHeight: "16rem",
    overflowY: "auto",
  };

  const activeDescendantId =
    isOpen && highlightedIndex >= 0 && highlightedIndex < filteredOptions.length
      ? `${listboxId}-option-${highlightedIndex}`
      : undefined;

  return (
    <div
      ref={containerRef}
      className="skill-combobox"
      style={{ position: "relative" }}
    >
      <div
        className={
          hasLinkedDescription
            ? "skill-combobox-input-row skill-combobox-input-row--with-description"
            : "skill-combobox-input-row"
        }
      >
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={isOpen}
          aria-controls={isOpen ? listboxId : undefined}
          aria-activedescendant={activeDescendantId}
          aria-describedby={
            isDescriptionOpen && hasLinkedDescription ? linkedDescriptionId : undefined
          }
          aria-label={ariaLabel}
          value={value}
          placeholder={placeholder}
          data-step-field={dataStepField ?? "skillId"}
          {...(dataStepIndex !== undefined ? { "data-step-index": dataStepIndex } : {})}
          className={inputClassName}
          onChange={handleInputChange}
          onPointerDown={handleInputPointerDown}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
        />
        <button
          type="button"
          className="skill-combobox-toggle"
          aria-label={isOpen ? "Hide skill options" : "Show skill options"}
          aria-expanded={isOpen}
          aria-controls={isOpen ? listboxId : undefined}
          tabIndex={-1}
          onPointerDown={handleToggle}
        >
          <svg
            aria-hidden="true"
            focusable="false"
            viewBox="0 0 12 8"
            width="12"
            height="8"
          >
            <path
              d="M1 1.5 L6 6.5 L11 1.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        {hasLinkedDescription ? (
          <button
            type="button"
            className="queue-info-toggle skill-combobox-description-toggle"
            aria-label={
              isDescriptionOpen ? "Hide skill description" : "Show skill description"
            }
            aria-expanded={isDescriptionOpen}
            aria-controls={linkedDescriptionId}
            title={
              isDescriptionOpen ? "Hide skill description" : "Show skill description"
            }
            onClick={handleDescriptionToggle}
            onKeyDown={(event) => {
              if (event.key === "Escape" && isDescriptionOpen) {
                event.preventDefault();
                setIsDescriptionOpen(false);
              }
            }}
          >
            <svg aria-hidden="true" focusable="false" viewBox="0 0 12 12">
              <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" />
              <path
                d="M6 5.5v3"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
              />
              <path
                d="M6 3.5h.01"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
              />
            </svg>
          </button>
        ) : null}
      </div>
      {isOpen && filteredOptions.length > 0 ? (
        <ul
          id={listboxId}
          role="listbox"
          className="skill-combobox-listbox"
          style={listStyle}
        >
          {filteredOptions.map((option, index) => {
            const isHighlighted = index === highlightedIndex;
            const isSelected = option === value;
            return (
              <li
                id={`${listboxId}-option-${index}`}
                key={option}
                role="option"
                aria-selected={isSelected}
                data-highlighted={isHighlighted ? "true" : undefined}
                className="skill-combobox-option"
                onPointerDown={(event) => {
                  event.preventDefault();
                  handleOptionPick(option);
                }}
                onMouseEnter={() => setHighlightedIndex(index)}
              >
                {option}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
