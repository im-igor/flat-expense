from conftest import make_plan
from fastapi.testclient import TestClient

from flat_expense.api import create_app


class Chat:
    def __init__(self, client, headers):
        self.client, self.headers, self.sequence = client, headers, 1000

    def send(self, text=None, callback=None, actor=111, group=False):
        self.sequence += 1
        msg = {
            "message_id": self.sequence,
            "date": 1,
            "from": {"id": actor},
            "chat": {"id": -1 if group else actor, "type": "group" if group else "private"},
        }
        update = {"update_id": self.sequence}
        if callback:
            update["callback_query"] = {
                "id": str(self.sequence),
                "from": {"id": actor},
                "message": msg,
                "data": callback,
            }
        else:
            update["message"] = {**msg, "text": text}
        response = self.client.post("/internal/telegram/update", headers=self.headers, json=update)
        assert response.status_code == 200, response.text
        self.last_update, self.last = update, response.json()
        return self.last

    def click(self, label):
        buttons = [
            b
            for m in self.last["messages"]
            for row in m.get("reply_markup", {}).get("inline_keyboard", [])
            for b in row
        ]
        target = next(b for b in buttons if b["text"] == label)
        return self.send(callback=target["callback_data"])


def test_full_expense_wizard_restarts_and_replay(client, headers, sessions, config):
    chat = Chat(client, headers)
    chat.send("/expense")
    chat.send("3 731,15")
    chat.send("31.05.2027")
    chat.send("Шпаклёвка")
    chat.click("Пропустить")
    chat.click("Ремонт")
    chat.click("Черновые материалы")
    # A new API instance reads the existing draft from the database.
    with TestClient(create_app(config, sessions)) as restarted:
        chat.client = restarted
        confirm = chat.last["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        saved = chat.click("Подтвердить")
        duplicate = restarted.post(
            "/internal/telegram/update", headers=headers, json=chat.last_update
        )
        assert duplicate.json() == saved
        chat.send(callback=confirm)
        assert "неактуальна" in chat.last["messages"][0]["text"]
        expenses = restarted.get("/v1/expenses", headers=headers).json()
        assert len(expenses) == 1
        assert expenses[0]["amount"] == "3731.15" and expenses[0]["paid_on"] == "2027-05-31"
        assert expenses[0]["planned_amount"] == "3731.15"
        assert expenses[0]["needs_review"] is True


def test_plan_and_expense_creation_are_independent(client, headers, category):
    chat = Chat(client, headers)
    chat.send("/plan")
    chat.send("Двери")
    chat.send("280000")
    chat.send("06.2027")
    chat.click("Ремонт")
    chat.click("Работы")
    chat.click("Пропустить")
    chat.click("Подтвердить")
    chat.send("/expense")
    chat.send("100000")
    chat.send("15.05.2027")
    chat.send("Аванс за двери")
    chat.click("Пропустить")
    chat.click("Ремонт")
    chat.click("Работы")
    chat.click("Подтвердить")
    plans = client.get("/v1/plans", headers=headers).json()
    expenses = client.get("/v1/expenses", headers=headers).json()
    assert plans[0]["expected"] == "280000.00"
    assert expenses[0]["amount"] == "100000.00"
    assert expenses[0]["needs_review"] is True


def test_bot_rejects_other_users_groups_and_stale_category(client, headers):
    chat = Chat(client, headers)
    assert chat.send("/start", actor=333)["messages"] == []
    assert chat.send("/start", group=True)["messages"] == []
    chat.send("/expense")
    chat.send("1.001")
    assert "двух знаков" in chat.last["messages"][0]["text"]
    chat.send("100")
    chat.click("Сегодня")
    chat.send("Расход")
    chat.click("Пропустить")
    stale = chat.last["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    chat.click("Ремонт")
    chat.send(callback=stale)
    assert "неактуальна" in chat.last["messages"][0]["text"]


def test_unknown_plan_can_be_priced_and_moved(client, headers, category):
    p = make_plan(client, headers, category, expected=None, due_month=None)
    chat = Chat(client, headers)
    chat.send(callback="vp:" + p["id"])
    chat.click("Фактическая цена")
    chat.send("1000")
    chat.click("Подтвердить")
    chat.click("Месяц расхода")
    chat.send("07.2027")
    chat.click("Подтвердить")
    saved = client.get("/v1/plans/" + p["id"], headers=headers).json()
    assert saved["agreed"] == "1000.00" and saved["due_month"] == "2027-07-01"


def test_telegram_relay_uses_https_for_methods_and_files():
    import pytest
    from aiogram.client.telegram import TelegramAPIServer
    from pydantic import ValidationError

    from flat_expense.config import Settings

    fields = {
        "api_token": "test-only-api-token-with-more-than-32-characters",
        "allowed_telegram_user_ids": "111",
    }
    config = Settings(**fields, telegram_api_root="https://relay.example.org:9443/")
    server = TelegramAPIServer.from_base(config.telegram_api_root)
    assert (
        server.api_url("fake-token", "getMe")
        == "https://relay.example.org:9443/botfake-token/getMe"
    )
    assert (
        server.file_url("fake-token", "documents/file.pdf")
        == "https://relay.example.org:9443/file/botfake-token/documents/file.pdf"
    )
    for value in (
        "http://relay.example.org",
        "https://user:password@relay.example.org",
        "https://relay.example.org/extra",
        "https://relay.example.org?token=value",
        "https://relay.example.org#fragment",
        "https://relay.example.org:0",
        "https://relay.example.org:99999",
        "",
    ):
        with pytest.raises(ValidationError):
            Settings(**fields, telegram_api_root=value)
