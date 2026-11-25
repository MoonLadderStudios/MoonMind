import type { components } from "../generated/openapi";
import type { ModelSelection } from "./modelSelection";
import type { ProviderProfileTierCapabilities } from "../utils/providerProfileTiers";

/** Advisory Provider Profile reads shared by every surface that authors a model selection. */
export type ProviderProfileTierPreviewItem =
  components["schemas"]["ProviderProfileTierPreviewItem"];
type ProviderProfileTierPreviewResponse =
  components["schemas"]["ProviderProfileTierPreviewResponse"];

export interface ModelTierPreviewSelection {
  id: string;
  profileId: string;
  runtime: ModelSelection;
}

export interface ProviderProfileTierPreviewResult {
  previews: Record<string, ProviderProfileTierPreviewItem>;
  errors: Record<string, string>;
}

function profileEndpoint(providerProfilesEndpoint: string, profileId: string, suffix: string): string {
  const queryIndex = providerProfilesEndpoint.search(/[?#]/);
  const endpointWithoutQuery = queryIndex >= 0
    ? providerProfilesEndpoint.slice(0, queryIndex)
    : providerProfilesEndpoint;
  return `${endpointWithoutQuery.replace(/\/+$/, "")}/${encodeURIComponent(profileId)}/${suffix}`;
}

export async function fetchProviderProfileTierPreviews(
  providerProfilesEndpoint: string,
  selections: ModelTierPreviewSelection[],
  responseErrorMessage: (response: Response, fallback: string) => Promise<string>,
): Promise<ProviderProfileTierPreviewResult> {
  const clampSelectionsByProfile = new Map<
    string,
    ModelTierPreviewSelection[]
  >();
  const requestGroups: Array<{
    profileId: string;
    selections: ModelTierPreviewSelection[];
  }> = [];
  for (const selection of selections) {
    if (selection.runtime.tierFallback === "strict") {
      requestGroups.push({
        profileId: selection.profileId,
        selections: [selection],
      });
      continue;
    }
    const existing = clampSelectionsByProfile.get(selection.profileId) || [];
    existing.push(selection);
    clampSelectionsByProfile.set(selection.profileId, existing);
  }
  for (const [profileId, profileSelections] of clampSelectionsByProfile) {
    requestGroups.push({ profileId, selections: profileSelections });
  }

  const results = await Promise.all(
    requestGroups.map(
      async ({ profileId, selections: profileSelections }) => {
        const errors: Record<string, string> = {};
        try {
          const response = await fetch(
            profileEndpoint(providerProfilesEndpoint, profileId, "model-tiers:preview"),
            {
              method: "POST",
              headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                steps: profileSelections.map((selection) => ({
                  id: selection.id,
                  ...selection.runtime,
                })),
              }),
            },
          );
          if (!response.ok) {
            const message = await responseErrorMessage(
              response,
              "Failed to preview Provider Profile model tiers.",
            );
            for (const selection of profileSelections) {
              errors[selection.id] = message;
            }
            return { response: null, errors };
          }
          return {
            response:
              (await response.json()) as ProviderProfileTierPreviewResponse,
            errors,
          };
        } catch (error) {
          const message = error instanceof Error
            ? error.message
            : "Failed to preview Provider Profile model tiers.";
          for (const selection of profileSelections) {
            errors[selection.id] = message;
          }
          return { response: null, errors };
        }
      },
    ),
  );

  const previews: Record<string, ProviderProfileTierPreviewItem> = {};
  const errors: Record<string, string> = {};
  for (const result of results) {
    Object.assign(errors, result.errors);
    if (result.response) {
      for (const item of result.response.items) {
        previews[item.stepId] = item;
      }
    }
  }
  return { previews, errors };
}

const CAPABILITIES_UNAVAILABLE = "Profile suggestions could not be loaded. Manual values are preserved.";

/** Model and effort suggestions for one profile; a failure never blocks manual values. */
export async function fetchProviderProfileTierCapabilities(
  providerProfilesEndpoint: string,
  profileId: string,
  signal: AbortSignal,
): Promise<{ capabilities?: ProviderProfileTierCapabilities; error?: string }> {
  try {
    const response = await fetch(
      profileEndpoint(providerProfilesEndpoint, profileId, "capabilities"),
      { headers: { Accept: "application/json" }, signal },
    );
    if (!response.ok) throw new Error(CAPABILITIES_UNAVAILABLE);
    const capabilities = await response.json() as ProviderProfileTierCapabilities;
    if (!Array.isArray(capabilities.model?.options) || !Array.isArray(capabilities.effort?.options)) throw new Error("Malformed capability response");
    return { capabilities };
  } catch (error) {
    if (signal.aborted) throw error;
    return { error: CAPABILITIES_UNAVAILABLE };
  }
}
