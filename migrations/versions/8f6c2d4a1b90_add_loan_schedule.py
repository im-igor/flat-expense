"""add loan schedule"""

import sqlalchemy as sa
from alembic import op

revision = "8f6c2d4a1b90"
down_revision = "db2a3db54d81"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "loan_import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "source_sha256"),
    )
    op.create_table(
        "loan_periods",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("usage_month", sa.Date(), nullable=False),
        sa.Column("period_index", sa.Integer(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("key_rate", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("rate_kind", sa.String(length=20), nullable=False),
        sa.Column("rate_effective_on", sa.Date(), nullable=True),
        sa.Column("start_balance_minor", sa.BigInteger(), nullable=False),
        sa.Column("interest_minor", sa.BigInteger(), nullable=False),
        sa.Column("principal_minor", sa.BigInteger(), nullable=False),
        sa.Column("payment_minor", sa.BigInteger(), nullable=False),
        sa.Column("end_balance_minor", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period_index >= 0"),
        sa.CheckConstraint("start_balance_minor >= 0"),
        sa.CheckConstraint("interest_minor >= 0"),
        sa.CheckConstraint("principal_minor >= 0"),
        sa.CheckConstraint("payment_minor >= 0"),
        sa.CheckConstraint("end_balance_minor >= 0"),
        sa.CheckConstraint("key_rate IS NULL OR (key_rate >= 0 AND key_rate <= 100)"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "period_index"),
    )
    op.create_index("ix_loan_periods_owner_id", "loan_periods", ["owner_id"])
    op.create_index("loan_owner_payment_date", "loan_periods", ["owner_id", "payment_date"])


def downgrade():
    op.drop_index("loan_owner_payment_date", table_name="loan_periods")
    op.drop_index("ix_loan_periods_owner_id", table_name="loan_periods")
    op.drop_table("loan_periods")
    op.drop_table("loan_import_batches")
