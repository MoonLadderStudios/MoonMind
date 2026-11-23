import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import formFields from '../../../tests/fixtures/security/readiness_form_fields.json';

interface PresetForm {
  inputSchema: { properties: Record<string, unknown> };
  uiSchema?: Record<string, unknown>;
  defaults: Record<string, unknown>;
}

const yaml = createRequire(import.meta.url)('js-yaml') as {
  load(text: string): { annotations: PresetForm };
};

// The API expands the seed's framework reference before Create receives the form.
// The catalog API test checks these representative controls against each
// installed pack's declared profile.
export function readinessPresetForm(slug: string): PresetForm {
  const { annotations } = yaml.load(readFileSync(`api_service/data/presets/${slug}.yaml`, 'utf8'));
  const fields = annotations.inputSchema.properties;
  const { 'x-framework-inputs': pack, ...presentation } = fields.system_inputs as {
    'x-framework-inputs': keyof typeof formFields;
  };
  if (!formFields[pack]) throw new Error(`Missing readiness form fixture for ${pack}`);
  fields.system_inputs = { ...presentation, type: 'object', additionalProperties: false, properties: formFields[pack] };
  return annotations;
}
