"""split combined utility and capital repair plans"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "b2e7c4a8d915"
down_revision = "f6d1a9c3b702"
branch_labels = None
depends_on = None

COMBINED_MINOR = 34_000 * 100
UTILITY_MINOR = 33_000 * 100
CAPITAL_REPAIR_MINOR = 4_000 * 100

CATEGORIES = sa.table(
    "categories",
    sa.column("id", sa.String(length=36)),
    sa.column("owner_id", sa.BigInteger()),
    sa.column("name", sa.String(length=120)),
    sa.column("parent_id", sa.String(length=36)),
)
PLANS = sa.table(
    "plans",
    sa.column("id", sa.String(length=36)),
    sa.column("owner_id", sa.BigInteger()),
    sa.column("title", sa.String(length=500)),
    sa.column("category_id", sa.String(length=36)),
    sa.column("expected_minor", sa.BigInteger()),
    sa.column("agreed_minor", sa.BigInteger()),
    sa.column("due_month", sa.Date()),
    sa.column("note", sa.String(length=4000)),
    sa.column("archived", sa.Boolean()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def split_combined_plans(bind) -> None:
    utility_categories = bind.execute(
        sa.select(CATEGORIES).where(CATEGORIES.c.name == "Коммуналка от УК")
    ).mappings()
    for utility in utility_categories:
        capital_category_id = bind.scalar(
            sa.select(CATEGORIES.c.id).where(
                CATEGORIES.c.owner_id == utility["owner_id"],
                CATEGORIES.c.parent_id == utility["parent_id"],
                CATEGORIES.c.name.in_(("Кап ремонт", "Капремонт")),
            )
        )
        combined = list(
            bind.execute(
                sa.select(PLANS).where(
                    PLANS.c.owner_id == utility["owner_id"],
                    PLANS.c.category_id == utility["id"],
                    PLANS.c.expected_minor == COMBINED_MINOR,
                )
            ).mappings()
        )
        if combined and capital_category_id is None:
            raise RuntimeError("Не найдена категория «Кап ремонт» для разделения планов.")
        for item in combined:
            note = item["note"] or ""
            bind.execute(
                sa.update(PLANS)
                .where(PLANS.c.id == item["id"])
                .values(
                    title="Коммуналка",
                    expected_minor=UTILITY_MINOR,
                    agreed_minor=None,
                    note=note,
                    version=item["version"] + 1,
                )
            )
            capital = (
                bind.execute(
                    sa.select(PLANS).where(
                        PLANS.c.owner_id == item["owner_id"],
                        PLANS.c.category_id == capital_category_id,
                        PLANS.c.due_month == item["due_month"],
                        PLANS.c.archived == item["archived"],
                    )
                )
                .mappings()
                .first()
            )
            values = {
                "title": "Капремонт",
                "expected_minor": CAPITAL_REPAIR_MINOR,
                "agreed_minor": None,
                "note": note,
            }
            if capital:
                bind.execute(
                    sa.update(PLANS)
                    .where(PLANS.c.id == capital["id"])
                    .values(**values, version=capital["version"] + 1)
                )
            else:
                bind.execute(
                    sa.insert(PLANS).values(
                        id=str(uuid4()),
                        owner_id=item["owner_id"],
                        category_id=capital_category_id,
                        due_month=item["due_month"],
                        archived=item["archived"],
                        version=1,
                        created_at=item["created_at"] or datetime.now(UTC),
                        **values,
                    )
                )


def upgrade():
    split_combined_plans(op.get_bind())


def downgrade():
    # The original combined rows cannot be identified safely after later edits.
    pass
