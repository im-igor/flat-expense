"""Durable bot conversations. No Telegram network calls in this module."""

import re
from datetime import date
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from flat_expense import service as s
from flat_expense.expense_ai import AIUnavailable
from flat_expense.expense_text import extract_leading_payment
from flat_expense.models import Category, Draft, Expense, Plan
from flat_expense.schemas import ExpenseIn, PlanIn


def button(text, callback):
    return {"text": text[:60], "callback_data": callback}


def message(text, rows=None):
    result = {"text": text}
    if rows:
        result["reply_markup"] = {"inline_keyboard": rows}
    return result


def rub(value):
    if value is None:
        return "неизвестно"
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def expense_card(item):
    return (
        f"{'Удалённый расход' if item['deleted'] else 'Расход'}\n{item['description']}\n"
        f"План: {rub(item['planned_amount'])}\nОплачено: {rub(item['amount'])} · {item['paid_on']}\n"
        f"{item['category']}\n"
        f"{item['note']}"
    )


def plan_card(item):
    status = {
        "planned": "План",
        "partial": "Частично оплачено",
        "paid": "Оплачено",
        "finalized": "Финализировано",
    }.get(item.get("payment_status"), "План")
    return (
        f"{'Архив' if item['archived'] else 'План'}: {item['title']}\n{item['category']}\n"
        f"Плановая сумма: {rub(item['planned_amount'])}\nОплачено: {rub(item['paid_amount'])}"
        f" · Осталось: {rub(item['remaining_amount'])}\nСтатус: {status}\n"
        f"Месяц расхода: {(item['due_month'] or 'без даты')[:7]}\n{item['note']}"
    )


