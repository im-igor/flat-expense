"""Create local secrets without echoing or accepting the bot token in argv."""

import argparse
import getpass
import os
import re
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--telegram-user-id", type=int, required=True)
parser.add_argument("--with-bot", action="store_true")
args = parser.parse_args()
if args.telegram_user_id <= 0:
    parser.error("Нужен положительный числовой Telegram user ID.")
token = getpass.getpass("Токен Telegram-бота (ввод скрыт): ") if args.with_bot else ""
if args.with_bot and not token:
    parser.error("Токен не введён. Настройки не созданы.")
if token and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
    parser.error("Формат токена не распознан.")
path = Path(".env")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(
        f"POSTGRES_PASSWORD={secrets.token_hex(32)}\nAPI_TOKEN={secrets.token_hex(32)}\nALLOWED_TELEGRAM_USER_IDS={args.telegram_user_id}\nTELEGRAM_BOT_TOKEN={token}\n"
    )
print("Создан .env с правами 0600. Существующий файл никогда не перезаписывается.")
