"""Read-only XLSX migration. Original actual rows, never summary/formula rows."""

import hashlib
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select

from flat_expense import service as s
from flat_expense.models import Category, Expense, ImportBatch, LoanImportBatch, LoanPeriod
from flat_expense.schemas import ExpenseIn, PlanIn

ALIASES = {
    "Мтериалы": "Черновые материалы",
    "Материалы": "Черновые материалы",
    "Капремонт": "Кап ремонт",
}


def category(db, owner, first, second):
    first, second = str(first or "").strip(), str(second or "").strip()
    if not first:
        raise s.DomainError("У расхода не указана категория 1. Исправьте исходный файл.")
    root = s.ensure_category(db, owner, ALIASES.get(first, first))
    return s.ensure_category(db, owner, ALIASES.get(second, second), root.id) if second else root


def source_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise s.DomainError("Дата в исходном файле не распознана. Нужна настоящая дата Excel.")


def plan_categories(code, group):
    known = {
        "H-DEAL": ("Сопровождение сделки", ""),
        "H-DESIGN": ("Ремонт", "Дизайн"),
        "H-UTILITY": ("Коммуналка", "Коммуналка от УК"),
        "H-CAP": ("Коммуналка", "Кап ремонт"),
        "H-REPLAN": ("Ремонт", "Перепланировка"),
        "H-CLIMATE": ("Ремонт", "Климат"),
        "H-ACCEPT": ("Ремонт", "Приемка"),
    }
    if code in known:
        return known[code]
    if code.startswith("UTIL-"):
        return "Коммуналка", "Коммуналка от УК"
    if code.startswith("SUP-"):
        return "Ремонт", "Авторский надзор"
    if code.startswith("INSURANCE"):
        return "Сопровождение сделки", "Страховка"
    return "Ремонт", group


