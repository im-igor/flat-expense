from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import make_expense, make_plan, post, report
from sqlalchemy import func, select

from flat_expense import service as s
from flat_expense.models import Audit, Category, Expense, Plan
from flat_expense.schemas import ExpenseIn


def test_advance_invoice_balance_delete_restore(client, headers, category):
    p = make_plan(client, headers, category, agreed="280000")
    advance = make_expense(client, headers, category, plan_id=p["id"])
    may, june = report(client, headers)["months"]
    assert (may["actual"], may["planned_remaining"]) == ("100000.00", "0.00")
    assert (june["actual"], june["planned_remaining"]) == ("0.00", "180000.00")
    make_expense(
        client, headers, category, "balance", plan_id=p["id"], amount="180000", paid_on="2027-06-20"
    )
    june = report(client, headers)["months"][1]
    assert (june["actual"], june["planned_remaining"], june["known_total"]) == (
        "180000.00",
        "0.00",
        "180000.00",
    )
    removed = post(
        client, headers, f"/v1/expenses/{advance['id']}/delete", "delete", {"version": 1}
    ).json()
    may, june = report(client, headers)["months"]
    assert may["actual"] == "0.00" and june["planned_remaining"] == "100000.00"
    post(
        client,
        headers,
        f"/v1/expenses/{advance['id']}/restore",
        "restore",
        {"version": removed["version"]},
    )
    may, june = report(client, headers)["months"]
    assert may["actual"] == "100000.00" and june["planned_remaining"] == "0.00"


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


def test_price_change_move_and_rename_preserve_link(client, headers, category):
    p = make_plan(client, headers, category)
    e = make_expense(client, headers, category, plan_id=p["id"])
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
    assert response.json()["remaining"] == "170000.00"
    assert response.json()["due_month"] == "2027-05-01"
    assert client.get(f"/v1/expenses/{e['id']}", headers=headers).json()["plan_id"] == p["id"]
    assert report(client, headers)["months"][0]["known_total"] == "270000.00"
    assert (
        client.put(
            f"/v1/plans/{p['id']}", headers={**headers, "Idempotency-Key": "stale"}, json=data
        ).status_code
        == 409
    )


def test_blank_zero_undated_and_overpayment(client, headers, category):
    make_plan(client, headers, category, "unknown", expected=None)
    make_plan(client, headers, category, "zero", expected="0")
    make_plan(client, headers, category, "no-date", expected="500", due_month=None)
    make_plan(client, headers, category, "nothing-known", expected=None, due_month=None)
    result = report(client, headers)
    assert result["months"][1]["unpriced"] == 1
    assert result["months"][1]["planned_remaining"] == "0.00"
    assert result["undated_remaining"] == "500.00" and result["undated_unpriced"] == 1
    p = make_plan(client, headers, category, "over", expected="10")
    make_expense(client, headers, category, plan_id=p["id"], amount="12.50")
    view = client.get(f"/v1/plans/{p['id']}", headers=headers).json()
    assert view["remaining"] == "0.00" and view["overpaid"] == "2.50"


def test_archive_keeps_actuals_and_restore_returns_forecast(client, headers, category):
    p = make_plan(client, headers, category)
    make_expense(client, headers, category, plan_id=p["id"])
    archived = post(
        client, headers, f"/v1/plans/{p['id']}/archive", "archive", {"version": 1}
    ).json()
    assert report(client, headers)["months"][0]["actual"] == "100000.00"
    assert report(client, headers)["months"][1]["planned_remaining"] == "0.00"
    post(
        client, headers, f"/v1/plans/{p['id']}/restore", "restore", {"version": archived["version"]}
    )
    assert report(client, headers)["months"][1]["planned_remaining"] == "150000.00"


def test_delete_plan_preserves_and_unlinks_actual_payments(client, headers, category, sessions):
    plan = make_plan(client, headers, category)
    expense = make_expense(client, headers, category, plan_id=plan["id"])
    response = client.request(
        "DELETE",
        f"/v1/plans/{plan['id']}",
        headers={**headers, "Idempotency-Key": "delete-plan"},
        json={"version": plan["version"]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": plan["id"],
        "deleted": True,
        "unlinked_expenses": 1,
        "linked_expenses_preserved": True,
    }
    assert client.get(f"/v1/plans/{plan['id']}", headers=headers).status_code == 404
    saved = client.get(f"/v1/expenses/{expense['id']}", headers=headers).json()
    assert saved["plan_id"] is None and saved["amount"] == "100000.00"
    assert report(client, headers)["months"][0]["actual"] == "100000.00"
    with sessions() as db:
        entry = db.scalar(select(Audit).where(Audit.entity == "plan", Audit.action == "delete"))
        assert entry.after["linked_expenses_preserved"] is True


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
            "plan_id": p["id"],
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
    expense = make_expense(client, headers, category, plan_id=plan["id"])
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
