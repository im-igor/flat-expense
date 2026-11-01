from datetime import datetime

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from flat_expense import service as s
from flat_expense.importer import import_xlsx, remove_historical_plan_cards, verify_expenses_xlsx
from flat_expense.models import Expense, Plan
from flat_expense.schemas import ExpenseIn, PlanIn


def workbook(path, broken=False, closed=False, code="MAT-TEST", total=10000):
    w = Workbook()
    e = w.active
    e.title = "Архив связей расходов"
    e.append(
        [
            "Дата",
            "Сумма",
            "Категория1",
            "Категория2",
            "Описание расхода",
            "Примечания",
            "Bot ID",
            "Код позиции плана",
        ]
    )
    e.append([datetime(2026, 10, 31), "3 731,15", "Ремонт", "Мтериалы", "Аванс", None, None, code])
    p = w.create_sheet("Архив плана")
    p.append(
        [
            "Код",
            "Группа",
            "Работа",
            "Полная сумма",
            "Оплачено",
            "Осталось",
            "Месяц",
            "Состояние",
            "Примечание",
        ]
    )
    p.append(
        [
            code,
            "Дизайн-проект и визуализация: оплачено" if closed else "Материалы",
            "Материалы",
            total,
            100 if broken else 3731.15,
            6268.85,
            datetime(2026, 11, 1),
            "Оплачено" if closed else "План",
            "",
        ]
    )
    w.save(path)


def test_import_numeric_strings_aliases_and_idempotence(sessions, tmp_path):
    path = tmp_path / "input.xlsx"
    workbook(path)
    with sessions() as db, db.begin():
        result = import_xlsx(db, 111, path)
        assert (
            result["expenses"] == 1 and result["plans"] == 1 and result["actual_total"] == "3731.15"
        )
        plan = s.list_plans(db, 111)[0]
        assert plan["expected"] == "10000.00"
        assert "Черновые материалы" in s.list_expenses(db, 111)[0]["category"]
    with sessions() as db, db.begin():
        assert import_xlsx(db, 111, path)["already_imported"]
        assert db.scalar(select(func.count()).select_from(Expense)) == 1


def test_bad_reconciliation_rolls_back_entire_import(sessions, tmp_path):
    path = tmp_path / "bad.xlsx"
    workbook(path, broken=True)
    with pytest.raises(s.DomainError), sessions() as db, db.begin():
        import_xlsx(db, 111, path)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Expense)) == 0


def test_import_skips_paid_aggregate_cards_but_keeps_expenses(sessions, tmp_path):
    path = tmp_path / "closed.xlsx"
    workbook(path, closed=True)
    with sessions() as db, db.begin():
        result = import_xlsx(db, 111, path)
        expense = db.scalar(select(Expense))
        assert result["plans"] == 0
        assert result["skipped_historical_plans"] == 1
        assert expense is not None
        assert db.scalar(select(func.count()).select_from(Plan)) == 0


def test_import_skips_unpriced_historical_card_but_keeps_expense(sessions, tmp_path):
    path = tmp_path / "unpriced.xlsx"
    workbook(path, code="MAT-WALLS", total=None)
    with sessions() as db, db.begin():
        result = import_xlsx(db, 111, path)
        expense = db.scalar(select(Expense))
        assert result["plans"] == 0
        assert result["skipped_historical_plans"] == 1
        assert expense is not None
        assert db.scalar(select(func.count()).select_from(Plan)) == 0


def test_remove_historical_cards_keeps_expenses(sessions):
    with sessions() as db, db.begin():
        category = s.ensure_category(db, 111, "Ремонт")
        plan = s.add_plan(
            db,
            111,
            PlanIn(
                title="Дизайн-проект и визуализация: оплачено",
                category_id=category.id,
                agreed="100.00",
            ),
        )
        expense = s.add_expense(
            db,
            111,
            ExpenseIn(
                paid_on="2026-01-01",
                amount="100.00",
                category_id=category.id,
                description="Оплата дизайна",
            ),
        )
        result = remove_historical_plan_cards(db, 111)
        assert result["removed_plans"] == 1
        assert db.get(Plan, plan["id"]) is None
        assert db.get(Expense, expense["id"]) is not None


def test_remove_historical_cards_keeps_facts_and_real_future_plan(sessions):
    with sessions() as db, db.begin():
        category = s.ensure_category(db, 111, "Ремонт")
        historical = s.add_plan(
            db,
            111,
            PlanIn(title="Материалы для перегородок", category_id=category.id),
        )
        future = s.add_plan(
            db,
            111,
            PlanIn(
                title="Материалы к этапу 3: штукатурка",
                category_id=category.id,
                expected="382231.20",
            ),
        )
        first = s.add_expense(
            db,
            111,
            ExpenseIn(
                paid_on="2026-10-05",
                amount="140626.00",
                category_id=category.id,
                description="Материалы для перегородок",
            ),
        )
        second = s.add_expense(
            db,
            111,
            ExpenseIn(
                paid_on="2026-10-21",
                amount="118565.00",
                category_id=category.id,
                description="Материалы для штукатурки",
            ),
        )
        s.audit(
            db,
            111,
            "expense",
            second["id"],
            "import_source",
            None,
            {"row": 2},
        )
        result = remove_historical_plan_cards(db, 111)
        assert result["removed_plans"] == 1
        assert db.get(Plan, historical["id"]) is None
        assert db.get(Plan, future["id"]) is not None
        assert db.get(Expense, first["id"]) is not None
        assert db.get(Expense, second["id"]) is not None


def test_dry_run_does_not_store_data(sessions, tmp_path):
    path = tmp_path / "input.xlsx"
    workbook(path)
    with sessions() as db:
        import_xlsx(db, 111, path)
        db.rollback()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Expense)) == 0


def test_verify_compares_every_expense_field(sessions, tmp_path):
    path = tmp_path / "input.xlsx"
    workbook(path)
    with sessions() as db, db.begin():
        import_xlsx(db, 111, path)
        result = verify_expenses_xlsx(db, 111, path)
        assert result == {
            "source_rows": 1,
            "database_rows": 1,
            "source_total": "3731.15",
            "database_total": "3731.15",
            "exact_match": True,
            "missing_rows": 0,
            "unexpected_rows": 0,
        }
        expense = db.scalar(select(Expense))
        expense.description = "Изменено"
        db.flush()
        result = verify_expenses_xlsx(db, 111, path)
        assert not result["exact_match"]
        assert result["missing_rows"] == result["unexpected_rows"] == 1
