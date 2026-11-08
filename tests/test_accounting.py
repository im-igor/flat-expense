from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import make_expense, make_plan, post, report
from sqlalchemy import func, select

from flat_expense import service as s
from flat_expense.models import Audit, Category, Expense, Plan
from flat_expense.schemas import ExpenseIn


def test_expenses_and_plans_are_independent(client, headers, category):
    make_plan(client, headers, category, agreed="280000")
    advance = make_expense(client, headers, category)
    may, june = report(client, headers)["months"]
    assert (may["actual"], may["planned_remaining"]) == ("100000.00", "0.00")
    assert (june["actual"], june["planned_remaining"]) == ("0.00", "280000.00")
    make_expense(client, headers, category, "balance", amount="180000", paid_on="2027-06-20")
    june = report(client, headers)["months"][1]
    assert (june["actual"], june["planned_remaining"], june["known_total"]) == (
        "180000.00",
        "280000.00",
        "460000.00",
    )
    removed = post(
        client, headers, f"/v1/expenses/{advance['id']}/delete", "delete", {"version": 1}
    ).json()
    may, june = report(client, headers)["months"]
    assert may["actual"] == "0.00" and june["planned_remaining"] == "280000.00"
    post(
        client,
        headers,
        f"/v1/expenses/{advance['id']}/restore",
        "restore",
        {"version": removed["version"]},
    )
    may, june = report(client, headers)["months"]
    assert may["actual"] == "100000.00" and june["planned_remaining"] == "280000.00"


def test_repeated_delivery_and_conflicting_key(client, headers, category, sessions):
    first = make_expense(client, headers, category)
    second = make_expense(client, headers, category)
    assert first == second
    response = post(
        client,
        headers,
        "/v1/expenses",
        "expense1",
        {
            "category_id": category["id"],
            "amount": "200",
            "paid_on": "2027-05-01",
            "description": "Другой расход",
        },
    )
    assert response.status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Expense)) == 1
        assert db.scalar(select(func.count()).select_from(Audit)) == 1


def test_price_change_and_move_do_not_change_expense(client, headers, category):
    p = make_plan(client, headers, category)
    e = make_expense(client, headers, category)
    data = {
        "title": "Межкомнатные двери",
        "category_id": category["id"],
        "expected": "250000",
        "agreed": "270000",
        "due_month": "2027-05-20",
        "version": 1,
    }
    response = client.put(
        f"/v1/plans/{p['id']}", headers={**headers, "Idempotency-Key": "edit"}, json=data
    )
    assert response.status_code == 200
    assert response.json()["due_month"] == "2027-05-01"
    assert client.get(f"/v1/expenses/{e['id']}", headers=headers).json()["amount"] == "100000.00"
    assert report(client, headers)["months"][0]["known_total"] == "370000.00"
    assert (
        client.put(
            f"/v1/plans/{p['id']}", headers={**headers, "Idempotency-Key": "stale"}, json=data
        ).status_code
        == 409
    )


def test_blank_zero_and_undated_plans(client, headers, category):
    make_plan(client, headers, category, "unknown", expected=None)
    make_plan(client, headers, category, "zero", expected="0")
    make_plan(client, headers, category, "no-date", expected="500", due_month=None)
    make_plan(client, headers, category, "nothing-known", expected=None, due_month=None)
    result = report(client, headers)
    assert result["months"][1]["unpriced"] == 1
    assert result["months"][1]["planned_remaining"] == "0.00"
    assert result["undated_remaining"] == "500.00" and result["undated_unpriced"] == 1


def test_archive_keeps_actuals_and_restore_returns_forecast(client, headers, category):
    p = make_plan(client, headers, category)
    make_expense(client, headers, category)
    archived = post(
        client, headers, f"/v1/plans/{p['id']}/archive", "archive", {"version": 1}
    ).json()
    assert report(client, headers)["months"][0]["actual"] == "100000.00"
    assert report(client, headers)["months"][1]["planned_remaining"] == "0.00"
    post(
        client, headers, f"/v1/plans/{p['id']}/restore", "restore", {"version": archived["version"]}
    )
    assert report(client, headers)["months"][1]["planned_remaining"] == "250000.00"


def test_delete_plan_does_not_change_actual_payments(client, headers, category, sessions):
    plan = make_plan(client, headers, category)
    expense = make_expense(client, headers, category)
    response = client.request(
        "DELETE",
        f"/v1/plans/{plan['id']}",
        headers={**headers, "Idempotency-Key": "delete-plan"},
        json={"version": plan["version"]},
    )
    assert response.status_code == 200
    assert response.json() == {"id": plan["id"], "deleted": True}
    assert client.get(f"/v1/plans/{plan['id']}", headers=headers).status_code == 404
    saved = client.get(f"/v1/expenses/{expense['id']}", headers=headers).json()
    assert saved["amount"] == "100000.00"
    assert report(client, headers)["months"][0]["actual"] == "100000.00"
    with sessions() as db:
        entry = db.scalar(select(Audit).where(Audit.entity == "plan", Audit.action == "delete"))
        assert entry.after == {"id": plan["id"], "deleted": True}


