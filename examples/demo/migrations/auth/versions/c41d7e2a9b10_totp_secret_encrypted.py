"""totp secret encrypted

The column holds the Fernet token of the secret (``enc1:...``), which does not fit in 64 characters.
Secrets written before this revision stay readable as plain text; run ``pachifast users encrypt-mfa-secrets``
to encrypt them.
"""

from alembic import op
import sqlalchemy as sa


revision = 'c41d7e2a9b10'
down_revision = '90c0a092913c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pachifast_mfa_config', schema=None) as batch_op:
        batch_op.alter_column('totp_secret', existing_type=sa.String(length=64), type_=sa.String(length=255), existing_nullable=True)


def downgrade():
    # encrypted values do not fit in 64 characters: run the downgrade only after disabling the TOTP of the users
    with op.batch_alter_table('pachifast_mfa_config', schema=None) as batch_op:
        batch_op.alter_column('totp_secret', existing_type=sa.String(length=255), type_=sa.String(length=64), existing_nullable=True)
