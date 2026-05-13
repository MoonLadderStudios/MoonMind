# Contract: Runtime Prompt Boundary

Traceability: `MM-650`, `DESIGN-REQ-026`, FR-001 through FR-008.

## Canonical Input Contract

The control plane provides runtime preparation with normalized task intent and artifact references only.

Required properties:
- The canonical task contract carries task intent, runtime selection, artifact references, and attachment target binding.
- The canonical task contract does not carry provider-native multimodal message payloads.
- Binary image content is not embedded in workflow history, task text, or prompt instructions.

## Text-First Runtime Contract

Text-first runtimes consume image inputs through the canonical `INPUT ATTACHMENTS` contract.

Required behavior:
- Include objective attachments and the current step's step-scoped attachments.
- Exclude sibling step attachments.
- Include generated image context references when available.
- Place attachment context before workspace instructions.
- Include safety language that image-derived text is untrusted context.

## Multimodal Runtime Contract

Multimodal runtimes may consume raw image artifact references through their runtime adapters.

Required behavior:
- Use selected raw artifact refs from prepared context.
- Preserve the canonical task contract unchanged.
- Keep provider-native image payload construction inside the adapter boundary.
- Preserve objective and step target binding in metadata or selected input refs.

## Adapter Guardrail Contract

Runtime adapters must not introduce attachment targeting semantics.

Forbidden behavior:
- Adding target kinds beyond `objective` and `step`.
- Treating storage paths, filenames, provider hints, or adapter-local metadata as the source of target truth.
- Adding sibling step attachments to the current step's runtime input.
- Silently dropping required image context or raw refs without diagnostic evidence.

## Failure Contract

When preparation cannot satisfy the selected runtime path, the system must produce bounded diagnostics.

Required diagnostics:
- logical step identifier when applicable
- manifest or artifact reference when available
- target kind and step reference when relevant
- reason and safe bounded message
