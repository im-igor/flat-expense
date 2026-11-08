import json
from datetime import date
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_bot import Chat

from flat_expense.api import create_app
from flat_expense.config import Settings
from flat_expense.expense_ai import AIUnavailable, LocalExpenseAI


def install_ai(client, category, **values):
    ai = Mock()
    ai.suggest.return_value = {
        "is_expense": True,
        "values": {
            "amount": "3731.15",
            "paid_on": "2026-11-06",
            "description": "Штукатурка",
            "note": "",
            "category_id": category["id"],
            **values,
        },
    }
    client.app.state.expense_ai = ai
    return ai


def test_quick_draft_review_edit_restart_confirm_and_replay(
    client, headers, category, sessions, config
):
    client.post(
        "/v1/categories",
        headers=headers,
        json={"name": "Черновые материалы", "parent_id": category["parent_id"]},
    )
    ai = install_ai(client, category)
    chat = Chat(client, headers)
    preview = chat.send("Вчера оплатил штукатурку 3 731,15")
    assert "Черновик подготовлен с ИИ" in preview["messages"][0]["text"]
    duplicate = client.post("/internal/telegram/update", headers=headers, json=chat.last_update)
    assert duplicate.json() == preview
    assert ai.suggest.call_count == 1
    assert client.get("/v1/expenses", headers=headers).json() == []
    stale = preview["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    chat.click("Изменить сумму")
    chat.send("4500,50")
    chat.click("Изменить дату")
    chat.send("05.11.2026")
    chat.click("Изменить категорию")
    chat.click("Ремонт")
    chat.click("Черновые материалы")
    chat.click("Изменить примечание")
    chat.send("Куплено по чеку")
    with TestClient(create_app(config, sessions)) as restarted:
        chat.client = restarted
        chat.send("/start")
        assert "4500" in chat.last["messages"][0]["text"].replace(" ", "")
        saved = chat.click("Подтвердить")
        assert (
            restarted.post(
                "/internal/telegram/update", headers=headers, json=chat.last_update
            ).json()
            == saved
        )
        chat.send(callback=stale)
        assert "неактуальна" in chat.last["messages"][0]["text"]
        records = restarted.get("/v1/expenses", headers=headers).json()
        assert len(records) == 1
        assert records[0]["amount"] == "4500.50"
        assert records[0]["paid_on"] == "2026-11-05"
        assert records[0]["note"] == "Куплено по чеку"
        assert records[0]["category"].endswith("Черновые материалы")


def test_missing_fields_are_asked_and_cancellation_writes_nothing(client, headers, category):
    install_ai(client, category, amount=None, paid_on=None, category_id=None)
    chat = Chat(client, headers)
    chat.send("/expense Оплатил штукатурку")
    assert "Сколько оплатили" in chat.last["messages"][0]["text"]
    chat.send("1000")
    chat.click("Сегодня")
    chat.click("Ремонт")
    chat.click("Работы")
    chat.click("Отменить")
    assert client.get("/v1/expenses", headers=headers).json() == []


def test_ai_outage_keeps_manual_flow_and_previous_draft(client, headers, category):
    ai = install_ai(client, category)
    ai.suggest.side_effect = AIUnavailable()
    chat = Chat(client, headers)
    chat.send("Расход 100 рублей")
    assert "Расход не записан" in chat.last["messages"][0]["text"]
    chat.send("/expense")
    chat.send("100")
    chat.click("Сегодня")
    chat.send("Штукатурка")
    chat.click("Пропустить")
    assert "вручную" in chat.last["messages"][0]["text"]
    chat.click("Ремонт")
    chat.click("Работы")
    chat.click("Подтвердить")
    assert len(client.get("/v1/expenses", headers=headers).json()) == 1


def test_wizard_ai_category_can_be_overridden(client, headers, category):
    client.post(
        "/v1/categories",
        headers=headers,
        json={"name": "Черновые материалы", "parent_id": category["parent_id"]},
    )
    ai = install_ai(client, category)
    chat = Chat(client, headers)
    chat.send("/expense")
    chat.send("100")
    chat.click("Сегодня")
    chat.send("Материалы")
    chat.click("Пропустить")
    assert ai.suggest.call_count == 1
    chat.click("Изменить категорию")
    chat.click("Ремонт")
    chat.click("Черновые материалы")
    chat.click("Подтвердить")
    record = client.get("/v1/expenses", headers=headers).json()[0]
    assert record["amount"] == "100.00"  # classifier cannot replace entered money
    assert record["category"].endswith("Черновые материалы")


def test_quick_entry_offers_matching_plans_as_optional_templates(client, headers, category):
    from conftest import make_plan

    plan = make_plan(client, headers, category, title="Межкомнатные двери")
    ai = install_ai(client, category)
    ai.suggest.return_value["plan_suggestions"] = [plan["id"]]
    chat = Chat(client, headers)
    preview = chat.send("Оплатил аванс за двери 100000")
    assert "Подходящие планы" in preview["messages"][0]["text"]
    assert ai.suggest.call_args.args[3] == [plan]
    chat.click("Использовать: Межкомнатные двери")
    assert "План: 250 000,00 ₽" in chat.last["messages"][0]["text"]
    chat.click("Подтвердить")
    saved = client.get("/v1/expenses", headers=headers).json()[0]
    assert saved["planned_amount"] == "250000.00"
    assert saved["category_id"] == category["id"]


def test_unauthorized_and_group_never_call_model(client, headers, category):
    ai = install_ai(client, category)
    chat = Chat(client, headers)
    assert chat.send("Расход 100", actor=999)["messages"] == []
    assert chat.send("Расход 100", group=True)["messages"] == []
    ai.suggest.assert_not_called()


def test_bot_uses_existing_dictionary_without_reintroducing_defaults(client, headers, category):
    install_ai(client, category)
    before = client.get("/v1/categories", headers=headers).json()
    Chat(client, headers).send("Штукатурка 3731,15")
    assert client.get("/v1/categories", headers=headers).json() == before


def test_plan_or_multiple_payments_do_not_become_expense(client, headers, category):
    ai = install_ai(client, category)
    ai.suggest.return_value["is_expense"] = False
    chat = Chat(client, headers)
    assert "одну фактическую" in chat.send("Куплю диван за 50000")["messages"][0]["text"]
    assert client.get("/v1/expenses", headers=headers).json() == []


def test_failed_quick_replacement_preserves_existing_draft(client, headers, category):
    ai = install_ai(client, category)
    chat = Chat(client, headers)
    chat.send("Штукатурка 3731,15")
    ai.suggest.side_effect = AIUnavailable()
    chat.send("/expense Другая оплата 2000")
    chat.send("/start")
    assert "Штукатурка" in chat.last["messages"][0]["text"]
    chat.click("Подтвердить")
    assert client.get("/v1/expenses", headers=headers).json()[0]["amount"] == "3731.15"


def provider(monkeypatch, config, doc_vectors=None, query_vector=None, status=200, body=None):
    config.expense_ai_enabled = True
    requests = []
    doc_vectors = doc_vectors if doc_vectors is not None else [[1.0, 0.0]]
    query_vector = query_vector if query_vector is not None else [1.0, 0.05]

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        vectors = [query_vector] if payload["input"][0].startswith("task:") else doc_vectors
        return httpx.Response(status, json=body if body is not None else {"embeddings": vectors})

    constructor = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: constructor(transport=httpx.MockTransport(respond), **kw)
    )
    return LocalExpenseAI(config), requests


