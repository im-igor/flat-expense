"""mark Telegram expenses that need web review"""

import sqlalchemy as sa
from alembic import op

revision = "a92f6c1d4e70"
down_revision = "e3f7a9b2c814"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "expenses",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("expenses", "needs_review", server_default=None)


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("expenses") as batch:
            batch.drop_column("needs_review")
    else:
        op.drop_column("expenses", "needs_review")