def test_edit_fact_recalculates_both_months(client, headers, category):
    e = make_expense(client, headers, category, amount="3731.15")
    data = {
        "paid_on": "2027-06-01",
        "amount": "3797.82",
        "category_id": category["id"],
        "description": "Исправлено",
        "version": 1,
    }
    response = client.put(
        f"/v1/expenses/{e['id']}", headers={**headers, "Idempotency-Key": "fix"}, json=data
    )
    assert response.status_code == 200
    may, june = report(client, headers)["months"]
    assert may["actual"] == "0.00" and june["actual"] == "3797.82"


def test_expense_plan_and_paid_amount_are_independent(client, headers, category):
    expense = make_expense(
        client,
        headers,
        category,
        amount="118565.00",
        planned_amount="382231.20",
        description="Материалы по этапу 3 штукатурка стен",
        paid_on="2026-10-21",
    )
    assert expense["planned_amount"] == "382231.20"
    assert expense["amount"] == "118565.00"

    response = client.put(
        f"/v1/expenses/{expense['id']}",
        headers={**headers, "Idempotency-Key": "independent-amounts"},
        json={
            "planned_amount": "382231.20",
            "amount": "120000.00",
            "paid_on": "2026-10-21",
            "category_id": category["id"],
            "description": expense["description"],
            "version": expense["version"],
        },
    )
    assert response.status_code == 200
    assert response.json()["planned_amount"] == "382231.20"
    assert response.json()["amount"] == "120000.00"

    # A legacy client that omits the new field changes only the paid amount.
    updated = response.json()
    legacy = client.put(
        f"/v1/expenses/{expense['id']}",
        headers={**headers, "Idempotency-Key": "legacy-amount-edit"},
        json={
            "amount": "118565.00",
            "paid_on": "2026-10-21",
            "category_id": category["id"],
            "description": expense["description"],
            "version": updated["version"],
        },
    ).json()
    assert legacy["planned_amount"] == "382231.20"
    assert legacy["amount"] == "118565.00"


def test_bulk_move_plan_due_month_updates_selected_active_plans(client, headers, category, sessions):
    first = make_plan(client, headers, category, key="bulk-first", due_month="2027-06-01")
    second = make_plan(client, headers, category, key="bulk-second", due_month="2027-07-01")

    response = post(
        client,
        headers,
        "/v1/plans/bulk-due-month",
        "bulk-move",
        {"ids": [first["id"], second["id"]], "due_month": "2027-09-19"},
    )

    assert response.status_code == 200
    assert response.json() == {"updated": 2, "due_month": "2027-09-01"}
    assert client.get(f"/v1/plans/{first['id']}", headers=headers).json()["due_month"] == "2027-09-01"
    assert client.get(f"/v1/plans/{second['id']}", headers=headers).json()["due_month"] == "2027-09-01"
    with sessions() as db:
        actions = db.scalars(select(Audit.action).where(Audit.action == "bulk_move_due_month")).all()
        assert actions == ["bulk_move_due_month", "bulk_move_due_month"]


def test_bulk_move_plan_due_month_rejects_archived_plan_without_partial_update(
    client, headers, category
):
    active = make_plan(client, headers, category, key="bulk-active", due_month="2027-06-01")
    archived = make_plan(client, headers, category, key="bulk-archived", due_month="2027-07-01")
    assert (
        post(
            client,
            headers,
            f"/v1/plans/{archived['id']}/archive",
            "archive-before-bulk",
            {"version": archived["version"]},
        ).status_code
        == 200
    )

    response = post(
        client,
        headers,
        "/v1/plans/bulk-due-month",
        "bulk-archived",
        {"ids": [active["id"], archived["id"]], "due_month": "2027-09-01"},
    )

    assert response.status_code == 409
    assert client.get(f"/v1/plans/{active['id']}", headers=headers).json()["due_month"] == "2027-06-01"


def test_new_expense_initializes_plan_once_from_fact(client, headers, category):
    expense = make_expense(client, headers, category, amount="118565.00")
    assert expense["planned_amount"] == "118565.00"

    updated = client.put(
        f"/v1/expenses/{expense['id']}",
        headers={**headers, "Idempotency-Key": "change-fact-only"},
        json={
            "amount": "120000.00",
            "paid_on": expense["paid_on"],
            "category_id": category["id"],
            "description": expense["description"],
            "version": expense["version"],
        },
    ).json()
    assert updated["planned_amount"] == "118565.00"
    assert updated["amount"] == "120000.00"


@pytest.mark.parametrize("amount", ["-1", "0", "1.001", "NaN", "Infinity", "10000000000"])
def test_invalid_money_rejected(client, headers, category, amount):
    response = post(
        client,
        headers,
        "/v1/expenses",
        "bad",
        {
            "amount": amount,
            "paid_on": "2027-05-01",
            "category_id": category["id"],
            "description": "x",
        },
    )
    assert response.status_code == 422