class Conversation:
    def __init__(self, db, owner, ai=None):
        self.db, self.owner = db, owner
        self.ai = ai
        self.draft = db.scalar(select(Draft).where(Draft.owner_id == owner).with_for_update())
        if self.draft is None:
            self.draft = Draft(owner_id=owner, data={})
            db.add(self.draft)
            db.flush()
        self.data = dict(self.draft.data)

    def save(self):
        self.data["nonce"] = uuid4().hex[:8]
        self.draft.data = dict(self.data)

    def clear(self):
        self.data = {}
        self.draft.data = {}

    def ensure_categories(self):
        if (
            self.db.scalar(select(Category.id).where(Category.owner_id == self.owner).limit(1))
            is None
        ):
            s.seed_categories(self.db, self.owner)

    def cb(self, label, value):
        return button(label, f"d:{self.data['nonce']}:{value}")

    def home(self):
        return message(
            "Быстрый ввод: сначала сумма, затем описание. Например: «3 731,15 штукатурка и 3 мешка клея». "
            "Бот сразу предложит категории из планов; перед записью проверьте карточку.\n\n"
            + "Учёт расходов на квартиру\n\nКаждая оплата хранится отдельно. План и фактическая цена покупки не считаются оплатой.\n\n/expense — расход\n/plan — будущая покупка\n/expenses — последние расходы\n/plans — будущие планы\n/month — текущий месяц (или /month 2027-06)\n/forecast — прогноз\n/undated — планы без даты\n/deleted — удалённые расходы\n/archive — архив планов\n/categories — категории\n/cancel — отменить ввод",
            [
                [button("Записать расход", "new:expense"), button("Добавить план", "new:plan")],
                [button("Расходы", "list:expenses:0"), button("Планы", "list:plans:0")],
                [button("Этот месяц", "month"), button("Прогноз", "forecast")],
            ],
        )

    def start(self, mode):
        self.data = {
            "mode": mode,
            "step": "amount" if mode == "expense" else "title",
            "values": {"note": ""},
        }
        self.save()
        return self.prompt()

    def quick_expense(self, text):
        if not text.strip() or len(text) > 4000:
            return message("Введите сумму и описание одним сообщением до 4000 символов.")
        result = extract_leading_payment(text, s.today())
        if not result["is_expense"]:
            return message(
                "Быстрый ввод: сначала сумма, затем описание. Например: «50 440 штукатурка и 3 алмазные чашки». "
                "Для будущей покупки используйте /plan."
            )
        self.ensure_categories()
        plans = [
            plan
            for plan in s.list_plans(self.db, self.owner, limit=100)
            if plan["payment_status"] not in ("paid", "finalized")
        ]
        plan_suggestions = self.fast_plan_suggestions(result["values"]["description"], plans)
        category_suggestions = []
        for identifier in plan_suggestions:
            plan = next((item for item in plans if item["id"] == identifier), None)
            if plan and plan["category_id"] not in category_suggestions:
                category_suggestions.append(plan["category_id"])
        self.data = {
            "mode": "expense",
            "quick": True,
            "step": "category",
            "values": result["values"],
            "plan_suggestions": plan_suggestions,
            "category_suggestions": category_suggestions,
        }
        return self.next_missing()

    @staticmethod
    def _search_terms(text):
        ignored = {"оплат", "покуп", "расхо", "товар", "штука"}
        return {
            word[:6]
            for word in re.findall(r"[а-яёa-z]+", text.lower())
            if len(word) >= 5 and word[:6] not in ignored
        }

    def fast_plan_suggestions(self, description, plans):
        """Match plan subjects locally; this deliberately never calls the AI model."""
        query_terms = self._search_terms(description)
        if not query_terms:
            return []
        ranked = []
        for plan in plans:
            document = " ".join(
                str(plan.get(field) or "") for field in ("title", "category", "note")
            )
            shared = query_terms & self._search_terms(document)
            if shared:
                ranked.append((len(shared), plan["id"]))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [identifier for _score, identifier in ranked[:3]]

    def suggested_plans(self):
        """Read suggestions again so archived or removed plans never appear in a draft."""
        result = []
        for identifier in self.data.get("plan_suggestions", []):
            if not isinstance(identifier, str):
                continue
            plan = self.db.get(Plan, identifier)
            if (
                plan
                and plan.owner_id == self.owner
                and not plan.archived
                and plan.payment_status not in ("paid", "finalized")
            ):
                result.append(plan)
        return result

    def use_plan_suggestion(self, identifier):
        if identifier not in self.data.get("plan_suggestions", []):
            raise s.DomainError("Этот вариант больше неактуален.")
        plan = s.owned(self.db, Plan, self.owner, identifier)
        if plan.archived or plan.payment_status in ("paid", "finalized"):
            raise s.DomainError("Этот вариант больше нельзя оплатить.")
        values = self.data["values"]
        values["category_id"] = plan.category_id
        self.data["values"] = values
        self.data["selected_plan_id"] = plan.id
        self.data["selected_plan_version"] = plan.version

    def next_missing(self):
        values = self.data["values"]
        for field in ("amount", "paid_on", "description", "category_id"):
            if not values.get(field):
                return self.advance("category" if field == "category_id" else field)
        return self.advance("confirm")

    def suggest_category(self):
        """Wizard fallback stays usable even when inference is down."""
        if self.ai:
            v = self.data["values"]
            try:
                result = self.ai.suggest(
                    f"Оплачено {v['amount']} рублей {v['paid_on']} за {v['description']}. {v.get('note', '')}",
                    s.categories(self.db, self.owner),
                    s.today(),
                )
                category_id = result["values"].get("category_id")
                if result["is_expense"] and category_id:
                    s.owned(self.db, Category, self.owner, category_id)
                    self.data["values"]["category_id"] = category_id
                    self.data["ai_suggested"] = True
                    return self.advance("confirm")
            except AIUnavailable:
                pass
        reply = self.advance("category")
        if self.ai:
            reply["text"] = "ИИ не предложил категорию. Выберите её вручную.\n" + reply["text"]
        return reply

    def prompt(self):
        step, v = self.data["step"], self.data["values"]
        prompts = {
            "amount": "Сколько оплатили? Например: 3 731,15",
            "planned_amount": "Сколько планировали потратить на эту позицию?",
            "paid_on": "Дата оплаты: ДД.ММ.ГГГГ или «сегодня».",
            "description": "Описание расхода. Например: «Аванс за двери».",
            "title": "Как называется будущая покупка или работа?",
            "expected": "Плановая полная стоимость? Число, 0 или «-», если пока неизвестна.",
            "agreed": "Фактическая полная цена по счёту или договорённости? «-» — вернуться к плановой оценке. Это не сумма оплаты.",
            "due_month": "На какой месяц запланирован расход? ММ.ГГГГ или «-», если даты пока нет.",
            "note": "Примечание? Можно пропустить кнопкой или отправить «-».",
            "category": "Выберите категорию 1.",
            "subcategory": "Выберите категорию 2.",
        }
        rows = []
        if step == "paid_on":
            rows.append([self.cb("Сегодня", "today")])
        if step in ("expected", "agreed", "due_month", "note"):
            rows.append([self.cb("Пока неизвестно" if step != "note" else "Пропустить", "skip")])
        if step == "description" and v.get("description"):
            rows.append([self.cb("Оставить название покупки", "keep")])
        if step in ("category", "subcategory"):
            if step == "category" and self.data.get("category_suggestions"):
                suggested = []
                for identifier in self.data["category_suggestions"]:
                    category = self.db.get(Category, identifier)
                    if category and category.owner_id == self.owner:
                        suggested.append(category)
                if suggested:
                    rows.extend(
                        [
                            [
                                self.cb(
                                    "Подходит: " + s.category_view(self.db, category)["path"],
                                    "quickcat:" + category.id,
                                )
                            ]
                            for category in suggested
                        ]
                    )
            parent = self.data.get("parent") if step == "subcategory" else None
            items = list(
                self.db.scalars(
                    select(Category)
                    .where(Category.owner_id == self.owner, Category.parent_id == parent)
                    .order_by(Category.name)
                )
            )
            page = self.data.get("page", 0)
            rows.extend([[self.cb(c.name, "cat:" + c.id)] for c in items[page : page + 12]])
            if page + 12 < len(items):
                rows.append([self.cb("Далее →", f"catpage:{page + 12}")])
            if page:
                rows.append([self.cb("← Назад", f"catpage:{max(0, page - 12)}")])
            if parent:
                rows.append([self.cb("Без подкатегории", "root:" + parent)])
        if step == "confirm":
            if self.data["mode"] == "pay_plan":
                plan = s.owned(self.db, Plan, self.owner, self.data["id"])
                total = s.plan_amount_minor(plan)
                payment = s.minor(v["amount"])
                after = plan.paid_amount_minor + payment
                remaining = None if total is None else max(0, total - after)
                text = (
                    f"Записать оплату в план?\n{plan.title}\n"
                    f"План: {rub(s.money(total))}\n"
                    f"Новая оплата: {rub(v['amount'])} · {v['paid_on']}\n"
                    f"Будет оплачено: {rub(s.money(after))} · Осталось: {rub(s.money(remaining))}"
                )
                rows = [[self.cb("Сохранить оплату", "planpay:open")]]
                if total is None or after < total:
                    rows[0] = [
                        self.cb("Внести ещё сумму", "planpay:open"),
                        self.cb("Финализировать", "planpay:finalize"),
                    ]
                return message(text, rows)
            cat = s.category_view(
                self.db, s.owned(self.db, Category, self.owner, v["category_id"])
            )["path"]
            if self.data["mode"].endswith("expense"):
                planned = v.get("planned_amount") or v["amount"]
                text = f"Сохранить расход?\n{v['description']}\nПлан: {rub(planned)}\nОплачено: {rub(v['amount'])} · {v['paid_on']}\n{cat}\n{v.get('note', '')}"
                if self.data.get("ai_suggested"):
                    text += "\n\nЧерновик подготовлен с ИИ. Проверьте сумму, дату и категорию."
                if self.data.get("plan_suggestions"):
                    text += "\n\nПосле сохранения перетащите оплату на подходящий план в веб-интерфейсе."
            else:
                text = f"Сохранить план?\n{v['title']}\n{cat}\nПлан: {rub(v.get('expected'))}\nФактическая цена: {rub(v.get('agreed'))}\nМесяц: {(v.get('due_month') or 'без даты')[:7]}\n{v.get('note', '')}"
            rows = [[self.cb("Подтвердить", "confirm"), self.cb("Отменить", "cancel")]]
            if self.data["mode"].endswith("expense"):
                rows += [
                    [
                        self.cb("Изменить сумму", "change:amount"),
                        self.cb("Изменить дату", "change:paid_on"),
                    ],
                    [
                        self.cb("Изменить описание", "change:description"),
                        self.cb("Изменить категорию", "change:category"),
                    ],
                    [self.cb("Изменить примечание", "change:note")],
                ]
            return message(text, rows)
        rows.append([self.cb("Отменить ввод", "cancel")])
        return message(prompts[step], rows)

    def advance(self, step):
        self.data["step"] = step
        self.data.pop("page", None)
        self.save()
        return self.prompt()

    def category_selected(self, identifier):
        c = s.owned(self.db, Category, self.owner, identifier)
        if self.data["step"] == "category" and c.parent_id is not None:
            raise s.DomainError("Выберите категорию первого уровня.")
        if (
            self.data["step"] == "subcategory"
            and c.id != self.data["parent"]
            and c.parent_id != self.data["parent"]
        ):
            raise s.DomainError("Подкатегория не относится к выбранной категории.")
        if self.data["step"] == "category" and self.db.scalar(
            select(Category.id).where(Category.parent_id == c.id).limit(1)
        ):
            self.data["parent"] = c.id
            return self.advance("subcategory")
        self.data["values"]["category_id"] = c.id
        if self.data.pop("review_field", False) or self.data.get("quick"):
            return self.next_missing()
        if self.data["mode"].startswith("edit"):
            return self.advance("confirm")
        return self.advance("confirm" if self.data["mode"] == "expense" else "note")

    def input(self, text):
        if not self.data:
            return self.quick_expense(text)
        text = text.strip()
        step, v = self.data["step"], dict(self.data["values"])
        if step in ("amount", "planned_amount", "expected", "agreed"):
            value = None if text in ("-", "неизвестно") and step != "amount" else s.minor(text)
            if step in ("amount", "planned_amount") and not value:
                raise s.DomainError("Сумма должна быть больше нуля.")
            v[step] = s.money(value)
        elif step in ("paid_on", "due_month"):
            v[step] = (
                None
                if step == "due_month" and text in ("-", "без даты")
                else s.parse_date(text, month=step == "due_month").isoformat()
            )
        elif step in ("description", "title", "note"):
            value = "" if step == "note" and text == "-" else text
            if (not value and step != "note") or len(value) > (4000 if step == "note" else 500):
                raise s.DomainError("Слишком длинный или пустой текст.")
            v[step] = value
        else:
            return self.prompt()
        self.data["values"] = v
        if self.data["mode"] == "pay_plan":
            return self.advance("paid_on" if step == "amount" else "confirm")
        if self.data.pop("review_field", False) or self.data.get("quick"):
            return self.next_missing()
        if self.data["mode"].startswith("edit"):
            return self.advance("confirm")
        if step == "note" and self.data["mode"] == "expense":
            return self.suggest_category()
        next_step = {
            "amount": "paid_on",
            "paid_on": "description",
            "description": "note",
            "title": "expected",
            "expected": "due_month",
            "due_month": "category",
            "note": "category" if self.data["mode"] == "expense" else "confirm",
        }[step]
        return self.advance(next_step)

    def confirm(self):
        v, mode = self.data["values"], self.data["mode"]
        if mode == "pay_plan":
            item = s.record_plan_payment(
                self.db,
                self.owner,
                self.data["id"],
                s.minor(v["amount"]),
                date.fromisoformat(v["paid_on"]),
                self.data["version"],
                self.data.get("finalize", False),
            )
            self.clear()
            return self.show_plan(item["id"])
        if mode.endswith("expense"):
            data = ExpenseIn.model_validate(v)
            item = (
                s.update_expense(self.db, self.owner, self.data["id"], data, self.data["version"])
                if mode.startswith("edit")
                else s.add_expense(self.db, self.owner, data, needs_review=True)
            )
            self.clear()
            return self.show_expense(item["id"])
        data = PlanIn.model_validate(v)
        item = (
            s.update_plan(self.db, self.owner, self.data["id"], data, self.data["version"])
            if mode.startswith("edit")
            else s.add_plan(self.db, self.owner, data)
        )
        self.clear()
        return self.show_plan(item["id"])

    def show_expense(self, identifier):
        item = s.expense_view(self.db, s.owned(self.db, Expense, self.owner, identifier))
        if item["deleted"]:
            rows = [[button("Восстановить", f"restoree:{identifier}:{item['version']}")]]
        else:
            rows = [
                [
                    button("План", "ed:planned_amount:" + identifier),
                    button("Оплачено", "ed:amount:" + identifier),
                ],
                [
                    button("Дата", "ed:paid_on:" + identifier),
                    button("Описание", "ed:description:" + identifier),
                ],
                [
                    button("Примечание", "ed:note:" + identifier),
                    button("Категория", "ed:category:" + identifier),
                ],
                [
                    button("Удалить", f"askdele:{identifier}:{item['version']}"),
                ],
            ]
        return message(expense_card(item), rows)

    def show_plan(self, identifier):
        item = s.plan_view(self.db, s.owned(self.db, Plan, self.owner, identifier))
        if item["archived"]:
            rows = [[button("Восстановить", f"restorep:{identifier}:{item['version']}")]]
        else:
            payment_actions = []
            if item["payment_status"] not in ("paid", "finalized"):
                payment_actions = [
                    button("Внести сумму", "payp:" + identifier),
                ]
            rows = [
                [
                    button("Плановая сумма", "ep:expected:" + identifier),
                    button("Фактическая цена", "ep:agreed:" + identifier),
                ],
                [
                    button("Месяц расхода", "ep:due_month:" + identifier),
                    button("Название", "ep:title:" + identifier),
                ],
                [
                    button("Примечание", "ep:note:" + identifier),
                    button("В архив", f"askarch:{identifier}:{item['version']}"),
                ],
            ]
            if payment_actions:
                rows.insert(0, payment_actions)
        return message(plan_card(item), rows)

    def start_plan_payment(self, identifier):
        plan = s.owned(self.db, Plan, self.owner, identifier)
        if plan.archived or plan.payment_status in ("paid", "finalized"):
            raise s.DomainError("Этот план уже закрыт.")
        self.data = {
            "mode": "pay_plan",
            "step": "amount",
            "values": {},
            "id": identifier,
            "version": plan.version,
        }
        self.save()
        return self.prompt()

    def edit(self, kind, field, identifier):
        if kind == "ed":
            allowed = {"amount", "planned_amount", "paid_on", "description", "note", "category"}
            item = s.expense_view(self.db, s.owned(self.db, Expense, self.owner, identifier))
            values = {
                k: item[k]
                for k in (
                    "amount",
                    "planned_amount",
                    "paid_on",
                    "description",
                    "note",
                    "category_id",
                )
            }
            mode = "edit_expense"
        else:
            allowed = {"expected", "agreed", "due_month", "title", "note"}
            item = s.plan_view(self.db, s.owned(self.db, Plan, self.owner, identifier))
            values = {
                k: item[k]
                for k in ("expected", "agreed", "due_month", "title", "note", "category_id")
            }
            mode = "edit_plan"
        if field not in allowed:
            raise s.DomainError("Неизвестное поле.")
        self.data = {
            "mode": mode,
            "step": field,
            "values": values,
            "id": identifier,
            "version": item["version"],
        }
        self.save()
        return self.prompt()

    def listing(self, kind, offset=0):
        is_expense = kind in ("expenses", "deleted")
        items = (
            s.list_expenses(self.db, self.owner, offset, 9, kind == "deleted")
            if is_expense
            else s.list_plans(self.db, self.owner, offset, 9, kind == "archive")
        )
        rows = [
            [
                button(
                    f"{i.get('description', i.get('title'))[:35]} · {rub(i.get('amount', i.get('agreed') or i.get('expected')))}",
                    ("ve:" if is_expense else "vp:") + i["id"],
                )
            ]
            for i in items[:8]
        ]
        if len(items) > 8:
            rows.append([button("Далее →", f"list:{kind}:{offset + 8}")])
        if offset:
            rows.append([button("← Назад", f"list:{kind}:{max(0, offset - 8)}")])
        return message("Выберите запись:" if items else "Здесь пока нет записей.", rows)

    def report(self, start, end):
        result = s.summary(self.db, self.owner, start, end)
        lines = ["Расходы по месяцам · ₽"]
        for m in result["months"]:
            lines.append(
                f"\n{m['month'][:7]}\nФакт: {rub(m['actual'])}\nБудущий план: {rub(m['planned_remaining'])}\nИзвестный итог: {rub(m['known_total'])}"
                + (f"\nБез оценки: {m['unpriced']}" if m["unpriced"] else "")
                + ("\n⚠ Плановая дата уже прошла" if m["overdue_plan"] else "")
            )
        lines.append(
            f"\nБез даты: {rub(result['undated_remaining'])}; без даты и оценки: {result['undated_unpriced']}.\nПокупки без оценки не включены в итог."
        )
        return message("\n".join(lines))

    def command(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) == 2 else ""
        if cmd in ("/start", "/help"):
            self.ensure_categories()
            if self.data:
                return self.prompt()
            return self.home()
        if cmd == "/cancel":
            self.clear()
            return message("Ввод отменён. /start — меню.")
        if cmd in ("/expense", "/plan"):
            self.ensure_categories()
            if cmd == "/expense" and arg:
                return self.quick_expense(arg)
            return self.start(cmd[1:])
        if cmd in ("/expenses", "/plans", "/deleted", "/archive"):
            return self.listing(cmd[1:])
        if cmd in ("/month", "/forecast"):
            start = s.parse_date(arg, month=True) if arg else s.today().replace(day=1)
            end = max(start, date(2027, 8, 1)) if cmd == "/forecast" else start
            return self.report(start, end)
        if cmd == "/undated":
            plans = s.list_plans(self.db, self.owner, limit=10000)
            names = [
                p["title"] + " · " + rub(p["agreed"] or p["expected"])
                for p in plans
                if not p["due_month"]
            ]
            return message(
                "Без даты:\n" + "\n".join(names) if names else "Все открытые планы имеют дату."
            )
        if cmd == "/categories":
            return message(
                "Категории:\n" + "\n".join(c["path"] for c in s.categories(self.db, self.owner))
            )
        return self.home()

    def callback(self, payload):
        parts = payload.split(":")
        if parts[0] == "d":
            if not self.data or len(parts) < 3 or parts[1] != self.data.get("nonce"):
                return message(
                    "Эта кнопка уже неактуальна. Продолжите текущий ввод или откройте /start."
                )
            action, step = parts[2], self.data["step"]
            if action == "cancel":
                self.clear()
                return message("Ввод отменён.")
            if action == "confirm" and step == "confirm":
                return self.confirm()
            if action == "planpay" and step == "confirm":
                if self.data["mode"] == "pay_plan":
                    self.data["finalize"] = parts[3] == "finalize"
                else:
                    self.data["plan_finalize"] = parts[3] == "finalize"
                return self.confirm()
            if action == "detachplan" and step == "confirm":
                self.data.pop("selected_plan_id", None)
                self.data.pop("selected_plan_version", None)
                return self.advance("confirm")
            if action == "change" and step == "confirm" and self.data["mode"].endswith("expense"):
                field = parts[3]
                if field in ("amount", "paid_on", "description", "category", "note"):
                    self.data["review_field"] = True
                    self.data.pop("parent", None)
                    return self.advance(field)
            if action == "plan" and step == "confirm" and self.data["mode"].endswith("expense"):
                self.use_plan_suggestion(parts[3])
                return self.advance("confirm")
            if action == "today" and step == "paid_on":
                return self.input("сегодня")
            if action == "skip" and step in ("expected", "agreed", "due_month", "note"):
                return self.input("-")
            if action == "keep" and step == "description":
                return self.input(self.data["values"]["description"])
            if action in ("cat", "root") and step in ("category", "subcategory"):
                return self.category_selected(parts[3])
            if action == "quickcat" and step == "category":
                category = s.owned(self.db, Category, self.owner, parts[3])
                self.data["values"]["category_id"] = category.id
                return self.next_missing()
            if action == "catpage" and step in ("category", "subcategory"):
                self.data["page"] = max(0, int(parts[3]))
                self.save()
                return self.prompt()
            return self.prompt()
        if parts[0] == "new" and parts[1] in ("expense", "plan"):
            self.ensure_categories()
            return self.start(parts[1])
        if parts[0] == "ve":
            return self.show_expense(parts[1])
        if parts[0] == "vp":
            return self.show_plan(parts[1])
        if parts[0] == "payp":
            return self.start_plan_payment(parts[1])
        if parts[0] in ("ed", "ep"):
            return self.edit(*parts)
        if parts[0] == "list" and parts[1] in ("expenses", "plans", "deleted", "archive"):
            return self.listing(parts[1], max(0, int(parts[2])))
        if parts[0] in ("month", "forecast"):
            return self.command("/" + parts[0])
        if parts[0] in ("askdele", "askarch"):
            action = "dele" if parts[0] == "askdele" else "arch"
            text = (
                "Удалить расход? Его можно восстановить."
                if action == "dele"
                else "Убрать план из прогноза?"
            )
            return message(
                text,
                [
                    [
                        button("Подтвердить", f"{action}:{parts[1]}:{parts[2]}"),
                        button("Назад", ("ve:" if action == "dele" else "vp:") + parts[1]),
                    ]
                ],
            )
        if parts[0] in ("dele", "restoree"):
            item = s.toggle_expense(
                self.db, self.owner, parts[1], int(parts[2]), parts[0] == "dele"
            )
            return self.show_expense(item["id"])
        if parts[0] in ("arch", "restorep"):
            item = s.toggle_plan(self.db, self.owner, parts[1], int(parts[2]), parts[0] == "arch")
            return self.show_plan(item["id"])
        return self.home()


