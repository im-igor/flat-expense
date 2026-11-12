"""Single long-poll consumer. DB commit precedes delivery; ack follows delivery."""

import asyncio
import logging
import mimetypes

import httpx
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BotCommand, InlineKeyboardMarkup

from flat_expense.config import settings

log = logging.getLogger("flat_expense.bot")

TRANSIENT_TELEGRAM_ERRORS = (
    ClientDecodeError,
    TelegramNetworkError,
    TelegramServerError,
)
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def incoming_file(update):
    message = update.get("message") or {}
    if document := message.get("document"):
        filename = document.get("file_name") or f"document-{message.get('message_id', 'file')}"
        content_type = document.get("mime_type") or mimetypes.guess_type(filename)[0]
        return document, filename, content_type
    if photos := message.get("photo"):
        return photos[-1], f"photo-{message.get('message_id', 'file')}.jpg", "image/jpeg"
    return None


async def store_incoming_file(bot, api, update):
    found = incoming_file(update)
    if found is None:
        return None
    file_meta, filename, content_type = found
    message = update["message"]
    if file_meta.get("file_size", 0) > MAX_DOCUMENT_BYTES:
        return "Файл больше 20 МБ и не был сохранён."
    if not content_type:
        return "Не удалось определить тип файла. Отправьте PDF, изображение, DOCX или XLSX."
    try:
        remote = await bot.get_file(file_meta["file_id"])
        downloaded = await bot.download_file(remote.file_path)
        content = downloaded.read()
    except TRANSIENT_TELEGRAM_ERRORS:
        raise
    if len(content) > MAX_DOCUMENT_BYTES:
        return "Файл больше 20 МБ и не был сохранён."
    source = "forwarded" if (message.get("forward_origin") or message.get("forward_from")) else "telegram"
    response = await api.post(
        "/internal/telegram/documents",
        params={"filename": filename, "note": (message.get("caption") or "")[:500], "source": source},
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Telegram-User-Id": str(message["from"]["id"]),
            "Idempotency-Key": f"tgdoc:{update['update_id']}:{file_meta['file_id']}",
        },
    )
    if response.is_success:
        return "Документ сохранён. Откройте нужную оплату или план в веб-интерфейсе и прикрепите его из блока «Документы из Telegram»."
    if response.status_code < 500:
        try:
            detail = response.json().get("detail", "Не удалось сохранить файл.")
        except ValueError:
            detail = "Не удалось сохранить файл."
        return str(detail)
    response.raise_for_status()


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
            timeout=120,
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
        connected = False
        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset, timeout=25, allowed_updates=["message", "callback_query"]
                )
                if not connected:
                    log.info("Telegram polling работает; соединение с API проверено.")
                    connected = True
                for update in updates:
                    result = await api.post(
                        "/internal/telegram/update",
                        json=update.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    )
                    result.raise_for_status()
                    payload = result.json()
                    if (
                        update.message
                        and update.message.chat.type == "private"
                        and update.message.from_user.id in config.allowed_ids
                        and (update.message.document or update.message.photo)
                    ):
                        status = await store_incoming_file(bot, api, update.model_dump(mode="json", by_alias=True, exclude_none=True))
                        if status:
                            payload["messages"].append({"text": status})
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
            except (*TRANSIENT_TELEGRAM_ERRORS, httpx.HTTPError):
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
