"""Idempotent reconciliation of the renovation plan with the agreed source documents."""

from datetime import date

from sqlalchemy import select

from flat_expense import service as s
from flat_expense.models import Category, Plan
from flat_expense.schemas import PlanIn

SOURCE_NOTE = "Смета Neat от 22.08.2026, ред. 0912"
WORK_STAGES = {
    3: ("Этап 3: штукатурка", 637_052, date(2026, 11, 1)),
    4: ("Этап 4: сантехника, отопление, шумоизоляция пола", 506_993, date(2026, 12, 1)),
    5: ("Этап 5: черновая электрика", 676_620, date(2027, 1, 1)),
    6: ("Этап 6: стяжка", 202_986, date(2027, 2, 1)),
    7: ("Этап 7: санузлы, плитка", 597_987, date(2027, 3, 1)),
    8: ("Этап 8: кварцвинил", 168_768, date(2027, 6, 1)),
    10: ("Этап 10: подготовка стен под отделку", 509_733, date(2027, 4, 1)),
    11: ("Этап 11: обои и окраска", 540_189, date(2027, 5, 1)),
    12: ("Этап 12: чистовая электрика", 265_010, date(2027, 7, 1)),
    13: ("Этап 13: чистовая сантехника", 63_900, date(2027, 7, 1)),
}
MATERIALS = {
    3: ("Материалы для штукатурки", "Материалы к этапу 3: штукатурка"),
    4: ("Материалы сантехники и отопления", "Материалы к этапу 4: сантехника и отопление"),
    5: ("Материалы черновой электрики", "Материалы к этапу 5: черновая электрика"),
    6: ("Материалы стяжки и шумоизоляции пола", "Материалы к этапу 6: стяжка и шумоизоляция"),
    7: ("Плитка, керамогранит, клеи и затирка", "Материалы к этапу 7: плитка и санузлы"),
    8: ("Кварцвинил, подложка и плинтусы", "Материалы к этапу 8: кварцвинил"),
    9: (None, "Материалы к этапу 9: дополнительная шумоизоляция"),
    10: ("Шпаклёвки, грунтовки, краски и расходники", "Материалы к этапу 10: подготовка стен"),
    11: ("Обои и материалы отделки стен", "Материалы к этапу 11: обои и окраска"),
    12: (None, "Материалы к этапу 12: чистовая электрика"),
    13: (None, "Материалы к этапу 13: чистовая сантехника"),
}


def _find_plan(db, owner: int, *titles: str | None) -> Plan | None:
    names = [title for title in titles if title]
    items = list(db.scalars(select(Plan).where(Plan.owner_id == owner, Plan.title.in_(names))))
    if len(items) > 1:
        raise s.DomainError(f"Найдено несколько планов: {', '.join(names)}.")
    return items[0] if items else None


def _update(db, owner: int, item: Plan, **changes) -> None:
    data = PlanIn(
        title=changes.get("title", item.title),
        category_id=changes.get("category_id", item.category_id),
        expected=s.money(item.expected_minor),
        agreed=s.money(item.agreed_minor),
        due_month=changes.get("due_month", item.due_month),
        note=changes.get("note", item.note),
    )
    s.update_plan(db, owner, item.id, data, item.version)


def apply_future_plan(db, owner: int) -> dict:
    roots = {
        item.name: item
        for item in db.scalars(
            select(Category).where(Category.owner_id == owner, Category.parent_id.is_(None))
        )
    }
    repair = roots.get("Ремонт")
    if repair is None:
        raise s.DomainError("Не найдена группа «Ремонт».")
    children = {
        item.name: item
        for item in db.scalars(select(Category).where(Category.parent_id == repair.id))
    }
    works = children.get("Работы")
    materials = children.get("Материалы")
    purchases = children.get("Покупки")
    if not works or not materials or not purchases:
        raise s.DomainError("В группе «Ремонт» не хватает категорий работ, материалов или покупок.")

    for _, (title, amount, due_month) in WORK_STAGES.items():
        item = _find_plan(db, owner, title)
        if item is None or item.expected_minor != amount * 100:
            raise s.DomainError(f"Не найдена точная строка сметы: {title} — {amount} ₽.")
        if item.due_month != due_month:
            _update(db, owner, item, due_month=due_month)

    stage9 = _find_plan(db, owner, "Этап 9: дополнительная шумоизоляция стен")
    stage9_note = (
        f"Опциональный этап; нулевая сумма в {SOURCE_NOTE}; цену уточнить при подтверждении работ."
    )
    if stage9 is None:
        s.add_plan(
            db,
            owner,
            PlanIn(
                title="Этап 9: дополнительная шумоизоляция стен",
                category_id=works.id,
                due_month=date(2027, 4, 1),
                note=stage9_note,
            ),
        )
    elif (
        stage9.category_id != works.id
        or stage9.due_month != date(2027, 4, 1)
        or stage9.note != stage9_note
    ):
        _update(
            db,
            owner,
            stage9,
            category_id=works.id,
            due_month=date(2027, 4, 1),
            note=stage9_note,
        )

    material_count = 0
    for stage, (old_title, new_title) in MATERIALS.items():
        due_month = date(2027, 4, 1) if stage == 9 else WORK_STAGES[stage][2]
        item = _find_plan(db, owner, old_title, new_title)
        note_marker = f"Материалы для незавершённого этапа {stage}."
        if item is None:
            s.add_plan(
                db,
                owner,
                PlanIn(
                    title=new_title,
                    category_id=materials.id,
                    due_month=due_month,
                    note=f"{note_marker} Сумму уточнить; работы учтены отдельно по {SOURCE_NOTE}.",
                ),
            )
        else:
            note = item.note
            if note_marker not in note:
                note = f"{note}; {note_marker}" if note else note_marker
            if item.title != new_title or item.due_month != due_month or item.note != note:
                _update(db, owner, item, title=new_title, due_month=due_month, note=note)
        material_count += 1

    purchase_items = list(
        db.scalars(
            select(Plan)
            .where(Plan.owner_id == owner, Plan.category_id == purchases.id)
            .order_by(Plan.created_at, Plan.id)
        )
    )
    if len(purchase_items) != 40:
        raise s.DomainError(
            f"Ожидалось 40 позиций из плана покупок, найдено {len(purchase_items)}."
        )
    months = [date(2026 + (10 + offset) // 12, (10 + offset) % 12 + 1, 1) for offset in range(10)]
    distribution = {month.isoformat(): 0 for month in months}
    for index, item in enumerate(purchase_items):
        due_month = months[index // 4]
        distribution[due_month.isoformat()] += 1
        marker = "Распределено равномерно из исходного плана покупок."
        note = (
            item.note
            if marker in (item.note or "")
            else (f"{item.note}; {marker}" if item.note else marker)
        )
        if item.due_month != due_month or item.note != note:
            _update(db, owner, item, due_month=due_month, note=note)

    result = {
        "completed_stages": [1, 2],
        "completed_amount": "443000.00",
        "remaining_priced_stages": len(WORK_STAGES),
        "remaining_work_total": s.money(sum(amount for _, amount, _ in WORK_STAGES.values()) * 100),
        "optional_unpriced_stage": 9,
        "material_positions": material_count,
        "purchase_positions": len(purchase_items),
        "purchase_distribution": distribution,
    }
    s.audit(db, owner, "plan", str(owner), "reconcile_future_plan", None, result)
    return result
