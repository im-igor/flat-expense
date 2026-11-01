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
    return Settings(
        api_token=TOKEN, allowed_telegram_user_ids="111,222", web_enabled=True, web_owner_id=111
    )


@pytest.fixture
def browser(sessions, web_config):
    with TestClient(create_app(web_config, sessions), base_url=ORIGIN) as client:
        yield client


def test_web_requires_opt_in_and_unambiguous_owner(sessions):
    with pytest.raises(ValidationError):
        Settings(api_token=TOKEN, allowed_telegram_user_ids="111,222", web_enabled=True)
    with pytest.raises(ValidationError):
        Settings(
            api_token=TOKEN, allowed_telegram_user_ids="111", web_enabled=True, web_owner_id=222
        )
    config = Settings(
        api_token=TOKEN, allowed_telegram_user_ids="111", web_enabled=True, web_owner_id=""
    )
    assert config.web_owner_id == 111
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
    payload = f"111:{int(time.time()) - 1}:nonce"
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
    assert other == []
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


def test_browser_purchase_advance_and_repeat_do_not_double_count(browser):
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
        "plan_id": plan["id"],
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
    assert report["months"][1]["planned_remaining"] == "180000.00"
    item = first.json()
    assert (
        browser.post(
            f"/v1/expenses/{item['id']}/delete",
            headers={**WRITE, "Idempotency-Key": "web-delete"},
            json={"version": item["version"]},
        ).status_code
        == 200
    )
    assert browser.get(f"/v1/plans/{plan['id']}").json()["remaining"] == "280000.00"
