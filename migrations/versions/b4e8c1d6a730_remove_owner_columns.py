"""remove Telegram-linked ownership from the single-user schema

Revision ID: b4e8c1d6a730
Revises: 9a3d7b4c6e21
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e8c1d6a730"
down_revision = "9a3d7b4c6e21"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        # SQLite is used only by the migration regression test. Production is
        # PostgreSQL, where the historical constraints can be removed safely.
        return

    for index in (
        "ix_audit_owner_id",
        "ix_categories_owner_id",
        "ix_documents_owner_id",
        "ix_expenses_owner_id",
        "expense_owner_date",
        "ix_plans_owner_id",
        "ix_loan_periods_owner_id",
        "loan_owner_payment_date",
    ):
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index}"))

    for table, constraint in (
        ("categories", "categories_owner_id_parent_key_name_key"),
        ("requests", "requests_owner_id_key_key"),
        ("import_batches", "import_batches_owner_id_source_sha256_key"),
        ("loan_periods", "loan_periods_owner_id_period_index_key"),
        ("loan_import_batches", "loan_import_batches_owner_id_source_sha256_key"),
    ):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))

    op.add_column("drafts", sa.Column("id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE drafts SET id = numbered.id FROM (SELECT ctid, row_number() OVER () AS id FROM drafts) AS numbered WHERE drafts.ctid = numbered.ctid"))
    op.execute(sa.text("ALTER TABLE drafts ALTER COLUMN id SET NOT NULL"))
    op.execute(sa.text("ALTER TABLE drafts DROP CONSTRAINT drafts_pkey"))
    op.execute(sa.text("ALTER TABLE drafts ADD PRIMARY KEY (id)"))

    for table in (
        "audit",
        "categories",
        "documents",
        "expenses",
        "plans",
        "requests",
        "drafts",
        "import_batches",
        "loan_periods",
        "loan_import_batches",
    ):
        op.drop_column(table, "owner_id")

    op.create_unique_constraint("categories_parent_key_name_key", "categories", ["parent_key", "name"])
    op.create_unique_constraint("requests_key_key", "requests", ["key"])
    op.create_unique_constraint("import_batches_source_sha256_key", "import_batches", ["source_sha256"])
    op.create_unique_constraint("loan_periods_period_index_key", "loan_periods", ["period_index"])
    op.create_unique_constraint(
        "loan_import_batches_source_sha256_key", "loan_import_batches", ["source_sha256"]
    )
    op.create_index("expense_paid_date", "expenses", ["paid_on"])
    op.create_index("loan_payment_date", "loan_periods", ["payment_date"])


def downgrade():
    raise RuntimeError("Restoring Telegram-linked ownership requires a database backup.")
