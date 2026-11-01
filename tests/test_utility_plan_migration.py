import importlib.util
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "b2e7c4a8d915_split_utility_and_capital_repair_plans.py"
)
SPEC = importlib.util.spec_from_file_location("split_utility_plans", MIGRATION_PATH)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_split_combined_utility_plans_is_scoped_and_idempotent():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as bind:
        bind.exec_driver_sql(
            "CREATE TABLE categories (id VARCHAR(36), owner_id BIGINT, name VARCHAR(120), parent_id VARCHAR(36))"
        )
        bind.exec_driver_sql(
            "CREATE TABLE plans (id VARCHAR(36), owner_id BIGINT, title VARCHAR(500), "
            "category_id VARCHAR(36), expected_minor BIGINT, agreed_minor BIGINT, "
            "due_month DATE, note VARCHAR(4000), archived BOOLEAN, version INTEGER, "
            "created_at DATETIME)"
        )
        bind.execute(
            migration.CATEGORIES.insert(),
            [
                {"id": "root", "owner_id": 1, "name": "Коммуналка", "parent_id": None},
                {
                    "id": "utility",
                    "owner_id": 1,
                    "name": "Коммуналка от УК",
                    "parent_id": "root",
                },
                {
                    "id": "capital",
                    "owner_id": 1,
                    "name": "Кап ремонт",
                    "parent_id": "root",
                },
            ],
        )
        common = {
            "owner_id": 1,
            "category_id": "utility",
            "agreed_minor": None,
            "due_month": date(2026, 11, 1),
            "note": "Исходный финансовый план",
            "archived": False,
            "version": 1,
            "created_at": datetime(2026, 10, 1),
        }
        bind.execute(
            migration.PLANS.insert(),
            [
                {
                    **common,
                    "id": "combined",
                    "title": "Коммуналка + капремонт",
                    "expected_minor": 3_400_000,
                },
                {
                    **common,
                    "id": "other",
                    "title": "Другой план УК",
                    "expected_minor": 3_500_000,
                },
            ],
        )

        migration.split_combined_plans(bind)
        migration.split_combined_plans(bind)

        rows = list(bind.execute(sa.select(migration.PLANS)).mappings())
        assert len(rows) == 3
        utility = next(row for row in rows if row["id"] == "combined")
        assert (utility["title"], utility["expected_minor"], utility["version"]) == (
            "Коммуналка",
            3_300_000,
            2,
        )
        capital = next(row for row in rows if row["category_id"] == "capital")
        assert (capital["title"], capital["expected_minor"], capital["due_month"]) == (
            "Капремонт",
            400_000,
            date(2026, 11, 1),
        )
        other = next(row for row in rows if row["id"] == "other")
        assert other["expected_minor"] == 3_500_000
