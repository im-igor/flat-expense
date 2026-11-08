"""Local semantic classifier. Only a category proposal; no SQL or generated money."""

import logging
import math
import re
from datetime import date
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from flat_expense.category_hints import CATEGORY_HINTS
from flat_expense.expense_text import extract

log = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """Safe failure without prompts, provider responses, or credentials."""


def normalize_query(text):
    return re.sub(
        r"[\d.,]+|\b(?:оплатил|оплатила|заплатил|заплатила|купил|купила|перевёл|вчера|сегодня|рублей|руб|тысяч|полная|стоимость)\b",
        " ",
        text,
        flags=re.I,
    ).strip()


def unit_vector(vector):
    if not isinstance(vector, list) or not vector or len(vector) > 4096:
        raise ValueError("Invalid embedding")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
        raise ValueError("Invalid embedding values")
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm")
    return tuple(x / norm for x in vector)


def lexical_bonus(query, path):
    """Exact subject terms complement embeddings for short product names and abbreviations."""
    stop = {"опла", "запл", "купи", "поку", "рубл", "квар", "стои", "полу", "сего", "вчер"}

    def terms(text):
        words = re.findall(r"[а-яёa-z]+", text.lower())
        return {w[:4] for w in words if (len(w) >= 4 or w == "ук") and w[:4] not in stop}

    shared = terms(query) & terms(path + " " + CATEGORY_HINTS.get(path, ""))
    return 0.06 * min(len(shared), 3)


class LocalExpenseAI:
    def __init__(self, config):
        self.config = config

    def embed(self, texts):
        payload = {
            "model": self.config.expense_ai_model,
            "input": texts,
            "truncate": False,
            "keep_alive": "15m",
            "options": {"num_ctx": 2048, "num_thread": 2},
        }
        with httpx.Client(timeout=self.config.expense_ai_timeout, trust_env=False) as client:
            response = client.post(self.config.expense_ai_url + "/api/embed", json=payload)
            response.raise_for_status()
        body = response.json()
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("embeddings"), list)
            or len(body["embeddings"]) != len(texts)
        ):
            raise ValueError("Invalid embeddings response")
        return tuple(unit_vector(v) for v in body["embeddings"])

    @lru_cache(maxsize=32)
    def category_vectors(self, paths):
        # Cache only category descriptions, never messages or expense history.
        return self.embed(
            ["title: " + path + " | text: " + CATEGORY_HINTS.get(path, path) for path in paths]
        )

    def suggest(self, text: str, categories: list[dict], today: date) -> dict:
        if not self.config.expense_ai_enabled or not text.strip() or len(text) > 4000:
            raise AIUnavailable()
        draft = extract(text, today)
        if not draft["is_expense"]:
            return draft
        parents = {c.get("parent_id") for c in categories if c.get("parent_id")}
        choices = {c["path"]: c["id"] for c in categories if c["id"] not in parents}
        query = normalize_query(text)
        if not choices or not query:
            return draft
        try:
            docs = self.category_vectors(tuple(choices))
            vector = self.embed(["task: classification | query: " + query])[0]
            if any(len(doc) != len(vector) for doc in docs):
                raise ValueError("Embedding dimensions differ")
            ranked = sorted(
                (
                    (
                        sum(x * y for x, y in zip(vector, doc, strict=True))
                        + lexical_bonus(query, path),
                        path,
                    )
                    for doc, path in zip(docs, choices, strict=True)
                ),
                reverse=True,
            )
            score, path = ranked[0]
            margin = score - ranked[1][0] if len(ranked) > 1 else score
            if (
                score >= self.config.expense_ai_min_similarity
                and margin >= self.config.expense_ai_min_margin
            ):
                draft["values"]["category_id"] = choices[path]
            return draft
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            log.warning("Локальный ИИ недоступен или вернул некорректный ответ.")
            raise AIUnavailable() from None


def validate_local_url(value: str) -> str:
    value = value.strip().rstrip("/")
    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname not in {"expense-ai", "localhost", "127.0.0.1", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path
        or url.query
        or url.fragment
        or (url.port is not None and not 1 <= url.port <= 65535)
    ):
        raise ValueError(
            "EXPENSE_AI_URL должен указывать на локальный Ollama (expense-ai/localhost)."
        )
    return value
