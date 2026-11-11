import hashlib
import json
import re
from calendar import isleap, monthrange
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flat_expense import cbr
from flat_expense.models import Audit, Category, Expense, LoanPeriod, Plan, RequestLog, utcnow
from flat_expense.schemas import ExpenseIn, PlanIn

MOSCOW = ZoneInfo("Europe/Moscow")


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def today() -> date:
    return datetime.now(MOSCOW).date()


def money(minor: int | None) -> str | None:
    return f"{Decimal(minor) / 100:.2f}" if minor is not None else None


def minor(value: Decimal | str | int | None) -> int | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise DomainError("Введите сумму числом, например 3 731,15.") from None
    if not number.is_finite() or number < 0 or number > Decimal("9999999999.99"):
        raise DomainError("Сумма должна быть от 0 до 9 999 999 999,99 ₽.")
    if number != number.quantize(Decimal("0.01")):
        raise DomainError("Укажите не больше двух знаков после запятой.")
    return int(number * 100)


def parse_date(text: str, month: bool = False) -> date:
    text = text.strip().lower()
    if text in ("сегодня", "today") and not month:
        return today()
    formats = ("%m.%Y", "%Y-%m") if month else ("%d.%m.%Y", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt).date()
            if not 2000 <= parsed.year <= 2100:
                break
            return parsed
        except ValueError:
            continue
    raise DomainError(
        "Введите месяц ММ.ГГГГ." if month else "Введите дату ДД.ММ.ГГГГ или «сегодня»."
    )


def month_range(start: date, end: date) -> list[date]:
    start, end = start.replace(day=1), end.replace(day=1)
    if end < start or (end.year - start.year) * 12 + end.month - start.month > 240:
        raise DomainError("Период должен быть от 1 до 241 месяца.")
    result = []
    cursor = start
    while cursor <= end:
        result.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def owned(db: Session, model, owner: int, identifier: str, lock: bool = False):
    query = select(model).where(model.id == identifier, model.owner_id == owner)
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise DomainError("Запись не найдена.", 404)
    return item


def category_view(db: Session, category: Category) -> dict:
    parent = db.get(Category, category.parent_id) if category.parent_id else None
    return {
        "id": category.id,
        "name": category.name,
        "parent_id": category.parent_id,
        "path": f"{parent.name} → {category.name}" if parent else category.name,
    }


def ensure_category(db: Session, owner: int, name: str, parent_id: str | None = None):
    name = name.strip()
    if not name or len(name) > 120:
        raise DomainError("Название категории: от 1 до 120 символов.")
    if parent_id:
        parent = owned(db, Category, owner, parent_id)
        if parent.parent_id:
            raise DomainError("Поддерживаются два уровня категорий.")
    query = select(Category).where(
        Category.owner_id == owner, Category.parent_key == (parent_id or ""), Category.name == name
    )
    category = db.scalar(query)
    if category:
        return category
    try:
        with db.begin_nested():
            category = Category(
                owner_id=owner, name=name, parent_id=parent_id, parent_key=parent_id or ""
            )
            db.add(category)
            db.flush()
    except IntegrityError:
        category = db.scalar(query)
        if category is None:
            raise
    return category


def seed_categories(db: Session, owner: int):
    groups = {
        "Ремонт": [
            "Работы",
            "Черновые материалы",
            "Дизайн",
            "Климат",
            "Перепланировка",
            "Приемка",
            "Авторский надзор",
        ],
        "Коммуналка": ["Кап ремонт", "Коммуналка от УК"],
        "Сопровождение сделки": ["Приемка", "Страховка"],
        "Покупки": ["Мебель", "Техника", "Прочее"],
    }
    for name, children in groups.items():
        parent = ensure_category(db, owner, name)
        for child in children:
            ensure_category(db, owner, child, parent.id)


