# Contract: Deployment Update Settings Card

## Placement

The Deployment Update card is rendered inside `Settings -> Operations` at `/tasks/settings?section=operations`. It is not added as a top-level navigation item.

## Read Endpoints

- `GET /api/v1/operations/deployment/stacks/moonmind`
- `GET /api/v1/operations/deployment/image-targets?stack=moonmind`

The card must tolerate optional future fields on stack state for recent action summaries and links.

## Submit Endpoint

`POST /api/v1/operations/deployment/update`

```json
{
  "stack": "moonmind",
  "image": {
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "reference": "20260425.1234"
  },
  "mode": "changed_services",
  "removeOrphans": true,
  "wait": true,
  "runSmokeCheck": false,
  "pauseWork": false,
  "pruneOldImages": false,
  "reason": "Operator supplied reason"
}
```

## Confirmation

Before submission the UI confirmation must include current image, target image, mode, stack, affected service summary, mutable-tag warning when applicable, and a service restart warning.
