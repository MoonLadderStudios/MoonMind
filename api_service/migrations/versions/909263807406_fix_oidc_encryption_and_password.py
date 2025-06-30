"""ensure oidc constraint and encrypted columns"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlalchemy_utils

revision: str = "909263807406"
down_revision: Union[str, None] = '9e9bd65e1b9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'user',
        'hashed_password',
        existing_type=sa.Text(),
        type_=sa.Text(),
        nullable=True,
    )

    op.alter_column(
        'user_profile',
        'google_api_key_encrypted',
        type_=sqlalchemy_utils.types.encrypted.encrypted_type.EncryptedType(sa.Text),
        existing_nullable=True,
    )
    op.alter_column(
        'user_profile',
        'openai_api_key_encrypted',
        type_=sqlalchemy_utils.types.encrypted.encrypted_type.EncryptedType(sa.Text),
        existing_nullable=True,
    )

    op.drop_constraint('uq_oidc_identity', 'user', type_='unique')
    op.create_unique_constraint('uq_oidc_identity', 'user', ['oidc_provider', 'oidc_subject'])


def downgrade() -> None:
    # Restore unique constraint to its previous definition
    op.drop_constraint('uq_oidc_identity', 'user', type_='unique')
    op.create_unique_constraint('uq_oidc_identity', 'user', ['oidc_identity'])

    # Revert encrypted columns to their original types and nullability
    op.alter_column(
        'user_profile',
        'openai_api_key_encrypted',
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        'user_profile',
        'google_api_key_encrypted',
        type_=sa.Text(),
        existing_nullable=False,
    )

    # Revert hashed_password to non-nullable state
    op.alter_column(
        'user',
        'hashed_password',
        existing_type=sa.Text(),
        type_=sa.Text(),
        nullable=False,
    )