def categories(db: Session, owner: int) -> list[dict]:
    items = list(
        db.scalars(select(Category).where(Category.owner_id == owner).order_by(Category.name))
    )
    expense_counts = dict(
        db.execute(
            select(Expense.category_id, func.count())
            .where(Expense.owner_id == owner)
            .group_by(Expense.category_id)
        ).all()
    )
    plan_counts = dict(
        db.execute(
            select(Plan.category_id, func.count())
            .where(Plan.owner_id == owner)
            .group_by(Plan.category_id)
        ).all()
    )
    child_counts = dict(
        db.execute(
            select(Category.parent_id, func.count())
            .where(Category.owner_id == owner, Category.parent_id.is_not(None))
            .group_by(Category.parent_id)
        ).all()
    )
    return [
        {
            **category_view(db, item),
            "expense_count": expense_counts.get(item.id, 0),
            "plan_count": plan_counts.get(item.id, 0),
            "child_count": child_counts.get(item.id, 0),
        }
        for item in items
    ]


def rename_category(db: Session, owner: int, identifier: str, name: str) -> dict:
    item = owned(db, Category, owner, identifier, lock=True)
    name = name.strip()
    if not name or len(name) > 120:
        raise DomainError("Название категории: от 1 до 120 символов.")
    before = category_view(db, item)
    if item.name == name:
        return before
    duplicate = db.scalar(
        select(Category.id).where(
            Category.owner_id == owner,
            Category.parent_key == item.parent_key,
            Category.name == name,
            Category.id != item.id,
        )
    )
    if duplicate:
        raise DomainError("В этой группе уже есть категория с таким названием.", 409)
    item.name = name
    db.flush()
    result = category_view(db, item)
    audit(db, owner, "category", item.id, "rename", before, result)
    return result


def delete_category(db: Session, owner: int, identifier: str) -> dict:
    item = owned(db, Category, owner, identifier, lock=True)
    before = category_view(db, item)
    children = db.scalar(
        select(func.count()).select_from(Category).where(Category.parent_id == item.id)
    )
    expenses = db.scalar(
        select(func.count())
        .select_from(Expense)
        .where(Expense.owner_id == owner, Expense.category_id == item.id)
    )
    plans = db.scalar(
        select(func.count())
        .select_from(Plan)
        .where(Plan.owner_id == owner, Plan.category_id == item.id)
    )
    if children:
        raise DomainError("Сначала удалите или объедините подкатегории этой группы.", 409)
    if expenses or plans:
        raise DomainError(
            "Категория используется в оплатах или планах. Сначала замените её на другую.", 409
        )
    db.delete(item)
    audit(db, owner, "category", item.id, "delete", before, {"deleted": True})
    return {"id": identifier, "deleted": True}


def replace_category(db: Session, owner: int, identifier: str, target_id: str) -> dict:
    source = owned(db, Category, owner, identifier, lock=True)
    target = owned(db, Category, owner, target_id, lock=True)
    if source.id == target.id:
        raise DomainError("Выберите другую категорию для замены.")
    children = db.scalar(
        select(func.count()).select_from(Category).where(Category.parent_id == source.id)
    )
    if children:
        raise DomainError("Сначала замените или удалите подкатегории этой группы.", 409)
    before = category_view(db, source)
    expense_count = db.execute(
        update(Expense)
        .where(Expense.owner_id == owner, Expense.category_id == source.id)
        .values(category_id=target.id, version=Expense.version + 1)
    ).rowcount
    plan_count = db.execute(
        update(Plan)
        .where(Plan.owner_id == owner, Plan.category_id == source.id)
        .values(category_id=target.id, version=Plan.version + 1)
    ).rowcount
    result = {
        "source": before,
        "target": category_view(db, target),
        "expenses": expense_count,
        "plans": plan_count,
    }
    db.delete(source)
    audit(db, owner, "category", source.id, "replace", before, result)
    return result


