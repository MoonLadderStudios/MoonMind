# Spec Overlap & Merge Review

This document summarizes findings from a comprehensive review of all current specification documents in the `specs/` directory to identify overlapping scopes, duplicate initiatives, and candidates for merging.

## Executive Summary

After reviewing 83 spec directories, several major instances of duplication and identical scope were found. The most striking overlaps exist where multiple directories cover the same feature phase, or where directories share the exact same names or titles. Consolidating these specs will improve repository hygiene and establish a single source of truth for each feature.

---

## Primary Merge Candidates

These directories exhibit identical or highly overlapping scopes and are strong candidates for immediate merging or deduplication.

### 1. Worker Pause System
- **`038-worker-pause`**: *Feature Specification: Worker Pause System* (Branch `034-worker-pause`)
- **`040-worker-pause`**: *Feature Specification: Worker Pause System* (Branch `035-worker-pause`)
**Recommendation:** These two specs share an identical feature title and directory topic. Merge `040-worker-pause` into `038-worker-pause` (or whichever encompasses the latest state) and retain only one source of truth for the worker pause mechanism.

### 2. Claude API Key Gating
- **`044-claude-runtime-gate`**: *Feature Specification: Claude Runtime API-Key Gating*
- **`046-claude-api-key-gate`**: *Feature Specification: Claude Runtime Enabled by API Key*
**Recommendation:** Both specs describe gating the new Claude runtime behind API key requirements. These should be combined into a single authoritative spec under the `044` directory to avoid split requirements.

### 3. Manifest Phase 0
- **`032-manifest-phase0`**: *Feature Specification: Manifest Queue Phase 0 Alignment*
- **`034-manifest-phase0`**: *Feature Specification: Manifest Phase 0 Rebaseline*
**Recommendation:** Both specs describe the Phase 0 baseline/alignment for the Manifest system. They should be merged to provide a unified feature baseline, likely prioritizing the "rebaseline" adjustments.

### 4. Jules External Events Duplicate
- **`048-jules-external-events`**: *(Empty/Stub spec containing only "DOC-REQ-001")*
- **`066-jules-external-events`**: *Feature Specification: Jules Temporal External Events*
**Recommendation:** `048-jules-external-events` appears to be an abandoned stub or duplicate branch name. It should be deleted entirely, keeping `066-jules-external-events` as the authoritative spec.

### 5. Task Presets
- **`026-task-presets`**: *Feature Specification: Task Presets Strategy Alignment*
- **`028-task-presets`**: *Feature Specification: Task Presets Catalog*
**Recommendation:** Both touch on the same core `task-presets` domain. While one focuses on strategy and the other on the catalog implementation, combining them into a comprehensive Task Presets spec would streamline documentation.

---

## Secondary Overlaps

These directories represent related phases or connected systems that don't strictly require merging, but might benefit from cross-linking or consolidation into an epic-level spec.

- **Task Proposal Tracking**: `029-task-proposal-phase2` (Task Proposal Queue Phase 2) and `037-task-proposal-update` (Task Proposal Targeting Policy). Both relate to task proposals but cover different phases/aspects. 
- **Agent Run Constraints**: `072-agent-run-contracts` (Agent Runtime Phase 1 Contracts) and `073-agent-run-workflow` (MoonMind.AgentRun Workflow). They correctly partition contracts vs. workflow implementations, but are deeply coupled.

---

## Next Steps

1. **Delete Dead Stubs**: Remove `048-jules-external-events`.
2. **Review & Merge**: Review the content of `038` and `040` (Worker Pause) alongside `044` and `046` (Claude gating). Combine all non-redundant requirements into the earliest-numbered spec and delete the duplicate directory.
3. **Consolidate Presets & Manifest**: Evaluate if `026/028` and `032/034` were intended as linear progressions (e.g., iterative specs). If so, merge the delta into the definitive specification document.