def process_update(db, allowed_ids, update, ai=None):
    callback = update.get("callback_query")
    msg = callback.get("message", {}) if callback else update.get("message", {})
    actor = callback.get("from", {}) if callback else msg.get("from", {})
    owner = actor.get("id")
    chat = msg.get("chat", {})
    if owner not in allowed_ids or chat.get("type") != "private" or chat.get("id") != owner:
        return {"messages": []}
    if not isinstance(update.get("update_id"), int):
        raise s.DomainError("Отсутствует update_id.")

    def run():
        conversation = Conversation(db, owner, ai=ai)
        try:
            if callback:
                reply = conversation.callback(callback.get("data", ""))
            elif text := msg.get("text", ""):
                reply = (
                    conversation.command(text) if text.startswith("/") else conversation.input(text)
                )
            else:
                reply = message(
                    "В этой версии принимается текст. Запишите оплату через /expense; распознавание вложений появится на следующем этапе."
                )
        except (s.DomainError, ValidationError) as error:
            text = (
                str(error)
                if isinstance(error, s.DomainError)
                else "Проверьте сумму, дату и обязательные поля."
            )
            reply = message(text + "\n/cancel — отменить ввод.")
        except (IndexError, ValueError):
            reply = message("Не удалось прочитать команду. /start — меню.")
        # Telegram text limit is 4096; preserve keyboard on the last chunk.
        text = reply["text"]
        messages = [message(text[i : i + 3800]) for i in range(0, len(text), 3800)]
        if reply.get("reply_markup"):
            messages[-1]["reply_markup"] = reply["reply_markup"]
        return {
            "chat_id": owner,
            "messages": messages,
            "callback_query_id": callback.get("id") if callback else None,
        }

    return s.once(db, owner, f"tg:{update['update_id']}", update, run)