def expense_view(db: Session, item: Expense) -> dict:
    category = owned(db, Category, item.owner_id, item.category_id)
    return {
        "id": item.id,
        "paid_on": item.paid_on.isoformat(),
        "amount": money(item.amount_minor),
        "planned_amount": money(item.planned_amount_minor),
        "category_id": item.category_id,
        "category": category_view(db, category)["path"],
        "description": item.description,
        "note": item.note,
        "needs_review": item.needs_review,
        "deleted": item.deleted,
        "version": item.version,
    }


def audit(db: Session, owner: int, entity: str, identifier: str, action: str, before, after):
    db.add(
        Audit(
            owner_id=owner,
            entity=entity,
            entity_id=identifier,
            action=action,
            before=before,
            after=after,
        )
    )


def plan_view(db: Session, item: Plan) -> dict:
    category = owned(db, Category, item.owner_id, item.category_id)
    planned_minor = plan_amount_minor(item)
    return {
        "id": item.id,
        "title": item.title,
        "category_id": item.category_id,
        "category": category_view(db, category)["path"],
        "expected": money(item.expected_minor),
        "agreed": money(item.agreed_minor),
        "due_month": item.due_month.isoformat() if item.due_month else None,
        "planned_amount": money(planned_minor),
        "paid_amount": money(item.paid_amount_minor),
        "paid_on": item.paid_on.isoformat() if item.paid_on else None,
        "remaining_amount": money(plan_remaining_minor(item)),
        "payment_status": item.payment_status,
        "note": item.note,
        "archived": item.archived,
        "version": item.version,
    }


def plan_amount_minor(item: Plan) -> int | None:
    return item.agreed_minor if item.agreed_minor is not None else item.expected_minor


def plan_remaining_minor(item: Plan) -> int | None:
    if item.payment_status in ("paid", "finalized"):
        return 0
    planned = plan_amount_minor(item)
    return max(0, planned - item.paid_amount_minor) if planned is not None else None


def validate_expense(db: Session, owner: int, data: ExpenseIn):
    owned(db, Category, owner, data.category_id)


def add_expense(db: Session, owner: int, data: ExpenseIn, needs_review: bool = False) -> dict:
    validate_expense(db, owner, data)
    item = Expense(
        owner_id=owner,
        paid_on=data.paid_on,
        amount_minor=minor(data.amount),
        planned_amount_minor=minor(data.planned_amount or data.amount),
        category_id=data.category_id,
        description=data.description,
        note=data.note,
        needs_review=needs_review,
    )
    db.add(item)
    db.flush()
    result = expense_view(db, item)
    audit(db, owner, "expense", item.id, "create", None, result)
    return result


