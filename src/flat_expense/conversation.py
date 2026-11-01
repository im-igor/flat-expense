"""Durable bot conversations. No Telegram network calls in this module."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from flat_expense import service as s
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
        f"{rub(item['amount'])} · {item['paid_on']}\n{item['category']}\n"
        f"{'Связан с планом' if item['plan_id'] else 'Самостоятельный расход'}\n{item['note']}"
    )


def plan_card(item):
    return (
        f"{'Архив' if item['archived'] else 'План'}: {item['title']}\n{item['category']}\n"
        f"Плановая стоимость: {rub(item['expected'])}\nФактическая цена: {rub(item['agreed'])}\n"
        f"Оплачено: {rub(item['paid'])}\nОсталось: {rub(item['remaining'])}\n"
        f"Месяц остатка: {(item['due_month'] or 'без даты')[:7]}\n"
        + (
            f"Оплачено сверх цены: {rub(item['overpaid'])}\n"
            if item["overpaid"] and item["overpaid"] != "0.00"
            else ""
        )
        + item["note"]
    )


class Conversation:
    def __init__(self, db, owner):
        self.db, self.owner = db, owner
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

    def cb(self, label, value):
        return button(label, f"d:{self.data['nonce']}:{value}")

    def home(self):
        return message(
            "Учёт расходов на квартиру\n\nКаждая оплата хранится отдельно. План и фактическая цена покупки не считаются оплатой.\n\n/expense — расход\n/plan — будущая покупка\n/expenses — последние расходы\n/plans — планы и остатки\n/month — текущий месяц (или /month 2027-06)\n/forecast — прогноз\n/undated — планы без даты\n/deleted — удалённые расходы\n/archive — архив планов\n/categories — категории\n/cancel — отменить ввод",
            [
                [button("Записать расход", "new:expense"), button("Добавить план", "new:plan")],
                [button("Расходы", "list:expenses:0"), button("Планы", "list:plans:0")],
                [button("Этот месяц", "month"), button("Прогноз", "forecast")],
            ],
        )

    def start(self, mode, plan_id=None):
        self.data = {
            "mode": mode,
            "step": "amount" if mode == "expense" else "title",
            "values": {"note": ""},
        }
        if plan_id:
            p = s.owned(self.db, Plan, self.owner, plan_id)
            if p.archived:
                raise s.DomainError("Сначала восстановите план из архива.")
            self.data["values"].update(plan_id=p.id, category_id=p.category_id, description=p.title)
        self.save()
        return self.prompt()

    def prompt(self):
        step, v = self.data["step"], self.data["values"]
        prompts = {
            "amount": "Сколько оплатили? Например: 3 731,15",
            "paid_on": "Дата оплаты: ДД.ММ.ГГГГ или «сегодня».",
            "description": "Описание расхода. Для аванса можно указать «Аванс за двери». Связь с покупкой сохранится по ID.",
            "title": "Как называется будущая покупка или работа?",
            "expected": "Плановая полная стоимость? Число, 0 или «-», если пока неизвестна.",
            "agreed": "Фактическая полная цена по счёту или договорённости? «-» — вернуться к плановой оценке. Это не сумма оплаты.",
            "due_month": "На какой месяц запланирован неоплаченный остаток? ММ.ГГГГ или «-», если даты пока нет.",
            "note": "Примечание? Можно пропустить кнопкой или отправить «-».",
            "category": "Выберите категорию 1.",
            "subcategory": "Выберите категорию 2.",
            "link": "Это самостоятельный расход или оплата существующего плана?",
        }
        rows = []
        if step == "paid_on":
            rows.append([self.cb("Сегодня", "today")])
        if step in ("expected", "agreed", "due_month", "note"):
            rows.append([self.cb("Пока неизвестно" if step != "note" else "Пропустить", "skip")])
        if step == "description" and v.get("description"):
            rows.append([self.cb("Оставить название покупки", "keep")])
        if step in ("category", "subcategory"):
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
        if step == "link":
            page = self.data.get("page", 0)
            rows.append([self.cb("Самостоятельный расход", "standalone")])
            plans = s.list_plans(self.db, self.owner, offset=page, limit=9)
            rows.extend([[self.cb(p["title"], "plan:" + p["id"])] for p in plans[:8]])
            if len(plans) == 9:
                rows.append([self.cb("Ещё планы →", f"planpage:{page + 8}")])
            if page:
                rows.append([self.cb("← Назад", f"planpage:{max(0, page - 8)}")])
        if step == "confirm":
            cat = s.category_view(
                self.db, s.owned(self.db, Category, self.owner, v["category_id"])
            )["path"]
            if self.data["mode"].endswith("expense"):
                text = f"Сохранить расход?\n{v['description']}\n{rub(v['amount'])} · {v['paid_on']}\n{cat}\n{'Оплата по плану' if v.get('plan_id') else 'Без плана'}\n{v.get('note', '')}"
            else:
                text = f"Сохранить план?\n{v['title']}\n{cat}\nПлан: {rub(v.get('expected'))}\nФактическая цена: {rub(v.get('agreed'))}\nМесяц: {(v.get('due_month') or 'без даты')[:7]}\n{v.get('note', '')}"
            return message(
                text, [[self.cb("Подтвердить", "confirm"), self.cb("Отменить", "cancel")]]
            )
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
        if self.data["mode"].startswith("edit"):
            return self.advance("confirm")
        return self.advance("description" if self.data["mode"] == "expense" else "note")

    def input(self, text):
        if not self.data:
            return self.home()
        text = text.strip()
        step, v = self.data["step"], dict(self.data["values"])
        if step in ("amount", "expected", "agreed"):
            value = None if text in ("-", "неизвестно") and step != "amount" else s.minor(text)
            if step == "amount" and not value:
                raise s.DomainError("Сумма оплаты должна быть больше нуля.")
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
        if self.data["mode"].startswith("edit"):
            return self.advance("confirm")
        next_step = {
            "amount": "paid_on",
            "paid_on": "description" if v.get("plan_id") else "link",
            "description": "note",
            "title": "expected",
            "expected": "due_month",
            "due_month": "category",
            "note": "confirm",
        }[step]
        return self.advance(next_step)

    def confirm(self):
        v, mode = self.data["values"], self.data["mode"]
        if mode.endswith("expense"):
            data = ExpenseIn.model_validate(v)
            item = (
                s.update_expense(self.db, self.owner, self.data["id"], data, self.data["version"])
                if mode.startswith("edit")
                else s.add_expense(self.db, self.owner, data)
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
                    button("Сумма", "ed:amount:" + identifier),
                    button("Дата", "ed:paid_on:" + identifier),
                ],
                [
                    button("Описание", "ed:description:" + identifier),
                    button("Примечание", "ed:note:" + identifier),
                ],
                [
                    button("Связь с планом", "ed:link:" + identifier),
                    button("Удалить", f"askdele:{identifier}:{item['version']}"),
                ],
            ]
            if not item["plan_id"]:
                rows.append([button("Категория", "ed:category:" + identifier)])
        return message(expense_card(item), rows)

    def show_plan(self, identifier):
        item = s.plan_view(self.db, s.owned(self.db, Plan, self.owner, identifier))
        if item["archived"]:
            rows = [[button("Восстановить", f"restorep:{identifier}:{item['version']}")]]
        else:
            rows = [
                [button("Записать оплату", "pay:" + identifier)],
                [
                    button("Плановая сумма", "ep:expected:" + identifier),
                    button("Фактическая цена", "ep:agreed:" + identifier),
                ],
                [
                    button("Месяц остатка", "ep:due_month:" + identifier),
                    button("Название", "ep:title:" + identifier),
                ],
                [
                    button("Примечание", "ep:note:" + identifier),
                    button("В архив", f"askarch:{identifier}:{item['version']}"),
                ],
            ]
        return message(plan_card(item), rows)

    def edit(self, kind, field, identifier):
        if kind == "ed":
            allowed = {"amount", "paid_on", "description", "note", "category", "link"}
            item = s.expense_view(self.db, s.owned(self.db, Expense, self.owner, identifier))
            values = {
                k: item[k]
                for k in ("amount", "paid_on", "description", "note", "category_id", "plan_id")
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
                    f"{i.get('description', i.get('title'))[:35]} · {rub(i.get('amount', i.get('remaining')))}",
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
                f"\n{m['month'][:7]}\nФакт: {rub(m['actual'])}\nОстаток плана: {rub(m['planned_remaining'])}\nИзвестный итог: {rub(m['known_total'])}"
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
            s.seed_categories(self.db, self.owner)
            return self.home()
        if cmd == "/cancel":
            self.clear()
            return message("Ввод отменён. /start — меню.")
        if cmd in ("/expense", "/plan"):
            s.seed_categories(self.db, self.owner)
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
                p["title"] + " · " + rub(p["remaining"])
                for p in plans
                if not p["due_month"] and p["remaining"] != "0.00"
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
            if action == "today" and step == "paid_on":
                return self.input("сегодня")
            if action == "skip" and step in ("expected", "agreed", "due_month", "note"):
                return self.input("-")
            if action == "keep" and step == "description":
                return self.input(self.data["values"]["description"])
            if action in ("cat", "root") and step in ("category", "subcategory"):
                return self.category_selected(parts[3])
            if action == "standalone" and step == "link":
                self.data["values"]["plan_id"] = None
                return self.advance("category")
            if action == "plan" and step == "link":
                p = s.owned(self.db, Plan, self.owner, parts[3])
                if p.archived:
                    raise s.DomainError("План уже перенесён в архив.")
                self.data["values"].update(
                    plan_id=p.id, category_id=p.category_id, description=p.title
                )
                return self.advance(
                    "confirm" if self.data["mode"].startswith("edit") else "description"
                )
            if (action == "planpage" and step == "link") or (
                action == "catpage" and step in ("category", "subcategory")
            ):
                self.data["page"] = max(0, int(parts[3]))
                self.save()
                return self.prompt()
            return self.prompt()
        if parts[0] == "new" and parts[1] in ("expense", "plan"):
            s.seed_categories(self.db, self.owner)
            return self.start(parts[1])
        if parts[0] == "pay":
            return self.start("expense", parts[1])
        if parts[0] == "ve":
            return self.show_expense(parts[1])
        if parts[0] == "vp":
            return self.show_plan(parts[1])
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
                else "Убрать план из прогноза? Уже сделанные оплаты останутся в истории."
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


def process_update(db, allowed_ids, update):
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
        conversation = Conversation(db, owner)
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
