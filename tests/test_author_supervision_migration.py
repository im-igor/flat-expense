import importlib.util
from pathlib import Path

import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "f6d1a9c3b702_remove_author_supervision_items.py"
)
SPEC = importlib.util.spec_from_file_location("remove_author_supervision_items", MIGRATION_PATH)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_remove_author_supervision_items_deletes_expenses_and_plans_only():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as bind:
        bind.exec_driver_sql("CREATE TABLE categories (id VARCHAR(36), name VARCHAR(120))")
        bind.exec_driver_sql("CREATE TABLE expenses (id VARCHAR(36), category_id VARCHAR(36))")
        bind.exec_driver_sql("CREATE TABLE plans (id VARCHAR(36), category_id VARCHAR(36))")
        bind.execute(
            migration.CATEGORIES.insert(),
            [
                {"id": "author", "name": "Авторский надзор"},
                {"id": "works", "name": "Работы"},
            ],
        )
        bind.execute(
            migration.EXPENSES.insert(),
            [
                {"id": "author-expense", "category_id": "author"},
                {"id": "works-expense", "category_id": "works"},
            ],
        )
        bind.execute(
            migration.PLANS.insert(),
            [
                {"id": "author-plan", "category_id": "author"},
                {"id": "works-plan", "category_id": "works"},
            ],
        )

        migration.remove_author_supervision_items(bind)

        assert bind.execute(sa.select(migration.EXPENSES.c.id)).scalars().all() == ["works-expense"]
        assert bind.execute(sa.select(migration.PLANS.c.id)).scalars().all() == ["works-plan"]
        assert set(bind.execute(sa.select(migration.CATEGORIES.c.id)).scalars()) == {
            "author",
            "works",
        }
