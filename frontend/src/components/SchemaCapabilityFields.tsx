import { useEffect, useId, useRef, useState, type ReactElement } from 'react';
import { Button } from './Button';
import { resolveSchemaProperty } from '../lib/assessmentProjects';

export interface SchemaCapabilityDetail {
  inputSchema?: Record<string, unknown>;
  uiSchema?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
}
type RepositoryOption = { value: string; label: string };
type BranchOption = { value: string; label: string };
export const OWNER_REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

export function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function schemaProperties(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return Object.fromEntries(Object.entries(recordValue(schema?.properties)).map(([name, property]) => [name, resolveSchemaProperty(property, schema || {})]));
}

export function schemaRequired(schema: Record<string, unknown> | undefined): Set<string> {
  return new Set(
    (Array.isArray(schema?.required) ? schema.required : [])
      .map((item) => String(item || "").trim())
      .filter(Boolean),
  );
}

export function schemaAlternativeRequiredGroups(
  schema: Record<string, unknown> | undefined,
): string[][] {
  const alternatives = Array.isArray(schema?.anyOf)
    ? schema.anyOf
    : Array.isArray(schema?.oneOf)
      ? schema.oneOf
      : [];
  return alternatives
    .map((alternative) =>
      Array.from(schemaRequired(recordValue(alternative))),
    )
    .filter((group) => group.length > 0);
}

export function schemaGuidedInputNames(
  schema: Record<string, unknown> | undefined,
): Set<string> {
  const names = schemaRequired(schema);
  for (const group of schemaAlternativeRequiredGroups(schema)) {
    for (const name of group) {
      names.add(name);
    }
  }
  return names;
}

export function capabilityFieldLabel(name: string, schema: Record<string, unknown>): string {
  return String(schema.title || name)
    .trim()
    .replaceAll("_", " ");
}

export function capabilityFieldUiSchema(
  uiSchema: Record<string, unknown> | undefined,
  name: string,
): Record<string, unknown> {
  return recordValue(uiSchema?.[name]);
}

export function capabilityFieldVisible(
  uiSchema: Record<string, unknown>,
  values: Record<string, unknown>,
  defaults?: Record<string, unknown>,
): boolean {
  const visibleWhen = recordValue(uiSchema["visibleWhen"]);
  if (Object.keys(visibleWhen).length === 0) {
    return true;
  }
  const field = String(visibleWhen["field"] ?? "").trim();
  if (!field) {
    return true;
  }
  const actual =
    values && values[field] !== undefined
      ? values[field]
      : safeCapabilityDefault(defaults, field);
  if (Object.prototype.hasOwnProperty.call(visibleWhen, "equals")) {
    return actual === visibleWhen["equals"];
  }
  if (Array.isArray(visibleWhen["oneOf"])) {
    return visibleWhen["oneOf"].some((value) => value === actual);
  }
  return true;
}

export function capabilityWidgetName(
  schema: Record<string, unknown>,
  uiSchema: Record<string, unknown>,
): string {
  const widget = String(
    uiSchema.widget || schema["x-moonmind-widget"] || schema.format || schema.type || "text",
  )
    .trim()
    .toLowerCase();
  const aliases: Record<string, string> = {
    checkbox: "boolean",
    editor: "textarea",
    "github.repository-picker": "repository-picker",
    repository: "repository-picker",
    repo: "repository-picker",
    "repo-picker": "repository-picker",
    "github.branch-picker": "branch",
  };
  return aliases[widget] || widget;
}

export function schemaChoiceOptions(schema: Record<string, unknown>): Array<{
  label: string;
  value: unknown;
}> {
  const choices = Array.isArray(schema.oneOf)
    ? schema.oneOf
    : Array.isArray(schema.anyOf)
      ? schema.anyOf
      : [];
  return choices
    .map((choice) => {
      const choiceSchema = recordValue(choice);
      const value = choiceSchema.const ?? (Array.isArray(choiceSchema.enum) ? choiceSchema.enum[0] : undefined);
      if (value === undefined) {
        return null;
      }
      return {
        label: String(choiceSchema.title || value),
        value,
      };
    })
    .filter((choice): choice is { label: string; value: unknown } => Boolean(choice));
}

