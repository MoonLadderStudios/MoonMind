# Contract: Skill Import API

## POST /api/skills/imports

Creates a local skill from one uploaded zip bundle.

Request:

- Content type: `multipart/form-data`
- `file`: required binary zip file
- `collision_policy`: optional enum, `reject` or `new_version`, default `reject`

Success response:

```json
{
  "import_id": "skill-import-...",
  "status": "saved",
  "skill_id": "zip-skill",
  "version_id": "zip-skill-...",
  "version_number": 1,
  "name": "zip-skill",
  "description": "Uploaded from a zip.",
  "warnings": []
}
```

Error behavior:

- `400`: invalid zip, invalid structure, unsafe entry, invalid manifest, or unsupported file shape
- `409`: same-name skill exists while `collision_policy=reject`
- `413`: archive exceeds configured size/count limits

Side effects:

- Valid imports write one skill directory under the configured local skill mirror.
- Invalid imports do not leave a partial skill directory.
- Uploaded scripts are never executed.

## Skills Page UI

- Upload form posts to `/api/skills/imports`.
- On success, invalidate skills detail data, close create mode, clear selected zip file, and select `response.name`.
