import uuid
import re

def make_branch(title, skill_name):
    desc_source = str(title or skill_name or "").strip()
    if desc_source:
        clean_desc = re.sub(r"[^a-z0-9]+", "-", desc_source.lower()).strip("-")
        clean_desc = clean_desc[:40].strip("-")
        prefix = f"auto-{clean_desc}-" if clean_desc else "auto-"
    else:
        prefix = "auto-"
    return f"{prefix}{str(uuid.uuid4())[:8]}"

print(make_branch("Fix the issue with the foo bar baz testing something really long and truncated at a dash----", ""))
print(make_branch("", "pr-resolver"))
print(make_branch("", ""))

