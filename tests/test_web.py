import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flat_expense.api import create_app
from flat_expense.config import Settings
from flat_expense.web import COOKIE, password_hash, signature

TOKEN = "web-test-token-with-at-least-32-characters"
ORIGIN = "http://127.0.0.1:8317"
WRITE = {"Origin": ORIGIN, "X-Flat-Expense": "web"}


@pytest.fixture
def web_config():
    return Settings(api_token=TOKEN, allowed_telegram_user_ids="111,222", web_enabled=True)


@pytest.fixture
def browser(sessions, web_config):
    with TestClient(create_app(web_config, sessions), base_url=ORIGIN) as client:
        yield client


def test_web_requires_opt_in_and_uses_single_account(sessions):
    config = Settings(api_token=TOKEN, allowed_telegram_user_ids="111,222", web_enabled=True)
    with TestClient(create_app(config, sessions), base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/v1/expenses").status_code == 200
    off = Settings(api_token=TOKEN, allowed_telegram_user_ids="111")
    with TestClient(create_app(off, sessions), base_url=ORIGIN) as client:
        assert client.get("/").status_code == 401
        assert client.get("/assets/app.js").status_code == 401
        assert client.get("/health").status_code == 200


def test_root_issues_scoped_cookie_without_service_credential(browser):
    page = browser.get("/")
    assert page.status_code == 200
    assert TOKEN not in page.text
    assert "HttpOnly" in page.headers["set-cookie"]
    assert "SameSite=strict" in page.headers["set-cookie"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert browser.get("/web/bootstrap").status_code == 200
    assert browser.get("/assets/app.js").status_code == 200
    assert browser.get("/assets/style.css").status_code == 200
    assert browser.get("/assets/editor.css").status_code == 200
    assert browser.get("/assets/overview.css").status_code == 200
    assert browser.get("/assets/theme.css").status_code == 200
    assert 'id="theme-toggle"' in page.text
    assert "initTheme" in browser.get("/assets/app.js").text
    assert browser.get("/assets/.env").status_code == 404
    assert browser.get("/openapi.json").status_code == 401
    assert browser.get("/internal/telegram/cursor").status_code == 401


def test_public_web_requires_password_and_supports_logout(sessions):
    config = Settings(
        api_token=TOKEN,
        allowed_telegram_user_ids="111",
        web_enabled=True,
        web_public=True,
        web_password_hash=password_hash("correct horse battery staple", b"0" * 16),
        web_allowed_hosts="expenses.example,127.0.0.1",
    )
    with TestClient(create_app(config, sessions), base_url=ORIGIN) as client:
        page = client.get("/", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login"
        assert client.get("/login").status_code == 200
        assert client.get("/v1/expenses").status_code == 401
        headers = {"Origin": ORIGIN, "X-Flat-Expense": "login"}
        assert (
            client.post("/web/login", headers=headers, json={"password": "wrong"}).status_code
            == 401
        )
        signed_in = client.post(
            "/web/login", headers=headers, json={"password": "correct horse battery staple"}
        )
        assert signed_in.status_code == 200
        assert "HttpOnly" in signed_in.headers["set-cookie"]
        assert client.get("/").status_code == 200
        assert client.get("/v1/expenses").status_code == 200
        assert client.post("/web/logout", headers=WRITE).status_code == 200
        assert client.get("/v1/expenses").status_code == 401


def test_public_web_rejects_unlisted_host_and_missing_password(sessions):
    with pytest.raises(ValidationError):
        Settings(
            api_token=TOKEN,
            allowed_telegram_user_ids="111",
            web_enabled=True,
            web_public=True,
        )
    config = Settings(
        api_token=TOKEN,
        allowed_telegram_user_ids="111",
        web_enabled=True,
        web_public=True,
        web_password_hash=password_hash("correct horse battery staple", b"1" * 16),
        web_allowed_hosts="127.0.0.1",
    )
    with TestClient(create_app(config, sessions), base_url=ORIGIN) as client:
        assert client.get("/login", headers={"Host": "attacker.example"}).status_code == 403


def test_browser_auth_blocks_missing_tampered_and_expired_cookies(browser, web_config):
    assert browser.get("/v1/expenses").status_code == 401
    browser.get("/")
    original = browser.cookies.get(COOKIE)
    browser.cookies.clear()
    browser.cookies.set(COOKIE, original + "x")
    assert browser.get("/v1/expenses").status_code == 401
    browser.cookies.clear()
    payload = f"1:{int(time.time()) - 1}:nonce"
    browser.cookies.set(COOKIE, payload + ":" + signature(payload, web_config))
    assert browser.get("/v1/expenses").status_code == 401


def test_browser_blocks_rebinding_and_cross_origin_writes(browser):
    assert browser.get("/", headers={"Host": "attacker.example"}).status_code == 403
    browser.get("/")
    assert browser.get("/v1/expenses", headers={"Host": "attacker.example"}).status_code == 403
    for headers in (
        {},
        {"Origin": "https://attacker.example", "X-Flat-Expense": "web"},
        {"Origin": ORIGIN},
        {"Origin": "null", "X-Flat-Expense": "web"},
    ):
        assert browser.post("/v1/categories/seed", headers=headers).status_code == 403
    assert browser.post("/v1/categories/seed", headers=WRITE).status_code == 200


def test_browser_cannot_choose_another_owner_or_use_telegram_internal(browser):
    browser.get("/")
    own = browser.post("/v1/categories", headers=WRITE, json={"name": "Личная"}).json()
    forged = browser.get("/v1/categories", headers={"X-Telegram-User-Id": "222"}).json()
    assert [c["id"] for c in forged] == [own["id"]]
    other = browser.get(
        "/v1/categories", headers={"Authorization": "Bearer " + TOKEN, "X-Telegram-User-Id": "222"}
    ).json()
    assert [item["id"] for item in other] == [own["id"]]
    assert (
        browser.post("/internal/telegram/update", headers=WRITE, json={"update_id": 1}).status_code
        == 401
    )
    assert (
        browser.get(
            "/v1/categories", headers={"Authorization": "Bearer wrong", "X-Telegram-User-Id": "111"}
        ).status_code
        == 401
    )


def test_browser_plan_and_payment_are_independent_and_repeat_is_idempotent(browser):
    browser.get("/")
    category = browser.post("/v1/categories", headers=WRITE, json={"name": "Материалы"}).json()
    plan = browser.post(
        "/v1/plans",
        headers={**WRITE, "Idempotency-Key": "web-plan"},
        json={
            "title": "Двери",
            "category_id": category["id"],
            "expected": "250000",
            "agreed": "280000",
            "due_month": "2027-06-01",
        },
    ).json()
    payment = {
        "paid_on": "2027-05-15",
        "amount": "100000",
        "category_id": category["id"],
        "description": "Аванс",
        "note": "",
    }
    first = browser.post(
        "/v1/expenses", headers={**WRITE, "Idempotency-Key": "web-payment"}, json=payment
    )
    assert first.status_code == 200
    assert (
        browser.post(
            "/v1/expenses", headers={**WRITE, "Idempotency-Key": "web-payment"}, json=payment
        ).json()
        == first.json()
    )
    assert len(browser.get("/v1/expenses").json()) == 1
    report = browser.get("/v1/summary?start=2027-05-01&end=2027-06-01").json()
    assert report["months"][0]["actual"] == "100000.00"
    assert report["months"][1]["planned_remaining"] == "280000.00"
    item = first.json()
    assert (
        browser.post(
            f"/v1/expenses/{item['id']}/delete",
            headers={**WRITE, "Idempotency-Key": "web-delete"},
            json={"version": item["version"]},
        ).status_code
        == 200
    )
    saved_plan = browser.get(f"/v1/plans/{plan['id']}").json()
    assert saved_plan["agreed"] == "280000.00"


def test_web_editor_supports_inline_categories_without_purchase_links(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert "Создать категорию 1" in asset
    assert "Создать категорию 2" in asset
    assert "Связать с покупкой" not in asset
    assert "Оплаты по этой покупке" not in asset


def test_ledger_supports_reviewing_bot_expenses_with_drag_and_drop(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    css = browser.get("/assets/style.css").text
    assert "needs_review" in asset
    assert "resolve-expense-review" in asset
    assert "apply-to-plan" in asset
    assert "dragstart" in asset and "plan-drop-target" in asset
    assert "needs-review-row" in css


def test_ledger_can_edit_total_paid_amount_of_a_plan(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert "edit-plan-paid-amount" in asset
    assert "/paid-amount" in asset
    assert "Ноль отменит все внесённые оплаты" in asset


def test_ledger_has_quick_plan_finalization(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert 'data-action="finalize-plan"' in asset
    assert "/finalize" in asset


def test_overview_has_loan_scope_and_hover_breakdown(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert "chart-loan-mode" in asset
    assert "chart-tooltip" in asset
    assert "actual_without_loan" in asset
    assert "planned_without_loan" in asset
    assert "month-total" in asset
    assert "total.plan" in asset
    assert "total.fact" in asset
    assert "total.paid" in asset
    assert "total.remaining" in asset


def test_ledger_pagination_keeps_month_groups_together(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert "function ledgerPages(rows)" in asset
    assert "page.length+group.rows.length>PAGE_SIZE" in asset
    assert "pages[state.ledgerPage]" in asset
    assert "pages.length)}" in asset
    assert "data-page-select" in asset
    assert "Перейти к странице" in asset
    assert "const current = Math.min(Math.max(0, page), pages - 1)" in asset


def test_footer_does_not_offer_a_manual_data_reload(browser):
    page = browser.get("/").text
    asset = browser.get("/assets/app.js").text
    assert "Обновить список" not in page
    assert 'data-action="refresh"' not in asset


def test_ledger_has_dependent_category_filters(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert 'id="ledger-category1"' in asset
    assert 'id="ledger-category2"' in asset
    assert "category=f.category2||f.category1" in asset
    assert "if(key==='category1')state.ledgerFilters.category2=''" in asset


def test_ledger_shows_paid_values_missing_budgets_and_filtered_summary(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert (
        "total.plan+=cents(row.item.planned_amount);total.fact+=amount;total.paid+=amount" in asset
    )
    assert "editableValue('expense',item,'planned_amount'" in asset
    assert (
        "planned_amount:moneyInput($('#planned_amount').value)||moneyInput($('#amount').value,true)"
        in asset
    )
    assert "if(!plannedTouched)planned.value=amount.value" in asset
    assert "budget-missing-cell" in asset
    assert "function ledgerSummary(rows)" in asset
    assert "Оплачено до сегодня" in asset
    assert "Итоги отображённых строк" in asset
    assert 'id="ledger-loan"' in asset
    assert "LOAN_CATEGORY_ID" in asset
    assert "ledger-type" not in asset
    assert "<th>Дата</th><th>Категория 1</th><th>Категория 2</th><th>Описание</th>" in asset


def test_ledger_removes_cumulative_month_totals(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    css = browser.get("/assets/style.css").text
    assert "function ledgerRows(filters=state.ledgerFilters)" in asset
    assert "function ledgerCumulativeTotals(rows)" not in asset
    assert "На начало месяца" not in asset
    assert "month-cumulative" not in css
    assert "ledger-summary" in css


def test_ledger_opens_at_current_month_and_supports_bulk_plan_deadlines(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    assert "function ledgerCurrentMonthPage(pages)" in asset
    assert "state.ledgerAutoPosition&&ledgerHasDefaultFilters()" in asset
    assert 'data-action="select-filtered-plans"' in asset
    assert 'data-action="bulk-move-plans"' in asset
    assert "/v1/plans/bulk-due-month" in asset
    assert 'data-action="record-plan-payment"' in asset
    assert "Точная дата оплаты" in asset


def test_overview_focuses_on_current_and_next_month_plans(browser):
    browser.get("/")
    asset = browser.get("/assets/app.js").text
    css = browser.get("/assets/overview.css").text
    assert "Последние оплаты" not in asset
    assert "Текущий незаконченный месяц" in asset
    assert "Следующий полный месяц" in asset
    assert "upcoming-grid" in asset and ".upcoming-grid" in css
    assert ".page-header.compact" in css