def test_authorization_and_tenant_isolation(client, headers, category):
    p = make_plan(client, headers, category)
    e = make_expense(client, headers, category)
    assert client.get("/v1/expenses").status_code == 401
    assert (
        client.get("/v1/expenses", headers={**headers, "X-Telegram-User-Id": "333"}).status_code
        == 403
    )
    other = {**headers, "X-Telegram-User-Id": "222"}
    assert client.get("/v1/expenses", headers=other).json() == []
    assert client.get(f"/v1/expenses/{e['id']}", headers=other).status_code == 404
    assert client.get(f"/v1/plans/{p['id']}", headers=other).status_code == 404
    response = post(
        client,
        other,
        "/v1/expenses",
        "cross",
        {
            "amount": "10",
            "paid_on": "2027-05-01",
            "category_id": category["id"],
            "description": "x",
        },
    )
    assert response.status_code == 404


def test_only_two_category_levels(client, headers, category):
    assert (
        client.post(
            "/v1/categories",
            headers=headers,
            json={"name": "Третий уровень", "parent_id": category["id"]},
        ).status_code
        == 400
    )


def test_category_rename_replace_and_delete_updates_existing_records(
    client, headers, category, sessions
):
    plan = make_plan(client, headers, category)
    expense = make_expense(client, headers, category)
    renamed = client.put(
        f"/v1/categories/{category['id']}",
        headers={**headers, "Idempotency-Key": "rename-category"},
        json={"name": "Монтаж"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["path"] == "Ремонт → Монтаж"
    assert (
        client.get(f"/v1/expenses/{expense['id']}", headers=headers)
        .json()["category"]
        .endswith("Монтаж")
    )

    target = client.post(
        "/v1/categories",
        headers=headers,
        json={"name": "Материалы", "parent_id": category["parent_id"]},
    ).json()
    response = client.post(
        f"/v1/categories/{category['id']}/replace",
        headers={**headers, "Idempotency-Key": "replace-category"},
        json={"target_id": target["id"]},
    )
    assert response.status_code == 200
    assert response.json()["expenses"] == 1 and response.json()["plans"] == 1
    assert (
        client.post(
            f"/v1/categories/{category['id']}/replace",
            headers={**headers, "Idempotency-Key": "replace-category"},
            json={"target_id": target["id"]},
        ).json()
        == response.json()
    )
    with sessions() as db:
        assert db.get(Category, category["id"]) is None
        assert db.get(Expense, expense["id"]).category_id == target["id"]
        assert db.get(Expense, expense["id"]).version == 2
        assert db.get(Plan, plan["id"]).category_id == target["id"]
        assert db.get(Plan, plan["id"]).version == 2

    unused = client.post("/v1/categories", headers=headers, json={"name": "Временная"}).json()
    deleted = client.delete(
        f"/v1/categories/{unused['id']}",
        headers={**headers, "Idempotency-Key": "delete-category"},
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True


def test_category_delete_conflicts_and_tenant_isolation(client, headers, category):
    assert (
        client.delete(
            f"/v1/categories/{category['parent_id']}",
            headers={**headers, "Idempotency-Key": "delete-parent"},
        ).status_code
        == 409
    )
    make_expense(client, headers, category)
    assert (
        client.delete(
            f"/v1/categories/{category['id']}",
            headers={**headers, "Idempotency-Key": "delete-used"},
        ).status_code
        == 409
    )
    other = {**headers, "X-Telegram-User-Id": "222", "Idempotency-Key": "cross-category"}
    other_target = client.post("/v1/categories", headers=other, json={"name": "Другая"}).json()
    assert (
        client.post(
            f"/v1/categories/{category['id']}/replace",
            headers=other,
            json={"target_id": other_target["id"]},
        ).status_code
        == 404
    )


def test_concurrent_same_key_is_one_payment(sessions, category):
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency test requires TEST_DATABASE_URL")
    data = ExpenseIn(
        paid_on="2027-05-01", amount="100", category_id=category["id"], description="Аванс"
    )

    def submit(_):
        with sessions() as db, db.begin():
            return s.once(
                db,
                111,
                "parallel",
                data.model_dump(mode="json"),
                lambda: s.add_expense(db, 111, data),
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert len({r["id"] for r in results}) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Expense)) == 1


def test_settings_require_positive_user_allowlist():
    from pydantic import ValidationError

    from flat_expense.config import Settings

    for value in ("", "0", "-111", "111,", "111,word"):
        with pytest.raises(ValidationError):
            Settings(
                api_token="test-token-with-at-least-32-characters", allowed_telegram_user_ids=value
            )
    config = Settings(
        api_token="test-token-with-at-least-32-characters", allowed_telegram_user_ids=" 111, 222 "
    )
    assert config.allowed_ids == {111, 222}
