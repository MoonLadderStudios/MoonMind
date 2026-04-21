# Quickstart: Skill Zip Import

## Manual Runtime Check

1. Open `/tasks/skills`.
2. Select `Create New Skill`.
3. Choose a zip with this shape:

```text
zip-skill/
├── SKILL.md
├── scripts/
│   └── check.sh
├── references/
│   └── context.md
└── assets/
    └── icon.txt
```

4. Ensure `SKILL.md` begins with frontmatter:

```markdown
---
name: zip-skill
description: Uploaded from a zip.
---
# Zip Skill

Use this bundle.
```

5. Upload the zip.
6. Confirm the list refreshes and `zip-skill` is selected.
7. Confirm the saved local mirror contains `SKILL.md` and the optional directories.

## Automated Checks

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py -k 'skill_import_api or upload_dashboard_skill_zip'
./tools/test_unit.sh --ui-args frontend/src/entrypoints/skills.test.tsx
```
