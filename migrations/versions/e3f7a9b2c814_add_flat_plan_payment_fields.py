"""add flat payment fields to plans"""

import sqlalchemy as sa
from alembic import op

revision = "e3f7a9b2c814"
down_revision = "d7e3a9b4c621"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plans",
        sa.Column("paid_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("plans", sa.Column("paid_on", sa.Date(), nullable=True))
    op.add_column(
        "plans",
        sa.Column("payment_status", sa.String(length=20), nullable=False, server_default="planned"),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("plans", "paid_amount_minor", server_default=None)
        op.alter_column("plans", "payment_status", server_default=None)


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("plans") as batch:
            batch.drop_column("payment_status")
            batch.drop_column("paid_on")
            batch.drop_column("paid_amount_minor")
    else:
        op.drop_column("plans", "payment_status")
        op.drop_column("plans", "paid_on")
        op.drop_column("plans", "paid_amount_minor")