export function textInputTypeForSchema(schema: Record<string, unknown>): string {
  const format = String(schema.format || "").trim().toLowerCase();
  if (format === "date-time") {
    return "datetime-local";
  }
  if (
    format === "email" ||
    format === "uri" ||
    format === "url" ||
    format === "date"
  ) {
    return format === "uri" ? "url" : format;
  }
  if (schema.type === "number" || schema.type === "integer") {
    return "number";
  }
  return "text";
}

export function complexCapabilityTextValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function parseComplexCapabilityValue(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return text;
  }
}

export function unsupportedCapabilityWidget(
  widget: string,
  schema: Record<string, unknown>,
): boolean {
  if (Array.isArray(schema.enum) || schemaChoiceOptions(schema).length > 0) {
    return false;
  }
  const supported = new Set([
    "array",
    "boolean",
    "branch",
    "date",
    "date-time",
    "email",
    "integer",
    "json",
    "multi-select",
    "github.issue-picker",
    "jira.issue-picker",
    "markdown",
    "number",
    "object",
    "repository",
    "repository-picker",
    "repo",
    "repo-picker",
    "string",
    "text",
    "textarea",
    "uri",
    "url",
  ]);
  return !supported.has(widget);
}

export function containsSecretLikeCapabilityValue(value: unknown): boolean {
  if (value && typeof value === "object") {
    if (Array.isArray(value)) {
      return value.some((item) => containsSecretLikeCapabilityValue(item));
    }
    return Object.entries(value as Record<string, unknown>).some(
      ([key, nested]) =>
        /(authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key)/i.test(
          key,
        ) || containsSecretLikeCapabilityValue(nested),
    );
  }
  return /(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)/i.test(
    String(value || ""),
  );
}

export function safeCapabilityDefault(
  defaults: Record<string, unknown> | undefined,
  name: string,
): unknown {
  const value = defaults?.[name];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (containsSecretLikeCapabilityValue(value)) {
    return undefined;
  }
  return structuredClone(value);
}

// A preset may declare `uiSchema.<field>.defaultFrom` when one input's default
// has to follow another operator selection rather than a single static value.
// The rule only supplies a default: once the operator sets the field, their
// value lives in `values` and is never replaced.
export function dependentCapabilityDefault(
  detail: Pick<SchemaCapabilityDetail, "uiSchema" | "defaults">,
  values: Record<string, unknown>,
  name: string,
): unknown {
  const defaultFrom = recordValue(
    capabilityFieldUiSchema(detail.uiSchema, name)["defaultFrom"],
  );
  const field = String(defaultFrom["field"] ?? "").trim();
  const map = recordValue(defaultFrom["map"]);
  if (!field || Object.keys(map).length === 0) {
    return undefined;
  }
  const source = capabilityInputValue(values, detail.defaults, field);
  const key = String(source ?? "").trim();
  // Only an entry the preset actually declared may supply a default. A source
  // value such as `constructor` would otherwise resolve an inherited
  // `Object.prototype` member that `structuredClone` cannot clone.
  if (!Object.hasOwn(map, key)) {
    return undefined;
  }
  const mapped = map[key];
  if (mapped === undefined || containsSecretLikeCapabilityValue(mapped)) {
    return undefined;
  }
  return structuredClone(mapped);
}

export function capabilityEffectiveDefaults(
  detail: Pick<SchemaCapabilityDetail, "uiSchema" | "defaults">,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const effective = { ...(detail.defaults || {}) };
  for (const name of Object.keys(recordValue(detail.uiSchema))) {
    const derived = dependentCapabilityDefault(detail, values, name);
    if (derived !== undefined) {
      effective[name] = derived;
    }
  }
  return effective;
}

export function capabilityInputValue(
  values: Record<string, unknown>,
  defaults: Record<string, unknown> | undefined,
  name: string,
): unknown {
  return values[name] !== undefined ? values[name] : safeCapabilityDefault(defaults, name);
}

