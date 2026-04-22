# MM-471 MoonSpec Orchestration Input

## Source

- Jira issue: MM-471
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Launch PentestGPT through approved runner profiles and deterministic container metadata
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-471 from MM project
Summary: Launch PentestGPT through approved runner profiles and deterministic container metadata
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-471 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-471: Launch PentestGPT through approved runner profiles and deterministic container metadata

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 10. Runner profile model
  - 12. Launch and materialization pipeline
  - 15. Security, isolation, and policy rules
  - 17.4 Orphan cleanup
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-008

User Story
As a platform operator, I need PentestGPT containers launched only through approved runner profiles so image, mounts, network, devices, resource limits, ownership labels, and cleanup behavior are controlled by MoonMind rather than user input.

Acceptance Criteria
- `pentestgpt-safe` is available as the default one-shot runner profile with restricted egress and no device policy.
- `pentestgpt-vpn-lab` is opt-in and requires scope approval plus the documented VPN/lab network attachment conditions before `NET_ADMIN` or `/dev/net/tun` are present.
- All launched containers use the pinned MoonMind-owned image, wrapper entrypoint, deterministic name pattern, and documented `moonmind.*` labels.
- No runner profile can mount the raw host Docker socket, accept arbitrary host paths, inherit unrelated auth volumes, or allow arbitrary user-selected images.
- Orphan cleanup can find PentestGPT containers by deterministic labels without relying on free-form logs.

Requirements
- Encode runner profile policy as data or typed configuration controlled by MoonMind.
- Use the existing Docker-out-of-Docker boundary rather than raw upstream compose or host Docker access from task input.
- Keep network policy and device/capability decisions attached to runner profiles.

Relevant Implementation Notes
- Preserve MM-471 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for the PentestGPT runner profile model, launch and materialization pipeline, security policy, isolation rules, and orphan cleanup.
- Ensure runner profiles, not task input, control image, mounts, network, devices, capabilities, resource limits, ownership labels, and cleanup behavior.
- Keep `pentestgpt-safe` conservative and available by default.
- Keep `pentestgpt-vpn-lab` opt-in and gated by scope approval plus documented VPN/lab network attachment conditions before granting `NET_ADMIN` or `/dev/net/tun`.
- Prevent raw host Docker socket mounts, arbitrary host paths, unrelated auth volume inheritance, and arbitrary user-selected images.
- Make deterministic `moonmind.*` labels sufficient for orphan cleanup discovery.

Non-Goals
- Implementing broader PentestGPT provider behavior beyond approved runner-profile launch controls and deterministic container metadata.
- Allowing user input to select arbitrary images, mounts, device access, capabilities, or host Docker access.
- Treating VPN/lab network attachment as allowed without explicit scope approval and documented runner-profile conditions.

Validation
- Verify `pentestgpt-safe` exists as the default one-shot runner profile with restricted egress and no device policy.
- Verify `pentestgpt-vpn-lab` requires scope approval and documented VPN/lab network attachment conditions before `NET_ADMIN` or `/dev/net/tun` are present.
- Verify launched containers use the pinned MoonMind-owned image, wrapper entrypoint, deterministic name pattern, and documented `moonmind.*` labels.
- Verify runner profiles reject raw host Docker socket mounts, arbitrary host paths, unrelated auth volume inheritance, and arbitrary user-selected images.
- Verify orphan cleanup can find PentestGPT containers by deterministic labels without relying on free-form logs.
- Verify runner profile policy is encoded as MoonMind-controlled data or typed configuration.

Needs Clarification
- None
