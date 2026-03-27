"""fix claude minimax seed in db

Revision ID: 93f6b4a2d1e0
Revises: fa1b2c3d4e5f
Create Date: 2026-03-27 12:00:00.000000

"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93f6b4a2d1e0'
down_revision: Union[str, None, Sequence[str]] = 'fa1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fetch the claude_minimax profile
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT env_template, secret_refs FROM managed_agent_auth_profiles WHERE profile_id = 'claude_minimax'")
    )
    row = result.fetchone()
    
    if row:
        env_template = row[0] or {}
        secret_refs = row[1] or {}
        
        # We need to parse json if it's stored as strings, or dict if stored as JSONB.
        if isinstance(env_template, str):
            env_template = json.loads(env_template)
        if isinstance(secret_refs, str):
            secret_refs = json.loads(secret_refs)

        modified = False
        
        # If the old template pattern exists in the database record:
        if "ANTHROPIC_AUTH_TOKEN" in env_template and env_template["ANTHROPIC_AUTH_TOKEN"] == "${anthropic_api_key}":
            del env_template["ANTHROPIC_AUTH_TOKEN"]
            modified = True
            
        if "anthropic_api_key" in secret_refs:
            # Re-map correctly
            val = secret_refs.pop("anthropic_api_key")
            secret_refs["ANTHROPIC_AUTH_TOKEN"] = val
            modified = True
            
        if modified:
            # We construct a parameterized string if the driver requires json.dumps, 
            # but SQLAlchemy handles dict -> JSON for postgres and sqlite.
            conn.execute(
                sa.text(
                    "UPDATE managed_agent_auth_profiles SET env_template = :env_template, secret_refs = :secret_refs WHERE profile_id = 'claude_minimax'"
                ).bindparams(
                    sa.bindparam("env_template", value=json.dumps(env_template) if isinstance(row[0], str) else env_template),
                    sa.bindparam("secret_refs", value=json.dumps(secret_refs) if isinstance(row[1], str) else secret_refs)
                )
            )

def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT env_template, secret_refs FROM managed_agent_auth_profiles WHERE profile_id = 'claude_minimax'")
    )
    row = result.fetchone()
    
    if row:
        env_template = row[0] or {}
        secret_refs = row[1] or {}
        
        if isinstance(env_template, str):
            env_template = json.loads(env_template)
        if isinstance(secret_refs, str):
            secret_refs = json.loads(secret_refs)

        modified = False
        
        if "ANTHROPIC_AUTH_TOKEN" in secret_refs:
            val = secret_refs.pop("ANTHROPIC_AUTH_TOKEN")
            secret_refs["anthropic_api_key"] = val
            env_template["ANTHROPIC_AUTH_TOKEN"] = "${anthropic_api_key}"
            modified = True
            
        if modified:
            conn.execute(
                sa.text(
                    "UPDATE managed_agent_auth_profiles SET env_template = :env_template, secret_refs = :secret_refs WHERE profile_id = 'claude_minimax'"
                ).bindparams(
                    sa.bindparam("env_template", value=json.dumps(env_template) if isinstance(row[0], str) else env_template),
                    sa.bindparam("secret_refs", value=json.dumps(secret_refs) if isinstance(row[1], str) else secret_refs)
                )
            )
