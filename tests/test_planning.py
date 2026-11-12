from collections import Counter
from datetime import date

from sqlalchemy import func, select

from flat_expense import service as s
from flat_expense.models import Category, Plan
from flat_expense.planning import (
    MATERIALS,
    OTHER_PURCHASES,
    TECH_PRICE_SOURCES,
    TECH_PURCHASES,
    WORK_STAGES,
    apply_future_plan,
)
from flat_expense.schemas import PlanIn


def add_plan(db, owner, category_id, title, expected=None, agreed=None, due_month=None, note=""):
    return s.add_plan(
        db,
        owner,
        PlanIn(
            title=title,
            category_id=category_id,
            expected=expected,
            agreed=agreed,
            due_month=due_month,
            note=note,
        ),
    )


def test_future_plan_reconciles_remaining_neat_stages_and_purchases(sessions):
    owner = 111
    with sessions() as db, db.begin():
        repair = s.ensure_category(db, owner, "Ремонт")
        works = s.ensure_category(db, owner, "Работы", repair.id)
        materials = s.ensure_category(db, owner, "Материалы", repair.id)
        purchases = s.ensure_category(db, owner, "Покупки", repair.id)

        add_plan(
            db,
            owner,
            works.id,
            "Этапы 1–2: потолочная шумоизоляция, подготовка, перегородки",
            agreed="443000",
            due_month=date(2026, 10, 1),
        )
        for title, amount, due_month in WORK_STAGES.values():
            add_plan(db, owner, works.id, title, expected=str(amount), due_month=due_month)
        for old_title, _ in MATERIALS.values():
            if old_title:
                add_plan(db, owner, materials.id, old_title)
        purchase_titles = [title for _, title, _ in TECH_PURCHASES] + OTHER_PURCHASES
        for title in purchase_titles:
            add_plan(
                db,
                owner,
                purchases.id,
                title,
                note="Из Excel" if title == "Вытяжка" else "",
            )

        result = apply_future_plan(db, owner)
        assert result["completed_stages"] == [1, 2]
        assert result["completed_amount"] == "443000.00"
        assert result["remaining_priced_stages"] == 10
        assert result["remaining_work_total"] == "4169238.00"
        assert result["material_positions"] == 11
        assert result["material_budget_total"] == "2501542.80"
        assert result["materials_category"] == "Черновые материалы"
        assert result["tech_positions"] == 32
        assert result["tech_budget_total"] == f"{sum(row[2] for row in TECH_PURCHASES):.2f}"
        assert result["tech_rooms"] == {
            "Кухня": 14,
            "Санузел / постирочная": 5,
            "Гостиная / Общее пространство": 3,
            "Климат": 5,
            "Общее": 5,
        }
        assert result["tech_price_sources"] == TECH_PRICE_SOURCES
        assert result["purchase_distribution"] == {
            "2026-11-01": 4,
            "2026-12-01": 4,
            "2027-01-01": 4,
            "2027-02-01": 4,
            "2027-03-01": 4,
            "2027-04-01": 4,
            "2027-05-01": 4,
            "2027-06-01": 4,
            "2027-07-01": 4,
            "2027-08-01": 4,
        }

        stage9 = db.scalar(
            select(Plan).where(
                Plan.title == "Этап 9: дополнительная шумоизоляция стен",
            )
        )
        assert stage9 is not None
        assert stage9.category_id == works.id
        assert stage9.expected_minor is None
        assert stage9.due_month == date(2027, 4, 1)
        assert stage9.note == ""

        material_titles = {new_title for _, new_title in MATERIALS.values()}
        material_rows = list(
            db.scalars(
                select(Plan).where(
                    Plan.title.in_(material_titles),
                )
            )
        )
        assert len(material_rows) == 11
        assert all(item.category_id == materials.id for item in material_rows)
        assert all(item.note == "" for item in material_rows)
        material_by_title = {item.title: item for item in material_rows}
        for stage, (_, title) in MATERIALS.items():
            expected_month = date(2027, 4, 1) if stage == 9 else WORK_STAGES[stage][2]
            assert material_by_title[title].due_month == expected_month
            work_amount = 0 if stage == 9 else WORK_STAGES[stage][1]
            assert material_by_title[title].expected_minor == work_amount * 60

        assert materials.name == "Черновые материалы"
        technology = db.scalar(
            select(Category).where(
                Category.parent_id.is_(None),
                Category.name == "Техника",
            )
        )
        rooms = {
            item.name: item.id
            for item in db.scalars(select(Category).where(Category.parent_id == technology.id))
        }
        for room, title, amount in TECH_PURCHASES:
            item = db.scalar(select(Plan).where(Plan.title == title))
            assert item.category_id == rooms[room]
            assert item.expected_minor == amount * 100
            assert item.note == ("Из Excel" if title == "Вытяжка" else "")

        purchase_months = Counter(
            db.scalars(
                select(Plan.due_month).where(
                    Plan.title.in_(purchase_titles),
                )
            )
        )
        assert set(purchase_months.values()) == {4}
        assert len(purchase_months) == 10

        count_before = db.scalar(select(func.count()).select_from(Plan))
        versions_before = dict(db.execute(select(Plan.id, Plan.version)).all())
        repeated = apply_future_plan(db, owner)
        assert repeated == result
        assert db.scalar(select(func.count()).select_from(Plan)) == count_before
        assert dict(db.execute(select(Plan.id, Plan.version)).all()) == versions_before
