from datetime import datetime

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from flat_expense import service as s
from flat_expense.importer import import_xlsx, verify_expenses_xlsx
from flat_expense.models import Expense


def workbook(path, broken=False):
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
    e.append(
        [datetime(2026, 10, 31), "3 731,15", "Ремонт", "Мтериалы", "Аванс", None, None, "MAT-TEST"]
    )
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
            "MAT-TEST",
            "Материалы",
            "Материалы",
            10000,
            100 if broken else 3731.15,
            6268.85,
            datetime(2026, 11, 1),
            "План",
            "",
        ]
    )
    w.save(path)


def test_import_numeric_strings_linking_aliases_and_idempotence(sessions, tmp_path):
    path = tmp_path / "input.xlsx"
    workbook(path)
    with sessions() as db, db.begin():
        result = import_xlsx(db, 111, path)
        assert (
            result["expenses"] == 1 and result["plans"] == 1 and result["actual_total"] == "3731.15"
        )
        plan = s.list_plans(db, 111)[0]
        assert plan["paid"] == "3731.15" and plan["remaining"] == "6268.85"
        assert "Материалы" in s.list_expenses(db, 111)[0]["category"]
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