def update_expense(db: Session, owner: int, identifier: str, data: ExpenseIn, version: int):
    item = owned(db, Expense, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("Запись уже изменена. Откройте её заново.", 409)
    if item.deleted:
        raise DomainError("Сначала восстановите удалённую запись.", 409)
    validate_expense(db, owner, data)
    before = expense_view(db, item)
    for name in ("paid_on", "category_id", "description", "note"):
        setattr(item, name, getattr(data, name))
    item.amount_minor = minor(data.amount)
    if data.planned_amount is not None:
        item.planned_amount_minor = minor(data.planned_amount)
    item.version += 1
    db.flush()
    result = expense_view(db, item)
    audit(db, owner, "expense", item.id, "update", before, result)
    return result


def toggle_expense(db: Session, owner: int, identifier: str, version: int, deleted: bool):
    item = owned(db, Expense, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("Запись уже изменена. Откройте её заново.", 409)
    before = expense_view(db, item)
    item.deleted = deleted
    item.version += 1
    db.flush()
    result = expense_view(db, item)
    audit(db, owner, "expense", item.id, "delete" if deleted else "restore", before, result)
    return result


def resolve_expense_review(db: Session, owner: int, identifier: str, version: int):
    item = owned(db, Expense, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("Запись уже изменена. Откройте её заново.", 409)
    if item.deleted:
        raise DomainError("Сначала восстановите удалённую запись.", 409)
    before = expense_view(db, item)
    item.needs_review = False
    item.version += 1
    db.flush()
    result = expense_view(db, item)
    audit(db, owner, "expense", item.id, "resolve_review", before, result)
    return result


def add_plan(db: Session, owner: int, data: PlanIn) -> dict:
    owned(db, Category, owner, data.category_id)
    item = Plan(
        owner_id=owner,
        title=data.title,
        category_id=data.category_id,
        expected_minor=minor(data.expected),
        agreed_minor=minor(data.agreed),
        due_month=data.due_month,
        note=data.note,
    )
    db.add(item)
    db.flush()
    result = plan_view(db, item)
    audit(db, owner, "plan", item.id, "create", None, result)
    return result


def update_plan(db: Session, owner: int, identifier: str, data: PlanIn, version: int):
    item = owned(db, Plan, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("План уже изменён. Откройте его заново.", 409)
    owned(db, Category, owner, data.category_id)
    before = plan_view(db, item)
    item.title, item.category_id, item.note = data.title, data.category_id, data.note
    item.expected_minor, item.agreed_minor = minor(data.expected), minor(data.agreed)
    item.due_month = data.due_month
    item.version += 1
    db.flush()
    result = plan_view(db, item)
    audit(db, owner, "plan", item.id, "update", before, result)
    return result


def record_plan_payment(
    db: Session,
    owner: int,
    identifier: str,
    amount_minor: int,
    paid_on: date,
    version: int,
    finalize: bool = False,
):
    item = owned(db, Plan, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("План уже изменён. Откройте его заново.", 409)
    if item.archived:
        raise DomainError("Сначала восстановите план из архива.", 409)
    if item.payment_status in ("paid", "finalized"):
        raise DomainError("Этот план уже закрыт.", 409)
    if amount_minor <= 0:
        raise DomainError("Сумма оплаты должна быть больше нуля.")
    before = plan_view(db, item)
    item.paid_amount_minor += amount_minor
    item.paid_on = paid_on
    planned = plan_amount_minor(item)
    if finalize:
        item.payment_status = "finalized"
    elif planned is not None and item.paid_amount_minor >= planned:
        item.payment_status = "paid"
    else:
        item.payment_status = "partial"
    item.version += 1
    db.flush()
    result = plan_view(db, item)
    audit(db, owner, "plan", item.id, "record_payment", before, result)
    return result


def apply_expense_to_plan(
    db: Session,
    owner: int,
    expense_id: str,
    plan_id: str,
    expense_version: int,
    plan_version: int,
):
    """Consume a reviewed Telegram expense into one plan without retaining a link."""
    expense = owned(db, Expense, owner, expense_id, lock=True)
    plan = owned(db, Plan, owner, plan_id, lock=True)
    if expense.version != expense_version or plan.version != plan_version:
        raise DomainError("Запись уже изменена. Обновите список и повторите действие.", 409)
    if expense.deleted:
        raise DomainError("Эта оплата уже удалена.", 409)
    if not expense.needs_review:
        raise DomainError("Эта оплата уже разобрана.", 409)
    if plan.archived:
        raise DomainError("Сначала восстановите план из архива.", 409)
    if plan.payment_status in ("paid", "finalized"):
        raise DomainError("Этот план уже закрыт.", 409)

    merged_note = plan.note
    if expense.note:
        merged_note = f"{plan.note}\n\n{expense.note}" if plan.note else expense.note
    if len(merged_note) > 4000:
        raise DomainError("После объединения примечание плана будет длиннее 4000 символов.")

    plan_before = plan_view(db, plan)
    expense_before = expense_view(db, expense)
    plan.paid_amount_minor += expense.amount_minor
    plan.paid_on = max((value for value in (plan.paid_on, expense.paid_on) if value), default=None)
    plan.note = merged_note
    planned = plan_amount_minor(plan)
    plan.payment_status = (
        "paid" if planned is not None and plan.paid_amount_minor >= planned else "partial"
    )
    plan.version += 1
    db.flush()
    result = plan_view(db, plan)
    audit(db, owner, "plan", plan.id, "apply_expense", plan_before, result)
    audit(db, owner, "expense", expense.id, "consume", expense_before, {"deleted": True})
    db.delete(expense)
    db.flush()
    return result


def bulk_move_plan_due_month(db: Session, owner: int, identifiers: list[str], due_month: date):
    items = [owned(db, Plan, owner, identifier, lock=True) for identifier in sorted(identifiers)]
    archived = next((item for item in items if item.archived), None)
    if archived:
        raise DomainError("Сначала восстановите архивный план, чтобы изменить его срок.", 409)
    for item in items:
        before = plan_view(db, item)
        item.due_month = due_month
        item.version += 1
        db.flush()
        audit(db, owner, "plan", item.id, "bulk_move_due_month", before, plan_view(db, item))
    return {"updated": len(items), "due_month": due_month.isoformat()}


def toggle_plan(db: Session, owner: int, identifier: str, version: int, archived: bool):
    item = owned(db, Plan, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("План уже изменён. Откройте его заново.", 409)
    before = plan_view(db, item)
    item.archived = archived
    item.version += 1
    db.flush()
    result = plan_view(db, item)
    audit(db, owner, "plan", item.id, "archive" if archived else "restore", before, result)
    return result


def delete_plan(db: Session, owner: int, identifier: str, version: int) -> dict:
    item = owned(db, Plan, owner, identifier, lock=True)
    if item.version != version:
        raise DomainError("План уже изменён. Откройте его заново.", 409)
    before = plan_view(db, item)
    result = {"id": item.id, "deleted": True}
    db.delete(item)
    audit(db, owner, "plan", item.id, "delete", before, result)
    db.flush()
    return result


def list_expenses(db: Session, owner: int, offset=0, limit=10, deleted=False):
    query = (
        select(Expense)
        .where(Expense.owner_id == owner, Expense.deleted == deleted)
        .order_by(Expense.paid_on.desc(), Expense.created_at.desc(), Expense.id)
        .offset(offset)
        .limit(limit)
    )
    return [expense_view(db, e) for e in db.scalars(query)]


def list_plans(db: Session, owner: int, offset=0, limit=10, archived=False):
    query = (
        select(Plan)
        .where(Plan.owner_id == owner, Plan.archived == archived)
        .order_by(Plan.due_month.asc().nulls_last(), Plan.created_at, Plan.id)
        .offset(offset)
        .limit(limit)
    )
    return [plan_view(db, p) for p in db.scalars(query)]


def summary(db: Session, owner: int, start: date, end: date):
    months = month_range(start, end)
    actual_without_loan = {m: 0 for m in months}
    planned_without_loan = {m: 0 for m in months}
    loan_actual_by_month = {m: 0 for m in months}
    loan_planned_by_month = {m: 0 for m in months}
    missing = {m: 0 for m in months}
    for e in db.scalars(
        select(Expense).where(
            Expense.owner_id == owner, Expense.deleted.is_(False), Expense.paid_on >= months[0]
        )
    ):
        m = e.paid_on.replace(day=1)
        if m in actual_without_loan:
            actual_without_loan[m] += e.amount_minor
    undated, unknown_undated, undated_count = 0, 0, 0
    for p in db.scalars(select(Plan).where(Plan.owner_id == owner, Plan.archived.is_(False))):
        if p.paid_on and p.paid_on >= months[0]:
            payment_month = p.paid_on.replace(day=1)
            if payment_month in actual_without_loan:
                actual_without_loan[payment_month] += p.paid_amount_minor
        rest = plan_remaining_minor(p)
        if p.due_month is None:
            if rest is None:
                unknown_undated += 1
            elif rest:
                undated += rest
                undated_count += 1
        elif p.due_month in planned_without_loan:
            if rest is None:
                missing[p.due_month] += 1
            else:
                planned_without_loan[p.due_month] += rest
    loan_actual, loan_planned = 0, 0
    current_day = today()
    for loan in db.scalars(select(LoanPeriod).where(LoanPeriod.owner_id == owner)):
        month = loan.payment_date.replace(day=1)
        if month not in actual_without_loan:
            continue
        amount = loan_calculation(loan)[2]
        if loan.payment_date <= current_day:
            loan_actual_by_month[month] += amount
            loan_actual += amount
        else:
            loan_planned_by_month[month] += amount
            loan_planned += amount
    return {
        "months": [
            {
                "month": m.isoformat(),
                "actual": money(actual_without_loan[m] + loan_actual_by_month[m]),
                "planned_remaining": money(planned_without_loan[m] + loan_planned_by_month[m]),
                "known_total": money(
                    actual_without_loan[m]
                    + planned_without_loan[m]
                    + loan_actual_by_month[m]
                    + loan_planned_by_month[m]
                ),
                "actual_without_loan": money(actual_without_loan[m]),
                "planned_without_loan": money(planned_without_loan[m]),
                "known_total_without_loan": money(actual_without_loan[m] + planned_without_loan[m]),
                "loan_actual": money(loan_actual_by_month[m]),
                "loan_planned": money(loan_planned_by_month[m]),
                "unpriced": missing[m],
                "overdue_plan": m < today().replace(day=1)
                and (planned_without_loan[m] > 0 or missing[m] > 0),
            }
            for m in months
        ],
        "undated_remaining": money(undated),
        "undated_count": undated_count,
        "undated_unpriced": unknown_undated,
        "loan_actual": money(loan_actual),
        "loan_planned": money(loan_planned),
        "basis": "Факт по датам оплат; будущие расходы по месяцу плана; заём по дате платежа. Пустые цены не включены в итог.",
    }


def loan_calculation(item: LoanPeriod) -> tuple[int, int, int]:
    days_in_year = 366 if isleap(item.usage_month.year) else 365
    days_in_month = monthrange(item.usage_month.year, item.usage_month.month)[1]
    rate = Decimal(item.key_rate) if item.key_rate is not None else None
    material = tax = Decimal(0)
    if rate is not None:
        balance = Decimal(item.end_balance_minor) / 100
        material = balance * (
            (Decimal(2) / 3 * rate / 100 - Decimal("0.03")) / days_in_year * days_in_month
        )
        tax = material * Decimal("0.35")
    total = Decimal(item.payment_minor) / 100 + tax

    def to_minor(value: Decimal) -> int:
        return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return to_minor(material), to_minor(tax), to_minor(total)


def loan_period_view(item: LoanPeriod) -> dict:
    days_in_year = 366 if isleap(item.usage_month.year) else 365
    days_in_month = monthrange(item.usage_month.year, item.usage_month.month)[1]
    rate = Decimal(item.key_rate) if item.key_rate is not None else None
    material_minor, tax_minor, total_minor = loan_calculation(item)
    return {
        "id": item.id,
        "usage_month": item.usage_month.isoformat(),
        "days_in_year": days_in_year,
        "days_in_month": days_in_month,
        "period_index": item.period_index,
        "payment_date": item.payment_date.isoformat(),
        "key_rate": f"{rate:.4f}" if rate is not None else None,
        "rate_kind": item.rate_kind,
        "rate_effective_on": (
            item.rate_effective_on.isoformat() if item.rate_effective_on else None
        ),
        "start_balance": money(item.start_balance_minor),
        "interest": money(item.interest_minor),
        "principal": money(item.principal_minor),
        "payment": money(item.payment_minor),
        "end_balance": money(item.end_balance_minor),
        "material_benefit": money(material_minor) if rate is not None else None,
        "material_tax": money(tax_minor) if rate is not None else None,
        "total": money(total_minor),
    }


def list_loan_periods(db: Session, owner: int) -> list[dict]:
    query = select(LoanPeriod).where(LoanPeriod.owner_id == owner).order_by(LoanPeriod.period_index)
    return [loan_period_view(item) for item in db.scalars(query)]


def set_loan_rate(
    db: Session, owner: int, identifier: str, key_rate: Decimal, effective_on: date | None
) -> dict:
    item = owned(db, LoanPeriod, owner, identifier, lock=True)
    rate = Decimal(key_rate).quantize(Decimal("0.0001"))
    if not Decimal("0") <= rate <= Decimal("100"):
        raise DomainError("Ключевая ставка должна быть от 0 до 100%.")
    before = loan_period_view(item)
    item.key_rate = rate
    item.rate_kind = "manual"
    item.rate_effective_on = effective_on or item.payment_date
    item.updated_at = utcnow()
    db.flush()
    result = loan_period_view(item)
    audit(db, owner, "loan_period", item.id, "set_rate", before, result)
    return result


def refresh_loan_rates(db: Session, owner: int, fetcher=None) -> dict:
    periods = list(
        db.scalars(
            select(LoanPeriod)
            .where(LoanPeriod.owner_id == owner)
            .order_by(LoanPeriod.period_index)
            .with_for_update()
        )
    )
    if not periods:
        raise DomainError("Сначала импортируйте график займа.", 409)
    current_day = today()
    first_day = min(item.payment_date for item in periods) - timedelta(days=14)
    fetcher = fetcher or cbr.fetch_key_rates
    try:
        rates = fetcher(first_day, current_day)
    except cbr.CbrError as exc:
        raise DomainError(str(exc), 502) from exc
    ordered = sorted(rates.items())
    current_options = [(day, rate) for day, rate in ordered if day <= current_day]
    if not current_options:
        raise DomainError("Нет ставки Банка России на текущую дату.", 502)
    latest_day, latest_rate = current_options[-1]
    actual = forecast = 0
    for item in periods:
        if item.payment_date <= current_day:
            options = [(day, rate) for day, rate in ordered if day <= item.payment_date]
            if not options:
                raise DomainError(f"Нет ключевой ставки на {item.payment_date:%d.%m.%Y}.", 502)
            effective_on, rate = options[-1]
            item.rate_kind = "actual"
            actual += 1
        else:
            effective_on, rate = latest_day, latest_rate
            item.rate_kind = "forecast"
            forecast += 1
        item.key_rate = rate.quantize(Decimal("0.0001"))
        item.rate_effective_on = effective_on
        item.updated_at = utcnow()
    result = {
        "updated": len(periods),
        "actual": actual,
        "forecast": forecast,
        "latest_rate": f"{latest_rate:.4f}",
        "latest_rate_date": latest_day.isoformat(),
        "source": cbr.SOURCE_PAGE,
    }
    audit(db, owner, "loan", str(owner), "refresh_rates", None, result)
    return result


def once(db: Session, owner: int, key: str, payload: dict, action):
    if not re.fullmatch(r"[A-Za-z0-9:_.-]{1,180}", key):
        raise DomainError("Неверный ключ повторной доставки.")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    query = select(RequestLog).where(RequestLog.owner_id == owner, RequestLog.key == key)
    previous = db.scalar(query)
    if previous is None:
        try:
            with db.begin_nested():
                previous = RequestLog(owner_id=owner, key=key, fingerprint=digest, response={})
                db.add(previous)
                db.flush()
        except IntegrityError:
            previous = db.scalar(query)
            if previous is None:
                raise
        else:
            previous.response = action()
            db.flush()
            return previous.response
    if previous.fingerprint != digest:
        raise DomainError("Этот ключ уже использован для другого запроса.", 409)
    return previous.response
