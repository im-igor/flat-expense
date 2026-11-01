"""Single long-poll consumer. DB commit precedes delivery; ack follows delivery."""

import asyncio
import logging

import httpx
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import BotCommand, InlineKeyboardMarkup

from flat_expense.config import settings

log = logging.getLogger("flat_expense.bot")


async def run():
    config = settings()
    if config.telegram_bot_token is None:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN в защищённом окружении.")
    bot = Bot(
        token=config.telegram_bot_token.get_secret_value(),
        session=AiohttpSession(api=TelegramAPIServer.from_base(config.telegram_api_root)),
    )
    async with (
        bot,
        httpx.AsyncClient(
            base_url=config.api_base_url,
            timeout=40,
            headers={"Authorization": "Bearer " + config.api_token.get_secret_value()},
        ) as api,
    ):
        info = await bot.get_webhook_info()
        if info.url:
            raise SystemExit(
                "У бота уже установлен webhook. Он не изменён. Для новой версии используйте отдельного бота или явно отключите прежний webhook."
            )
        await bot.set_my_commands(
            [
                BotCommand(command=c, description=d)
                for c, d in [
                    ("start", "Меню"),
                    ("expense", "Записать расход"),
                    ("plan", "Добавить план"),
                    ("expenses", "Последние расходы"),
                    ("plans", "Планы и остатки"),
                    ("month", "Расходы за месяц"),
                    ("forecast", "Прогноз до переезда"),
                    ("undated", "Планы без даты"),
                    ("deleted", "Удалённые расходы"),
                    ("archive", "Архив планов"),
                    ("categories", "Категории"),
                    ("cancel", "Отменить ввод"),
                ]
            ]
        )
        response = await api.get("/internal/telegram/cursor")
        response.raise_for_status()
        offset = response.json()["offset"]
        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset, timeout=25, allowed_updates=["message", "callback_query"]
                )
                for update in updates:
                    result = await api.post(
                        "/internal/telegram/update",
                        json=update.model_dump(mode="json", exclude_none=True),
                    )
                    result.raise_for_status()
                    payload = result.json()
                    if payload.get("callback_query_id"):
                        try:
                            await bot.answer_callback_query(payload["callback_query_id"])
                        except TelegramBadRequest:
                            pass  # The card may still be delivered after callback expiry.
                    try:
                        for msg in payload["messages"]:
                            markup = (
                                InlineKeyboardMarkup.model_validate(msg["reply_markup"])
                                if msg.get("reply_markup")
                                else None
                            )
                            await bot.send_message(
                                payload["chat_id"], msg["text"], reply_markup=markup
                            )
                    except TelegramForbiddenError:
                        # User blocked the bot after submission; expense is already durable.
                        log.warning("Ответ не доставлен: пользователь заблокировал бота.")
                    ack = await api.post(f"/internal/telegram/ack/{update.update_id + 1}")
                    ack.raise_for_status()
                    offset = update.update_id + 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
            except (TelegramNetworkError, httpx.HTTPError):
                # Never log exception text: an HTTP URL can contain the bot token.
                log.warning("Временный сбой связи; повтор через 5 секунд.")
                await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Startup/network errors may contain request URLs with a bot token.
        log.error(
            "Бот остановлен (%s). Проверьте настройки и связь с Telegram.", type(exc).__name__
        )
        raise SystemExit(1) from None