export function capabilityInputTextValue(
  values: Record<string, unknown>,
  defaults: Record<string, unknown> | undefined,
  name: string,
): string {
  const value = capabilityInputValue(values, defaults, name);
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    if (record.repository && record.number) {
      return `${String(record.repository)}#${String(record.number)}`;
    }
    return String(record.key || "");
  }
  return value === undefined || value === null ? "" : String(value);
}

export function capabilityInputStringArrayValue(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter(Boolean);
}

export function normalizeGitHubIssueInput(
  rawValue: string,
  defaultRepository: string | undefined,
): Record<string, unknown> {
  const value = rawValue.trim();
  if (!value) {
    return {};
  }
  const urlMatch = value.match(
    /^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/issues\/(\d+)(?:[/?#].*)?$/i,
  );
  if (urlMatch) {
    return {
      repository: urlMatch[1],
      number: Number(urlMatch[2]),
      url: `https://github.com/${urlMatch[1]}/issues/${urlMatch[2]}`,
    };
  }
  const qualifiedMatch = value.match(
    /^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)$/i,
  );
  if (qualifiedMatch) {
    return { repository: qualifiedMatch[1], number: Number(qualifiedMatch[2]) };
  }
  const shorthandMatch = value.match(/^#(\d+)$/);
  const normalizedRepository = String(defaultRepository || "").trim();
  if (shorthandMatch && OWNER_REPO_PATTERN.test(normalizedRepository)) {
    return { repository: normalizedRepository, number: Number(shorthandMatch[1]) };
  }
  return { repository: normalizedRepository, number: Number.NaN, raw: value };
}

export function issuePickerTextValue(
  value: unknown,
  provider: "jira" | "github",
): string {
  const issueValue = recordValue(value);
  if (provider === "github") {
    const repository = String(issueValue.repository || "").trim();
    const number = String(issueValue.number || "").trim();
    if (repository && number) {
      return `${repository}#${number}`;
    }
    return String(issueValue.raw || "");
  }
  return String(issueValue.key || "");
}

function CapabilityStringList({ value, onChange, ...props }: {
  value: unknown;
  onChange: (value: string[]) => void;
  id: string;
  disabled: boolean;
  "aria-label": string;
  "aria-invalid": boolean;
  "aria-describedby"?: string | undefined;
}): ReactElement {
  const serialized = capabilityInputStringArrayValue(value).join("\n");
  const [draft, setDraft] = useState(serialized);
  const lastEmitted = useRef(serialized);
  useEffect(() => {
    if (serialized !== lastEmitted.current) {
      setDraft(serialized);
      lastEmitted.current = serialized;
    }
  }, [serialized]);
  return <textarea {...props} value={draft} onChange={(event) => {
    const text = event.target.value;
    const items = text.split(/\r?\n/).filter((item) => item !== "");
    lastEmitted.current = items.join("\n");
    setDraft(text);
    onChange(items);
  }} />;
}

export function SchemaCapabilityFields({
  fields,
  detail,
  values,
  errors,
  disabled,
  repositoryOptions,
  branchOptions,
  onChange,
  onGitHubIssueRepositoryChange,
  defaultIssueRepository = "",
}: {
  fields: Array<[string, unknown]>;
  detail: Pick<SchemaCapabilityDetail, "inputSchema" | "uiSchema" | "defaults">;
  values: Record<string, unknown>;
  errors: Record<string, string>;
  disabled: boolean;
  repositoryOptions: RepositoryOption[];
  branchOptions: BranchOption[];
  onChange: (name: string, value: unknown) => void;
  onGitHubIssueRepositoryChange?: (repository: string) => void;
  defaultIssueRepository?: string;
}): ReactElement | null {
  const fieldGroupId = useId();
  if (fields.length === 0) {
    return null;
  }
  // Dependent defaults are resolved against the current values so a field whose
  // default follows another selection re-renders with the derived value.
  const effectiveDefaults = capabilityEffectiveDefaults(detail, values);
  return (
    <div className="grid-2">
      {fields.map(([name, rawSchema]) => {
        const fieldSchema = recordValue(rawSchema);
        const uiSchema = capabilityFieldUiSchema(detail.uiSchema, name);
        const widget = capabilityWidgetName(fieldSchema, uiSchema);
        const label = capabilityFieldLabel(name, fieldSchema);
        const value = capabilityInputValue(values, effectiveDefaults, name);
        const inputId = `queue-capability-input-${fieldGroupId}-${name}`;
        const error = errors[name];
        const description = String(fieldSchema.description || "").trim();
        const describedBy = [description ? `${inputId}-hint` : '', error ? `${inputId}-error` : ''].filter(Boolean).join(' ') || undefined;
        const choices = schemaChoiceOptions(fieldSchema);
        // Omission is distinct from an authored empty object/list. Only offer
        // it when no semantic default would immediately recreate the value.
        const omitControl = !schemaRequired(detail.inputSchema).has(name) && value !== undefined &&
          safeCapabilityDefault(effectiveDefaults, name) === undefined &&
          safeCapabilityDefault(fieldSchema, "default") === undefined
          ? <Button type="button" variant="secondary" disabled={disabled} onClick={() => onChange(name, undefined)}>Omit {label}</Button>
          : null;
        const hasTypeSafeRenderer =
          fieldSchema.type === "boolean" ||
          fieldSchema.type === "number" ||
          fieldSchema.type === "integer" ||
          fieldSchema.type === "array" ||
          fieldSchema.type === "object" ||
          Array.isArray(fieldSchema.enum) ||
          choices.length > 0;
        if (fieldSchema.readOnly === true) {
          return <label key={name} htmlFor={inputId}>{label}<textarea id={inputId} aria-label={label}
            value={complexCapabilityTextValue(value)} readOnly disabled={disabled} aria-describedby={describedBy} />
            {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
          </label>;
        }
        if (unsupportedCapabilityWidget(widget, fieldSchema) && !hasTypeSafeRenderer) {
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="text"
                value={capabilityInputTextValue(values, effectiveDefaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) => onChange(name, event.target.value)}
              />
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              <span className="notice small">
                Widget {widget} is unavailable; using a text field.
              </span>
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        if (widget === "jira.issue-picker" || widget === "github.issue-picker") {
          const issueValue = recordValue(value);
          const provider = widget === "github.issue-picker" ? "github" : "jira";
          const placeholder = String(
            uiSchema.searchPlaceholder ||
              (provider === "github" ? "Search GitHub issues" : "Search Jira issues"),
          );
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="text"
                value={issuePickerTextValue(value, provider)}
                placeholder={placeholder}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) => {
                  if (provider === "github") {
                    const normalizedIssue = normalizeGitHubIssueInput(
                      event.target.value,
                      String(
                        issueValue.repository ||
                          defaultIssueRepository ||
                          "",
                      ),
                    );
                    onChange(name, normalizedIssue);
                    const issueRepository = String(
                      normalizedIssue.repository || "",
                    ).trim();
                    if (
                      issueRepository &&
                      Number.isInteger(normalizedIssue.number) &&
                      Number(normalizedIssue.number) > 0
                    ) {
                      onGitHubIssueRepositoryChange?.(issueRepository);
                    }
                    return;
                  }
                  onChange(name, {
                    ...issueValue,
                    key: event.target.value,
                  });
                }}
              />
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        if (fieldSchema.type === "boolean") {
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="checkbox"
                checked={Boolean(value)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) => onChange(name, event.target.checked)}
              />
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        const itemSchema = recordValue(fieldSchema.items);
        if (
          fieldSchema.type === "array" &&
          (Array.isArray(itemSchema.enum) || schemaChoiceOptions(itemSchema).length > 0)
        ) {
          const itemChoices = schemaChoiceOptions(itemSchema);
          const itemEnumOptions = Array.isArray(itemSchema.enum)
            ? itemSchema.enum
            : [];
          const itemOptions: Array<{ label: string; value: unknown }> =
            itemChoices.length > 0
              ? itemChoices
              : itemEnumOptions.map((option: unknown) => ({
                  label: String(option),
                  value: option,
                }));
          const selectedValues = capabilityInputStringArrayValue(value);
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <select
                id={inputId}
                aria-label={label}
                multiple
                value={selectedValues}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) => {
                  const selected = Array.from(event.currentTarget.selectedOptions)
                    .map((option) => {
                      const matched = itemOptions.find(
                        (item) => String(item.value) === option.value,
                      );
                      return matched?.value ?? option.value;
                    });
                  onChange(name, selected);
                }}
              >
                {itemOptions.map((option) => (
                  <option key={String(option.value)} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        if (Array.isArray(fieldSchema.enum) || choices.length > 0) {
          const enumOptions = Array.isArray(fieldSchema.enum)
            ? fieldSchema.enum
            : [];
          const options: Array<{ label: string; value: unknown }> =
            choices.length > 0
              ? choices
              : enumOptions.map((option: unknown) => ({
                  label: String(option),
                  value: option,
                }));
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <select
                id={inputId}
                aria-label={label}
                value={capabilityInputTextValue(values, effectiveDefaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) => {
                  const selected = options.find(
                    (option) => String(option.value) === event.target.value,
                  );
                  onChange(name, selected?.value ?? event.target.value);
                }}
              >
                {!options.some((option) => String(option.value) === "") ? <option value="">Choose an option</option> : null}
                {options.map((option) => (
                  <option key={String(option.value)} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        if (widget === "textarea" || widget === "markdown" || widget === "json") {
          const structuredValue =
            widget === "json" &&
            (fieldSchema.type === "array" || fieldSchema.type === "object");
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <textarea
                id={inputId}
                aria-label={label}
                value={capabilityInputTextValue(values, effectiveDefaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) =>
                  onChange(
                    name,
                    structuredValue
                      ? parseComplexCapabilityValue(event.target.value)
                      : event.target.value,
                  )
                }
              />
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        if (fieldSchema.type === "object" && Object.keys(schemaProperties(fieldSchema)).length > 0) {
          const malformedObject = value !== undefined && (value === null || typeof value !== "object" || Array.isArray(value));
          const nestedValues = recordValue(value);
          const nestedErrors = Object.fromEntries(Object.entries(errors)
            .filter(([path]) => path.startsWith(`${name}.`))
            .map(([path, message]) => [path.slice(name.length + 1), message]));
          const nestedDefaults = Object.fromEntries(Object.entries(schemaProperties(fieldSchema))
            .map(([key, schema]) => [key, safeCapabilityDefault(recordValue(schema), "default")])
            .filter(([, fallback]) => fallback !== undefined));
          return <fieldset key={name} className="col-span-full min-w-0" disabled={disabled} aria-describedby={describedBy}>
            <legend>{label}</legend>
            {description ? <p id={`${inputId}-hint`} className="small">{description}</p> : null}
            {error ? <p id={`${inputId}-error`} className="notice small">{error}</p> : null}
            <SchemaCapabilityFields fields={Object.entries(schemaProperties(fieldSchema))}
              detail={{ inputSchema: fieldSchema, uiSchema, defaults: nestedDefaults }} values={nestedValues} errors={nestedErrors}
              disabled={disabled || malformedObject} repositoryOptions={repositoryOptions} branchOptions={branchOptions}
              defaultIssueRepository={defaultIssueRepository}
              {...(onGitHubIssueRepositoryChange ? { onGitHubIssueRepositoryChange } : {})}
              onChange={(key, next) => {
                if (malformedObject) return;
                const updated = { ...nestedValues, [key]: next };
                if (next === undefined) delete updated[key];
                onChange(name, updated);
              }} />
            {malformedObject ? <p role="alert">Correct the {label} JSON before editing its structured fields. The current text is retained.</p> : null}
            {fieldSchema.additionalProperties !== false ? <details open={malformedObject ? true : undefined}>
              <summary>Advanced {label} JSON</summary>
              <label htmlFor={`${inputId}-json`}>{label} JSON
                <textarea id={`${inputId}-json`} aria-label={`${label} JSON`} disabled={disabled}
                  value={complexCapabilityTextValue(value)}
                  onChange={(event) => onChange(name, parseComplexCapabilityValue(event.target.value))} />
              </label>
            </details> : null}
            {omitControl}
          </fieldset>;
        }
        if (fieldSchema.type === "array" && itemSchema.type === "object" && Object.keys(schemaProperties(itemSchema)).length > 0) {
          const items = Array.isArray(value) ? value : [];
          const itemErrors = Object.fromEntries(Object.entries(errors)
            .filter(([path]) => path.startsWith(`${name}.`))
            .map(([path, message]) => [path.slice(name.length + 1), message]));
          return <fieldset key={name} className="col-span-full min-w-0" disabled={disabled} aria-describedby={describedBy}>
            <legend>{label}</legend>
            {description ? <p id={`${inputId}-hint`} className="small">{description}</p> : null}
            {error ? <p id={`${inputId}-error`} className="notice small">{error}</p> : null}
            {items.map((item, index) => <div key={index}>
              <SchemaCapabilityFields fields={[[String(index), { ...itemSchema, title: `${label} item ${index + 1}` }]]}
                detail={{ inputSchema: { required: [String(index)] } }} values={{ [index]: item }} errors={itemErrors} disabled={disabled}
                repositoryOptions={repositoryOptions} branchOptions={branchOptions} defaultIssueRepository={defaultIssueRepository}
                onChange={(_key, updated) => onChange(name, items.map((previous, position) => position === index ? updated ?? {} : previous))} />
              <Button type="button" variant="secondary" disabled={disabled}
                onClick={() => onChange(name, items.filter((_item, position) => position !== index))}>Remove {label} item {index + 1}</Button>
            </div>)}
            <Button type="button" variant="secondary" disabled={disabled || (typeof fieldSchema.maxItems === "number" && items.length >= fieldSchema.maxItems)}
              onClick={() => onChange(name, [...items, {}])}>Add {label} item</Button>
            {omitControl}
          </fieldset>;
        }
        if (fieldSchema.type === "array" && itemSchema.type === "string") {
          return <div key={name}><label htmlFor={inputId}>{label}
            <CapabilityStringList id={inputId} aria-label={label} value={value} disabled={disabled}
              aria-invalid={Boolean(error)} aria-describedby={describedBy}
              onChange={(items) => onChange(name, items)} />
            <span className="small">Enter one item per line.</span>
            {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
            {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
          </label>{omitControl}</div>;
        }
        if (fieldSchema.type === "array" || fieldSchema.type === "object") {
          return (
            <div key={name}><label htmlFor={inputId}>
              {label}
              <textarea
                id={inputId}
                aria-label={label}
                value={complexCapabilityTextValue(value)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
                onChange={(event) =>
                  onChange(name, parseComplexCapabilityValue(event.target.value))
                }
              />
              {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
              {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
            </label>{omitControl}</div>
          );
        }
        const inputType = widget === "text" && fieldSchema.type === "string" ? "text" : textInputTypeForSchema(fieldSchema);
        return (
          <div key={name}><label htmlFor={inputId}>
            {label}
            <input
              id={inputId}
              aria-label={label}
              type={inputType}
              list={
                widget === "repository" ||
                widget === "repository-picker" ||
                widget === "repo" ||
                widget === "repo-picker" ||
                widget === "branch"
                  ? `${inputId}-options`
                  : undefined
              }
              value={capabilityInputTextValue(values, effectiveDefaults, name)}
              disabled={disabled}
              aria-invalid={Boolean(error)}
                aria-describedby={describedBy}
              onChange={(event) =>
                onChange(
                  name,
                  inputType === "number"
                    ? event.target.value === ""
                      ? ""
                      : Number(event.target.value)
                    : event.target.value,
                )
              }
            />
            {widget === "repository" ||
            widget === "repository-picker" ||
            widget === "repo" ||
            widget === "repo-picker" ? (
              <datalist id={`${inputId}-options`}>
                {repositoryOptions.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    label={option.label}
                  />
                ))}
              </datalist>
            ) : null}
            {widget === "branch" ? (
              <datalist id={`${inputId}-options`}>
                {branchOptions.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    label={option.label}
                  />
                ))}
              </datalist>
            ) : null}
            {description ? <span id={`${inputId}-hint`} className="small">{description}</span> : null}
            {error ? <span id={`${inputId}-error`} className="notice small">{error}</span> : null}
          </label>{omitControl}</div>
        );
      })}
    </div>
  );
}
