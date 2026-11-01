"""Idempotent reconciliation of the renovation plan with the agreed source documents."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from flat_expense import service as s
from flat_expense.models import Category, Plan
from flat_expense.schemas import PlanIn

SOURCE_NOTE = "Смета Neat от 22.08.2026, ред. 0912"
TECH_PRICE_NOTE = (
    "Ориентир верхнего ценового сегмента на 01.11.2026; "
    "уточнить модель и поставщика перед покупкой."
)
TECH_PRICE_SOURCES = [
    "https://high-level.ru/brands/signature/",
    "https://miele-store.ru/market/sushilnye-mashiny/recommend/premium/",
    "https://miele-store.ru/market/washing-machine/washing-dry-machine/",
    "https://hold-group.ru/zhurnal/ventilyatsiya-i-konditsionirovanie-kvartiry-penthausa",
    "https://arlight.group/catalog/curtains/",
    "https://orbitase.tech/catalog/home?sort=expensive&view=table",
]
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
TECH_PURCHASES = [
    ("Кухня", "Варочная панель", 450_000),
    ("Кухня", "Вытяжка", 300_000),
    ("Кухня", "Духовой шкаф", 600_000),
    ("Кухня", "Микроволновая печь", 250_000),
    ("Кухня", "Кофемашина", 500_000),
    ("Кухня", "Мультиварка", 100_000),
    ("Кухня", "Посудомоечная машина", 250_000),
    ("Кухня", "Холодильник встроенный", 1_200_000),
    ("Кухня", "Морозильная камера (отдельная)", 800_000),
    ("Кухня", "Чайник", 50_000),
    ("Кухня", "Тостер", 50_000),
    ("Кухня", "Измельчитель пищевых отходов", 120_000),
    ("Кухня", "Фильтр для воды", 100_000),
    ("Кухня", "Бойлер для воды подстольный", 120_000),
    ("Санузел / постирочная", "Водонагреватель проточный", 100_000),
    ("Санузел / постирочная", "Полотенцесушитель", 300_000),
    ("Санузел / постирочная", "Система защиты от протечек", 150_000),
    ("Санузел / постирочная", "Стиральная машина", 420_000),
    ("Санузел / постирочная", "Сушильная машина", 500_000),
    ("Гостиная / Общее пространство", "Телевизор", 600_000),
    ("Гостиная / Общее пространство", "Сетевое хранилище NAS", 500_000),
    ("Гостиная / Общее пространство", "Контроллер умного дома", 300_000),
    ("Климат", "Умные термостаты на отопление", 300_000),
    ("Климат", "Датчики температуры и влажности", 100_000),
    ("Климат", "Система увлажнения воздуха", 1_000_000),
    ("Климат", "Приточная вентиляционная установка", 1_500_000),
    ("Климат", "Кондиционрирование", 1_500_000),
    ("Общее", "Умные приводы штор", 300_000),
    ("Общее", "Видеодомофон", 200_000),
    ("Общее", "Робот-пылесос", 150_000),
    ("Общее", "Вертикальный пылесос", 100_000),
    ("Общее", "Электроприводы штор и рулонных штор", 500_000),
]
OTHER_PURCHASES = [
    "Двери: полотна, коробки, фурнитура, установка",
    "Натяжные потолки с монтажом",
    "Светильники, розетки и выключатели",
    "Сантехника: ванна, унитазы, раковины, смесители",
    "Кухня со столешницей, доставкой и монтажом",
    "Встроенные шкафы и гардеробные",
    "Кровати, матрасы, диван, стол и стулья",
    "Шторы и карнизы",
]


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
        expected=changes.get("expected", s.money(item.expected_minor)),
        agreed=changes.get("agreed", s.money(item.agreed_minor)),
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
    material_categories = [
        item for name, item in children.items() if name in ("Материалы", "Черновые материалы")
    ]
    if len(material_categories) > 1:
        raise s.DomainError("Найдены обе категории «Материалы» и «Черновые материалы».")
    materials = material_categories[0] if material_categories else None
    purchases = children.get("Покупки")
    if not works or not materials or not purchases:
        raise s.DomainError("В группе «Ремонт» не хватает категорий работ, материалов или покупок.")
    if materials.name == "Материалы":
        s.rename_category(db, owner, materials.id, "Черновые материалы")

    technology = s.ensure_category(db, owner, "Техника")
    technology_rooms = {
        room: s.ensure_category(db, owner, room, technology.id)
        for room in dict.fromkeys(room for room, _, _ in TECH_PURCHASES)
    }

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
    material_budget = Decimal(0)
    for stage, (old_title, new_title) in MATERIALS.items():
        due_month = date(2027, 4, 1) if stage == 9 else WORK_STAGES[stage][2]
        work_amount = Decimal(0 if stage == 9 else WORK_STAGES[stage][1])
        material_amount = work_amount * Decimal("0.60")
        material_budget += material_amount
        item = _find_plan(db, owner, old_title, new_title)
        note = (
            f"Материалы для незавершённого этапа {stage}. "
            f"Плановая сумма = 60% стоимости работ по {SOURCE_NOTE}."
        )
        if stage == 9:
            note += " В смете стоимость опционального этапа равна 0 ₽; сумму нужно уточнить."
        if item is None:
            s.add_plan(
                db,
                owner,
                PlanIn(
                    title=new_title,
                    category_id=materials.id,
                    expected=material_amount,
                    due_month=due_month,
                    note=note,
                ),
            )
        else:
            expected_minor = s.minor(material_amount)
            if (
                item.title != new_title
                or item.category_id != materials.id
                or item.expected_minor != expected_minor
                or item.due_month != due_month
                or item.note != note
            ):
                _update(
                    db,
                    owner,
                    item,
                    title=new_title,
                    category_id=materials.id,
                    expected=material_amount,
                    due_month=due_month,
                    note=note,
                )
        material_count += 1

    purchase_titles = [title for _, title, _ in TECH_PURCHASES] + OTHER_PURCHASES
    found_purchases = list(
        db.scalars(select(Plan).where(Plan.owner_id == owner, Plan.title.in_(purchase_titles)))
    )
    purchase_by_title = {}
    for item in found_purchases:
        if item.title in purchase_by_title:
            raise s.DomainError(f"Найдено несколько планов с названием «{item.title}».")
        purchase_by_title[item.title] = item
    missing = [title for title in purchase_titles if title not in purchase_by_title]
    if missing:
        raise s.DomainError(f"Не найдены позиции плана покупок: {', '.join(missing)}.")
    purchase_items = [purchase_by_title[title] for title in purchase_titles]
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

    tech_budget = 0
    for room, title, amount in TECH_PURCHASES:
        item = purchase_by_title[title]
        category_id = technology_rooms[room].id
        note = (
            item.note
            if TECH_PRICE_NOTE in (item.note or "")
            else (f"{item.note}; {TECH_PRICE_NOTE}" if item.note else TECH_PRICE_NOTE)
        )
        if (
            item.category_id != category_id
            or item.expected_minor != amount * 100
            or item.note != note
        ):
            _update(
                db,
                owner,
                item,
                category_id=category_id,
                expected=Decimal(amount),
                note=note,
            )
        tech_budget += amount

    result = {
        "completed_stages": [1, 2],
        "completed_amount": "443000.00",
        "remaining_priced_stages": len(WORK_STAGES),
        "remaining_work_total": s.money(sum(amount for _, amount, _ in WORK_STAGES.values()) * 100),
        "optional_unpriced_stage": 9,
        "material_positions": material_count,
        "material_budget_total": f"{material_budget:.2f}",
        "materials_category": materials.name,
        "purchase_positions": len(purchase_items),
        "purchase_distribution": distribution,
        "tech_positions": len(TECH_PURCHASES),
        "tech_budget_total": f"{Decimal(tech_budget):.2f}",
        "tech_rooms": {room: 0 for room in technology_rooms},
        "tech_price_sources": TECH_PRICE_SOURCES,
    }
    for room, _, _ in TECH_PURCHASES:
        result["tech_rooms"][room] += 1
    s.audit(db, owner, "plan", str(owner), "reconcile_future_plan", None, result)
    return result
