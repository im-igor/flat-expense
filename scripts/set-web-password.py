#!/usr/bin/env python3
"""Interactively protect the direct HTTP interface and update the private .env file."""

import argparse
import getpass
import hashlib
import os
import secrets
from pathlib import Path


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Public server IP or host name")
    args = parser.parse_args()
    host = args.host.strip().lower()
    if not host or any(character in host for character in "/: "):
        raise SystemExit("Укажите IP или имя сервера без протокола и порта.")
    first = getpass.getpass("Новый пароль веб-интерфейса (минимум 12 символов): ")
    second = getpass.getpass("Повторите пароль: ")
    if len(first) < 12:
        raise SystemExit("Пароль должен содержать минимум 12 символов.")
    if first != second:
        raise SystemExit("Пароли не совпали.")

    path = Path(".env")
    if not path.is_file():
        raise SystemExit("Файл .env не найден. Запустите скрипт из корня проекта.")
    updates = {
        "WEB_ENABLED": "true",
        "WEB_PUBLIC": "true",
        "WEB_PASSWORD_HASH": password_hash(first),
        "WEB_ALLOWED_HOSTS": f"{host},localhost,127.0.0.1",
        "WEB_BIND_ADDRESS": "0.0.0.0",
        "WEB_PORT": "80",
    }
    kept = []
    for line in path.read_text().splitlines():
        key = line.partition("=")[0]
        if key not in updates:
            kept.append(line)
    kept.extend(f"{key}={value}" for key, value in updates.items())
    temporary = path.with_suffix(".env.tmp")
    temporary.write_text("\n".join(kept) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    print("Пароль сохранён в виде стойкого хеша; исходный пароль не записан.")


if __name__ == "__main__":
    main()
