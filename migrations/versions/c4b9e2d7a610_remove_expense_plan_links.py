"""remove expense to plan links"""

import sqlalchemy as sa
from alembic import op

revision = "c4b9e2d7a610"
down_revision = "8f6c2d4a1b90"
branch_labels = None
depends_on = None

LEGACY_LINK_KEYS = {
    "plan_id",
    "paid",
    "remaining",
    "overpaid",
    "linked_expenses_preserved",
    "unlinked_expenses",
}


def without_legacy_links(value):
    if isinstance(value, dict):
        return {
            key: without_legacy_links(item)
            for key, item in value.items()
            if key not in LEGACY_LINK_KEYS
        }
    if isinstance(value, list):
        return [without_legacy_links(item) for item in value]
    return value


def scrub_json_history():
    bind = op.get_bind()
    tables = (
        (
            sa.table("audit", sa.column("id"), sa.column("before"), sa.column("after")),
            ("before", "after"),
        ),
        (sa.table("requests", sa.column("id"), sa.column("response")), ("response",)),
        (sa.table("drafts", sa.column("owner_id"), sa.column("data")), ("data",)),
    )
    for table, columns in tables:
        primary_key = list(table.c)[0]
        for row in bind.execute(sa.select(table)).mappings():
            changes = {}
            for column in columns:
                cleaned = without_legacy_links(row[column])
                if cleaned != row[column]:
                    changes[column] = cleaned
            if changes:
                bind.execute(
                    sa.update(table).where(primary_key == row[primary_key.name]).values(**changes)
                )


def upgrade():
    scrub_json_history()
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "expenses", table_args=(sa.CheckConstraint("amount_minor > 0"),)
        ) as batch:
            batch.drop_index("ix_expenses_plan_id")
            batch.drop_column("plan_id")
    else:
        op.drop_index("ix_expenses_plan_id", table_name="expenses")
        op.drop_column("expenses", "plan_id")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "expenses", table_args=(sa.CheckConstraint("amount_minor > 0"),)
        ) as batch:
            batch.add_column(sa.Column("plan_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key("expenses_plan_id_fkey", "plans", ["plan_id"], ["id"])
            batch.create_index("ix_expenses_plan_id", ["plan_id"], unique=False)
    else:
        op.add_column("expenses", sa.Column("plan_id", sa.String(length=36), nullable=True))
        op.create_foreign_key(
            "expenses_plan_id_fkey",
            "expenses",
            "plans",
            ["plan_id"],
            ["id"],
        )
        op.create_index("ix_expenses_plan_id", "expenses", ["plan_id"], unique=False)
