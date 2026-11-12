"""add documents stored in PostgreSQL"""

import sqlalchemy as sa
from alembic import op

revision = "9a3d7b4c6e21"
down_revision = "a92f6c1d4e70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("expense_id", sa.String(length=36), sa.ForeignKey("expenses.id")),
        sa.Column("plan_id", sa.String(length=36), sa.ForeignKey("plans.id")),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="web"),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520"),
        sa.CheckConstraint("(expense_id IS NULL) OR (plan_id IS NULL)"),
        sa.CheckConstraint("source IN ('web', 'telegram', 'forwarded')"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_documents_expense_id", "documents", ["expense_id"])
    op.create_index("ix_documents_plan_id", "documents", ["plan_id"])


def downgrade():
    op.drop_table("documents")
