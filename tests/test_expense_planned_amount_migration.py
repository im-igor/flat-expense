from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_migration_backfills_existing_expense_plan(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    command.upgrade(config, "b2e7c4a8d915")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO expenses "
            "(id, owner_id, paid_on, amount_minor, category_id, description, note, "
            "deleted, version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "expense",
                1,
                "2026-10-21",
                11_856_500,
                "category",
                "Материалы",
                "",
                False,
                1,
                datetime.now(UTC),
            ),
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        row = connection.exec_driver_sql(
            "SELECT amount_minor, planned_amount_minor FROM expenses WHERE id = 'expense'"
        ).one()
        assert row == (11_856_500, 11_856_500)
        columns = {
            column[1]: column
            for column in connection.exec_driver_sql("PRAGMA table_info(expenses)")
        }
        assert columns["planned_amount_minor"][3] == 1
        plan_columns = {
            column[1]: column for column in connection.exec_driver_sql("PRAGMA table_info(plans)")
        }
        assert {"paid_amount_minor", "paid_on", "payment_status"} <= set(plan_columns)
        assert plan_columns["paid_amount_minor"][3] == 1
    engine.dispose()