CATEGORIES = [{"id": "owned-id", "path": "Ремонт → Работы"}]


def test_adapter_matches_categories_and_caches_only_dictionary(monkeypatch, config):
    ai, requests = provider(monkeypatch, config)
    result = ai.suggest("Вчера штукатурка 3731,15", CATEGORIES, date(2026, 11, 7))
    assert result["values"]["amount"] == "3731.15"
    assert result["values"]["paid_on"] == "2026-11-06"
    assert result["values"]["category_id"] == "owned-id"
    ai.suggest("Оплатил рабочему 5000 рублей", CATEGORIES, date(2026, 11, 7))
    assert len(requests) == 3
    wire = json.dumps(requests)
    assert config.api_token.get_secret_value() not in wire
    assert "owned-id" not in wire
    assert "3731" not in wire
    assert all(r["truncate"] is False for r in requests)


def test_adapter_suggests_matching_plans_without_sending_ids(monkeypatch, config):
    config.expense_ai_enabled = True
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload["input"]
        if inputs[-1].startswith("task: match"):
            return httpx.Response(200, json={"embeddings": [[1, 0], [0, 1], [1, 0]]})
        if inputs[0].startswith("task:"):
            return httpx.Response(200, json={"embeddings": [[1, 0]]})
        return httpx.Response(200, json={"embeddings": [[1, 0]]})

    constructor = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: constructor(transport=httpx.MockTransport(respond), **kw)
    )
    result = LocalExpenseAI(config).suggest(
        "Оплатил двери 100000",
        CATEGORIES,
        date(2026, 11, 7),
        [
            {"id": "doors-id", "title": "Межкомнатные двери", "category": "Ремонт → Работы"},
            {"id": "sofa-id", "title": "Диван", "category": "Покупки → Мебель"},
        ],
    )
    assert result["plan_suggestions"] == ["doors-id"]
    wire = json.dumps(requests)
    assert "doors-id" not in wire and "sofa-id" not in wire


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"embeddings": []},
        {"embeddings": [[0, 0]]},
        {"embeddings": [["x", 1]]},
        {"embeddings": [[True, 0]]},
    ],
)
def test_bad_model_output_fails_closed(monkeypatch, config, body):
    ai, _ = provider(monkeypatch, config, body=body)
    with pytest.raises(AIUnavailable):
        ai.suggest("Оплата 100", CATEGORIES, date(2026, 11, 7))


