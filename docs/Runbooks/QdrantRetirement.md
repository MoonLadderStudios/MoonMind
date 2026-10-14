# Retiring a Leftover Qdrant Service

The current release has no vector store. A deployment upgraded from an older,
Qdrant-bearing release can still have the old `qdrant` container running and
its `qdrant-storage` volume on disk: removing a service from Compose does not
stop an already-running container, and normal updates never delete volumes.
This runbook retires that leftover safely. Nothing in the normal update path
performs these steps; each is an explicit operator action.

`qdrant-storage` and `moonmind_retrieval_state` are different data. Retiring
Qdrant never touches retrieval state.

## 1. Check for remaining vector work

Before stopping anything, confirm that no active or pending workflow,
retryable Activity, or recurring schedule still targets the old vector
backend. Current releases reject vector retrieval inputs at admission, so new
work cannot add to this; anything found belongs to the old release and should
drain there first rather than be retried against a missing backend.

## 2. Preserve the data

Qdrant can hold documents, metadata, or text that exist nowhere else, so do not
assume its contents are reproducible just because the vectors are derived.

1. Record the old image (`qdrant/qdrant:v1.17.1`), the exact mount
   (`qdrant-storage:/qdrant/storage`), and the container's configuration.
2. Take a recoverable snapshot of the volume, plus a logical export of any
   collections whose payloads may be unique.
3. Verify that the snapshot or export restores.
4. Keep these copies in operator-controlled storage, never in issues or source
   control, because payloads can contain private content.

## 3. Remove only the exact container

1. Identify the container by Compose project, service `qdrant`, and container
   ID. If ownership is ambiguous (for example, more than one candidate or a
   different project), stop and resolve it first.
2. Stop and remove that one container, then confirm it is gone. Repeating the
   check is safe.

Do not use `docker compose down -v`, volume prune, orphan cleanup, or
filesystem deletion for this: they can remove PostgreSQL, MinIO, workspace, or
Omnigent data along with Qdrant.

## 4. Keep the volume for a while

Leave `qdrant-storage` and the exports in place for a recovery window. Deleting
the volume afterwards is a separate, deliberate action taken only after the
export has been verified.

## 5. Rolling back

Rolling back means redeploying the previous matching application, Compose, and
image revision against the preserved data, after checking schema
compatibility. The current release does not add an optional Qdrant profile or
adapter back as a rollback path.
