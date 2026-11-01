"""add an independent planned amount to expenses"""

import sqlalchemy as sa
from alembic import op

revision = "d7e3a9b4c621"
down_revision = "b2e7c4a8d915"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("planned_amount_minor", sa.BigInteger(), nullable=True))
    op.execute(sa.text("UPDATE expenses SET planned_amount_minor = amount_minor"))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "expenses",
            table_args=(
                sa.CheckConstraint("amount_minor > 0"),
                sa.CheckConstraint(
                    "planned_amount_minor > 0",
                    name="ck_expenses_planned_amount_positive",
                ),
            ),
        ) as batch:
            batch.alter_column(
                "planned_amount_minor", existing_type=sa.BigInteger(), nullable=False
            )
    else:
        op.alter_column(
            "expenses", "planned_amount_minor", existing_type=sa.BigInteger(), nullable=False
        )
        op.create_check_constraint(
            "ck_expenses_planned_amount_positive",
            "expenses",
            "planned_amount_minor > 0",
        )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "expenses", table_args=(sa.CheckConstraint("amount_minor > 0"),)
        ) as batch:
            batch.drop_column("planned_amount_minor")
    else:
        op.drop_constraint("ck_expenses_planned_amount_positive", "expenses", type_="check")
        op.drop_column("expenses", "planned_amount_minor")