def test_failed_model_response(monkeypatch, config):
    ai, _ = provider(monkeypatch, config, status=503)
    with pytest.raises(AIUnavailable):
        ai.suggest("Оплата 100", CATEGORIES, date(2026, 11, 7))


def test_embedding_dimensions_must_match(monkeypatch, config):
    ai, _ = provider(monkeypatch, config, query_vector=[1, 2, 3])
    with pytest.raises(AIUnavailable):
        ai.suggest("Оплата 100", CATEGORIES, date(2026, 11, 7))


def test_low_similarity_asks_user(monkeypatch, config):
    ai, _ = provider(monkeypatch, config, query_vector=[0, 1])
    result = ai.suggest("Оплатил что-то 100", CATEGORIES, date(2026, 11, 7))
    assert "category_id" not in result["values"]


def test_nearly_equal_categories_ask_user(monkeypatch, config):
    ai, _ = provider(monkeypatch, config, doc_vectors=[[1, 0], [0.99, 0.01]], query_vector=[1, 0])
    result = ai.suggest(
        "Оплата 100", [*CATEGORIES, {"id": "other", "path": "Ремонт → Прочее"}], date(2026, 11, 7)
    )
    assert "category_id" not in result["values"]


def test_future_and_multiple_payments_skip_inference(monkeypatch, config):
    ai, requests = provider(monkeypatch, config)
    assert not ai.suggest("Куплю диван 1000", CATEGORIES, date(2026, 11, 7))["is_expense"]
    assert not ai.suggest("Купил стул 1000 и стол 2000", CATEGORIES, date(2026, 11, 7))[
        "is_expense"
    ]
    assert requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://external.example",
        "http://evil.example",
        "http://localhost?token=x",
        "http://user:pass@localhost",
        "http://localhost/extra",
    ],
)
def test_local_only_url(config, url):
    with pytest.raises(ValidationError):
        Settings(api_token=config.api_token, allowed_telegram_user_ids="111", expense_ai_url=url)