def import_xlsx(db, owner: int, path: Path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = db.scalar(
        select(ImportBatch).where(
            ImportBatch.owner_id == owner, ImportBatch.source_sha256 == digest
        )
    )
    if previous:
        return {**previous.summary, "already_imported": True}
    # This is an initial migration, not ongoing reconciliation of edited workbooks.
    if db.scalar(select(ImportBatch.id).where(ImportBatch.owner_id == owner).limit(1)):
        raise s.DomainError(
            "Начальный импорт уже выполнен из другого файла. Повторный импорт новой версии требует сверки, чтобы не задвоить расходы."
        )
    from flat_expense.models import Expense, Plan

    if db.scalar(select(Expense.id).where(Expense.owner_id == owner).limit(1)) or db.scalar(
        select(Plan.id).where(Plan.owner_id == owner).limit(1)
    ):
        raise s.DomainError(
            "Начальный импорт разрешён только в пустой учёт. Сначала проверьте текущие записи."
        )
    book = load_workbook(path, read_only=True, data_only=True)
    plans, source_paid = {}, {}
    try:
        unified = "Архив плана" in book.sheetnames and "Архив связей расходов" in book.sheetnames
        if unified:
            for row in book["Архив плана"].iter_rows(min_row=2, max_col=9, values_only=True):
                code, group, title, total, paid, remaining, month, status, note = row
                if code is None:
                    continue
                if code in plans:
                    raise s.DomainError("В исходном плане повторяется код позиции.")
                c = category(db, owner, *plan_categories(code, group))
                cost = s.money(s.minor(total))
                closed = status == "Оплачено"
                plan = s.add_plan(
                    db,
                    owner,
                    PlanIn(
                        title=title,
                        category_id=c.id,
                        expected=None if closed else cost,
                        agreed=cost if closed else None,
                        due_month=source_date(month).replace(day=1) if month is not None else None,
                        note=note or "",
                    ),
                )
                plans[code], source_paid[code] = plan["id"], s.minor(paid) or 0
            sheet = book["Архив связей расходов"]
        elif "Расходы" in book.sheetnames:
            sheet = book["Расходы"]
        else:
            raise s.DomainError("Нет исходного листа «Расходы» или архивов подготовленной книги.")
        headers = [sheet.cell(1, c).value for c in range(1, 7)]
        if headers[:5] != ["Дата", "Сумма", "Категория1", "Категория2", "Описание расхода"]:
            raise s.DomainError("Заголовки исходного журнала не совпадают с ожидаемой структурой.")
        count, amount_total = 0, 0
        for row in sheet.iter_rows(min_row=2, max_col=8, values_only=True):
            paid_on, amount, first, second, description, note, bot_id, code = row
            if all(v is None for v in row[:6]):
                continue
            if not description or amount is None:
                raise s.DomainError("В исходном журнале есть неполная строка; импорт отменён.")
            c = category(db, owner, first, second)
            if unified and code not in plans:
                raise s.DomainError("Оплата ссылается на отсутствующую позицию плана.")
            amount_minor = s.minor(amount)
            result = s.add_expense(
                db,
                owner,
                ExpenseIn(
                    paid_on=source_date(paid_on),
                    amount=s.money(amount_minor),
                    category_id=c.id,
                    description=str(description),
                    note=str(note or ""),
                    plan_id=plans.get(code),
                ),
            )
            s.audit(
                db,
                owner,
                "expense",
                result["id"],
                "import_source",
                None,
                {
                    "row": count + 2,
                    "sha256": digest,
                    "original_category_1": first,
                    "original_category_2": second,
                    "original_amount": str(amount),
                    "bot_id": str(bot_id) if bot_id else None,
                },
            )
            count += 1
            amount_total += amount_minor
        if unified:
            paid = s.paid_totals(db, owner)
            for code, identifier in plans.items():
                if paid.get(identifier, 0) != source_paid[code]:
                    raise s.DomainError(
                        "Оплаты по позиции не совпали с контрольной суммой исходного плана. Импорт отменён."
                    )
        summary = {
            "expenses": count,
            "actual_total": s.money(amount_total),
            "plans": len(plans),
            "loan_imported": False,
            "normalized_category_aliases": ALIASES,
            "already_imported": False,
        }
        db.add(ImportBatch(owner_id=owner, source_sha256=digest, summary=summary))
        db.flush()
        return summary
    finally:
        book.close()


LOAN_HEADERS = [
    "Месяц пользования займом",
    "Дней в году",
    "Дней в месяце",
    "№",
    "Дата платежа",
    "Ставка ЦБ на дату",
    "Остаток на начало",
    "Начислено процентов",
    "Погашение основного долга",
    "Всего платеж",
    "Остаток на конец",
    "Мат выгода",
    "Налог на мат выгоду",
    "Итого",
]


def import_loan_xlsx(db, owner: int, path: Path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = db.scalar(
        select(LoanImportBatch).where(
            LoanImportBatch.owner_id == owner, LoanImportBatch.source_sha256 == digest
        )
    )
    if previous:
        return {**previous.summary, "already_imported": True}
    if db.scalar(select(LoanPeriod.id).where(LoanPeriod.owner_id == owner).limit(1)):
        raise s.DomainError(
            "График займа уже загружен. Новую версию нужно сверить отдельно, чтобы не задвоить периоды."
        )
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Финплан по потокам" not in book.sheetnames:
            raise s.DomainError("Нет листа «Финплан по потокам».")
        sheet = book["Финплан по потокам"]
        headers = [sheet.cell(3, column).value for column in range(1, 15)]
        if headers != LOAN_HEADERS:
            raise s.DomainError("Столбцы A–N графика займа не совпали с ожидаемой структурой.")
        periods: list[LoanPeriod] = []
        for row_number, row in enumerate(
            sheet.iter_rows(min_row=4, min_col=1, max_col=11, values_only=True), start=4
        ):
            usage_month, _, _, index, payment_date, rate, *amounts = row
            if all(value is None for value in row):
                continue
            if (
                usage_month is None
                or index is None
                or payment_date is None
                or any(value is None for value in amounts)
            ):
                raise s.DomainError(f"В строке {row_number} графика займа не хватает данных.")
            start, interest, principal, payment, end = amounts
            try:
                parsed_rate = (Decimal(str(rate)) * 100).quantize(Decimal("0.0001"))
            except (InvalidOperation, ValueError):
                parsed_rate = None
            item = LoanPeriod(
                owner_id=owner,
                usage_month=source_date(usage_month).replace(day=1),
                period_index=int(index),
                payment_date=source_date(payment_date),
                key_rate=parsed_rate,
                rate_kind="imported" if parsed_rate is not None else "missing",
                start_balance_minor=s.minor(start),
                interest_minor=s.minor(interest),
                principal_minor=s.minor(principal),
                payment_minor=s.minor(payment),
                end_balance_minor=s.minor(end),
            )
            periods.append(item)
            db.add(item)
        if not periods or [item.period_index for item in periods] != list(range(len(periods))):
            raise s.DomainError("Нумерация графика займа должна идти подряд с 0.")
        summary = {
            "periods": len(periods),
            "first_month": periods[0].usage_month.isoformat(),
            "last_month": periods[-1].usage_month.isoformat(),
            "initial_balance": s.money(periods[0].start_balance_minor),
            "final_balance": s.money(periods[-1].end_balance_minor),
            "already_imported": False,
        }
        db.add(LoanImportBatch(owner_id=owner, source_sha256=digest, summary=summary))
        s.audit(db, owner, "loan", str(owner), "import", None, summary)
        db.flush()
        return summary
    finally:
        book.close()


def verify_expenses_xlsx(db, owner: int, path: Path) -> dict:
    """Compare every source expense field with the current database, including duplicates."""

    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Архив связей расходов" in book.sheetnames:
            sheet = book["Архив связей расходов"]
        elif "Расходы" in book.sheetnames:
            sheet = book["Расходы"]
        else:
            raise s.DomainError("Нет исходного журнала расходов.")
        headers = [sheet.cell(1, column).value for column in range(1, 6)]
        if headers != ["Дата", "Сумма", "Категория1", "Категория2", "Описание расхода"]:
            raise s.DomainError("Заголовки исходного журнала расходов не совпали.")
        expected = Counter()
        for row in sheet.iter_rows(min_row=2, max_col=6, values_only=True):
            paid_on, amount, first, second, description, note = row
            if all(value is None for value in row):
                continue
            root = ALIASES.get(str(first or "").strip(), str(first or "").strip())
            child = ALIASES.get(str(second or "").strip(), str(second or "").strip())
            path_name = f"{root} → {child}" if child else root
            expected[
                (
                    source_date(paid_on).isoformat(),
                    s.minor(amount),
                    path_name,
                    str(description),
                    str(note or ""),
                )
            ] += 1
        categories = {
            item.id: item for item in db.scalars(select(Category).where(Category.owner_id == owner))
        }
        actual = Counter()
        for item in db.scalars(select(Expense).where(Expense.owner_id == owner)):
            category_item = categories[item.category_id]
            parent = categories.get(category_item.parent_id)
            path_name = f"{parent.name} → {category_item.name}" if parent else category_item.name
            actual[
                (
                    item.paid_on.isoformat(),
                    item.amount_minor,
                    path_name,
                    item.description,
                    item.note,
                )
            ] += 1
        missing = expected - actual
        unexpected = actual - expected
        expected_total = sum(row[1] * count for row, count in expected.items())
        actual_total = sum(row[1] * count for row, count in actual.items())
        return {
            "source_rows": sum(expected.values()),
            "database_rows": sum(actual.values()),
            "source_total": s.money(expected_total),
            "database_total": s.money(actual_total),
            "exact_match": not missing and not unexpected,
            "missing_rows": sum(missing.values()),
            "unexpected_rows": sum(unexpected.values()),
        }
    finally:
        book.close()
