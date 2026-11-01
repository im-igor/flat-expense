import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from flat_expense.api import create_app
from flat_expense.config import Settings
from flat_expense.models import Base

TOKEN = "test-only-token-not-used-in-production-123456789"


@pytest.fixture
def sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        if not (make_url(url).database or "").endswith("_test"):
            raise RuntimeError(
                "TEST_DATABASE_URL must target a disposable database ending in _test"
            )
        engine = create_engine(url, hide_parameters=True)
    else:
        engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def config():
    return Settings(api_token=TOKEN, allowed_telegram_user_ids="111,222")


@pytest.fixture
def client(sessions, config):
    with TestClient(create_app(config, sessions)) as result:
        yield result


@pytest.fixture
def headers():
    return {"Authorization": "Bearer " + TOKEN, "X-Telegram-User-Id": "111"}


@pytest.fixture
def category(client, headers):
    root = client.post("/v1/categories", headers=headers, json={"name": "Ремонт"}).json()
    return client.post(
        "/v1/categories", headers=headers, json={"name": "Работы", "parent_id": root["id"]}
    ).json()


def post(client, headers, path, key, data):
    return client.post(path, headers={**headers, "Idempotency-Key": key}, json=data)


def make_plan(client, headers, category, key="plan1", **changes):
    data = {
        "title": "Двери",
        "category_id": category["id"],
        "expected": "250000.00",
        "due_month": "2027-06-01",
        **changes,
    }
    result = post(client, headers, "/v1/plans", key, data)
    assert result.status_code == 200, result.text
    return result.json()


def make_expense(client, headers, category, key="expense1", **changes):
    data = {
        "description": "Аванс за двери",
        "category_id": category["id"],
        "amount": "100000.00",
        "paid_on": "2027-05-15",
        **changes,
    }
    result = post(client, headers, "/v1/expenses", key, data)
    assert result.status_code == 200, result.text
    return result.json()


def report(client, headers):
    response = client.get("/v1/summary?start=2027-05-01&end=2027-06-01", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()
