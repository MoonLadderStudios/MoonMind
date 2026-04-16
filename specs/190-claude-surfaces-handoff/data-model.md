# Data Model: Claude Surfaces Handoff

## Claude Surface Binding

Represents one durable client surface attached to a Claude managed session.

Fields:
- `surface_id`: nonblank stable surface identifier.
- `surface_kind`: terminal, vscode, jetbrains, desktop, web, mobile, scheduler, channel, or sdk.
- `projection_mode`: primary or remote_projection.
- `connection_state`: connected, disconnected, reconnecting, or detached.
- `interactive`: whether the surface can accept user interaction.
- `capabilities`: bounded set of surface capabilities such as approvals, diff review, notifications, QR connect, or keyboard control.
- `last_seen_at`: optional timestamp for the latest observed surface activity.

Validation:
- A managed session may contain at most one primary binding.
- Remote Control surfaces use `remote_projection`.
- Unsupported surface kinds, projection modes, or blank identifiers fail validation.

## Claude Managed Session Handoff Fields

Extends the existing Claude managed-session record.

Fields:
- `handoff_from_session_id`: source session for cloud handoff.
- `handoff_seed_artifact_refs`: bounded nonblank artifact refs used to seed a cloud handoff destination.

Validation:
- Cloud handoff destination uses `execution_owner = anthropic_cloud_vm`.
- Cloud handoff destination has projection mode `handoff`.
- Cloud handoff destination has a distinct `session_id`.
- Seed refs are references only, not embedded summaries.

## Claude Surface Lifecycle Event

Represents normalized surface and handoff activity.

Fields:
- `event_id`: nonblank event identifier.
- `session_id`: affected session identifier.
- `surface_id`: affected surface identifier when the event is surface-scoped.
- `event_name`: one normalized surface lifecycle event name.
- `source_session_id`: source session for handoff events.
- `destination_session_id`: destination session for handoff events.
- `handoff_seed_artifact_refs`: bounded seed refs for handoff events.
- `occurred_at`: event timestamp.
- `metadata`: compact metadata only.

Validation:
- Surface-scoped events require `surface_id`.
- Handoff events require source and destination session identifiers.
- Handoff events may carry seed refs but must not embed summary payloads.

## Claude Surface Flow

Deterministic fixture that models one local session with local primary surface, Remote Control projection, disconnect/reconnect, resume to another local surface, and cloud handoff destination.

Validation:
- Projection preserves source session identity and execution owner.
- Resume preserves canonical session identity when execution owner is unchanged.
- Handoff creates a distinct cloud-owned destination with lineage.
- Events use normalized names and bounded metadata.
