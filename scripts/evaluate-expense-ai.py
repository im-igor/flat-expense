"""Synthetic acceptance set. Does not read or write the expense database."""

import json
import time
from datetime import date

from flat_expense.config import Settings
from flat_expense.expense_ai import AIUnavailable, LocalExpenseAI

CATEGORIES = [
    {"id": path, "path": path}
    for path in [
        "Ремонт → Работы",
        "Ремонт → Черновые материалы",
        "Ремонт → Покупки",
        "Ремонт → Дизайн",
        "Ремонт → Климат",
        "Ремонт → Перепланировка",
        "Ремонт → Приемка",
        "Ремонт → Авторский надзор",
        "Ремонт → Прочее",
        "Коммуналка → Кап ремонт",
        "Коммуналка → Коммуналка от УК",
        "Сопровождение сделки → Приемка",
        "Сопровождение сделки → Страховка",
        "Сопровождение сделки → Аккредитив",
        "Сопровождение сделки → Гос пошлина",
        "Сопровождение сделки → Оценка",
        "Техника → Кухня",
        "Техника → Санузел / постирочная",
        "Техника → Гостиная / Общее пространство",
        "Техника → Климат",
        "Техника → Общее",
    ]
]
CASES = [
    (
        "Вчера заплатил мастеру за штукатурные работы 3 731,15 рублей",
        "3731.15",
        "2026-11-06",
        "Ремонт → Работы",
    ),
    (
        "Купил 10 мешков штукатурки за 4500 рублей сегодня",
        "4500.00",
        "2026-11-07",
        "Ремонт → Черновые материалы",
    ),
    ("Оплатил диван 50 тысяч рублей", "50000.00", "2026-11-07", "Ремонт → Покупки"),
    ("05.11.2026 оплатил холодильник 85000 руб", "85000.00", "2026-11-05", "Техника → Кухня"),
    (
        "Коммуналка от УК за октябрь оплачена сегодня, 6500,50 рублей",
        "6500.50",
        "2026-11-07",
        "Коммуналка → Коммуналка от УК",
    ),
    (
        "Заплатил за страховку квартиры 7200 рублей",
        "7200.00",
        "2026-11-07",
        "Сопровождение сделки → Страховка",
    ),
    (
        "Аванс за работы 20000 рублей, полная стоимость работ 80000",
        "20000.00",
        "2026-11-07",
        "Ремонт → Работы",
    ),
    ("Оплатил дизайнеру проект квартиры", None, "2026-11-07", "Ремонт → Дизайн"),
    ("За разработку чертежей интерьера перевёл 25000", "25000.00", "2026-11-07", "Ремонт → Дизайн"),
    (
        "45000 за автомат для стирки белья",
        "45000.00",
        "2026-11-07",
        "Техника → Санузел / постирочная",
    ),
    ("Платёж в фонд капремонта 1800", "1800.00", "2026-11-07", "Коммуналка → Кап ремонт"),
    ("Купил 8 банок грунта, 6000 руб", "6000.00", "2026-11-07", "Ремонт → Черновые материалы"),
    ("Планирую купить диван за 50000 рублей", False, None, None),
    ("Оплатил мастеру 2000 рублей и купил цемент за 3000 рублей", False, None, None),
]


def main():
    # Explicit test credentials: no production configuration or database access required.
    config = Settings(
        api_token="synthetic-evaluation-token-not-for-production",
        allowed_telegram_user_ids="111",
        expense_ai_enabled=True,
    )
    ai = LocalExpenseAI(config)
    results = []
    for text, amount, paid_on, category_id in CASES:
        started = time.monotonic()
        try:
            result = ai.suggest(text, CATEGORIES, date(2026, 11, 7))
            v = result["values"]
            passed = (
                not result["is_expense"]
                if amount is False
                else (
                    result["is_expense"]
                    and v.get("amount") == amount
                    and v.get("paid_on") == paid_on
                    and v.get("category_id") in (category_id, None)
                )
            )
        except AIUnavailable:
            result, passed = {"error": "unavailable"}, False
        row = {
            "text": text,
            "passed": passed,
            "seconds": round(time.monotonic() - started, 2),
            "result": result,
            "automatic": bool(
                passed
                and amount is not False
                and result.get("values", {}).get("category_id") == category_id
            ),
            "manual": bool(
                passed
                and amount is not False
                and result.get("values", {}).get("category_id") is None
            ),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(
        json.dumps(
            {
                "passed": sum(r["passed"] for r in results),
                "total": len(results),
                "model": config.expense_ai_model,
                "automatic": sum(r["automatic"] for r in results),
                "manual": sum(r["manual"] for r in results),
            }
        ),
        flush=True,
    )
    return (
        0 if all(r["passed"] for r in results) and sum(r["automatic"] for r in results) >= 8 else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
