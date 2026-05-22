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
  KeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactElement,
} from "react";

export interface SkillComboboxProps {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  placeholder?: string;
  inputId?: string;
  dataStepIndex?: string;
  ariaLabel?: string;
  inputClassName?: string;
}

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
  dataStepIndex,
  ariaLabel,
  inputClassName,
}: SkillComboboxProps): ReactElement {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [highlightedIndex, setHighlightedIndex] = useState<number>(-1);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const generatedListId = useId();
  const listboxId = `${generatedListId}-listbox`;

  const filteredOptions = useMemo(
    () => filterOptions(options, value),
    [options, value],
  );

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

  const handleToggle = useCallback(
    (event: ReactMouseEvent<HTMLButtonElement>) => {
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

  return (
    <div
      ref={containerRef}
      className="skill-combobox"
      style={{ position: "relative" }}
    >
      <div className="skill-combobox-input-row">
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={isOpen}
          aria-controls={listboxId}
          aria-label={ariaLabel}
          value={value}
          placeholder={placeholder}
          data-step-field="skillId"
          {...(dataStepIndex !== undefined ? { "data-step-index": dataStepIndex } : {})}
          className={inputClassName}
          onChange={handleInputChange}
          onPointerDown={handleInputPointerDown}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="skill-combobox-toggle"
          aria-label={isOpen ? "Hide skill options" : "Show skill options"}
          aria-expanded={isOpen}
          aria-controls={listboxId}
          tabIndex={-1}
          onClick={handleToggle}
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
