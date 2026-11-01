"""remove author supervision items"""

import sqlalchemy as sa
from alembic import op

revision = "f6d1a9c3b702"
down_revision = "c4b9e2d7a610"
branch_labels = None
depends_on = None

CATEGORIES = sa.table(
    "categories",
    sa.column("id", sa.String(length=36)),
    sa.column("name", sa.String(length=120)),
)
EXPENSES = sa.table(
    "expenses",
    sa.column("id", sa.String(length=36)),
    sa.column("category_id", sa.String(length=36)),
)
PLANS = sa.table(
    "plans",
    sa.column("id", sa.String(length=36)),
    sa.column("category_id", sa.String(length=36)),
)


def remove_author_supervision_items(bind) -> None:
    category_ids = sa.select(CATEGORIES.c.id).where(CATEGORIES.c.name == "Авторский надзор")
    bind.execute(sa.delete(EXPENSES).where(EXPENSES.c.category_id.in_(category_ids)))
    bind.execute(sa.delete(PLANS).where(PLANS.c.category_id.in_(category_ids)))


def upgrade():
    remove_author_supervision_items(op.get_bind())


def downgrade():
    # Deleted financial records cannot be reconstructed safely.
    pass
