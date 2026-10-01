import { useQuery } from "@tanstack/react-query";

interface ProviderProfileResponse {
  profile_id: string;
  enabled: boolean;
  launch_ready: boolean;
}

// GitHub access is not a first-run prerequisite: scratch work needs no PAT,
// and repository work reports its own missing connection when it runs
// (MoonLadderStudios/MoonMind#4023).
export function DashboardAlerts() {
  const {
    data: profilesData,
    isLoading: profilesLoading,
    isError: profilesError,
  } = useQuery<ProviderProfileResponse[]>({
    queryKey: ["provider-profiles"],
    queryFn: async () => {
      const response = await fetch("/api/v1/provider-profiles", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("Failed to fetch provider profiles");
      }
      return response.json();
    },
  });

  if (profilesLoading || (!profilesData && !profilesError)) {
    return null;
  }

  if (profilesError) {
    return (
      <div className="notice notice-warning" style={{ marginBottom: "20px" }}>
        MoonMind could not verify provider profile readiness. Review Provider
        Profiles in Settings.
        <div style={{ marginTop: "12px" }}>
          <a
            href="/settings/providers-secrets"
            className="btn btn-sm btn-outline"
          >
            Open Settings
          </a>
        </div>
      </div>
    );
  }

  const needsProviderProfileSetup = !(
    profilesData?.some((profile) => profile.launch_ready) ?? false
  );

  if (!needsProviderProfileSetup) {
    return null;
  }

  return (
    <div className="notice notice-warning" style={{ marginBottom: "20px" }}>
      <strong>First-Run Setup:</strong> Complete setup before running agent
      tasks.
      <ul
        style={{ marginTop: "8px", marginLeft: "20px", listStyleType: "disc" }}
      >
        <li>Set up and enable at least one provider profile in Settings.</li>
      </ul>
      <div style={{ marginTop: "12px" }}>
        <a
          href="/settings/providers-secrets"
          className="btn btn-sm btn-outline"
        >
          Open Settings
        </a>
      </div>
    </div>
  );
}
export default DashboardAlerts;
