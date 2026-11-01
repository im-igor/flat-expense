#!/usr/bin/env bash
# Load prebuilt images from this revision's GitHub Release; never pull or build.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'Этот комплект предназначен для Linux x86_64. Нужна сборка для архитектуры вашего сервера.' >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo 'Сначала создайте настройки: python3 scripts/configure.py --telegram-user-id ВАШ_ID --with-bot' >&2
  exit 1
fi
for required in docker git curl sha256sum; do
  command -v "$required" > /dev/null || { echo "Не найдена команда: $required" >&2; exit 1; }
done
docker info > /dev/null
docker compose version > /dev/null
revision="$(git rev-parse --short=12 HEAD)"
archive='flat-expense-linux-amd64.tar.gz'
base_url="https://github.com/im-igor/flat-expense/releases/download/offline-${revision}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
printf 'Загружаю готовую сборку %s из GitHub…\n' "$revision"
# Download the checksum first: a missing release fails before the large download.
curl --fail --location --retry 3 --connect-timeout 15 --max-time 60 \
  "$base_url/$archive.sha256" --output "$work_dir/$archive.sha256"
curl --fail --location --retry 3 --connect-timeout 15 --max-time 1800 \
  "$base_url/$archive" --output "$work_dir/$archive"
(cd "$work_dir" && sha256sum --check "$archive.sha256")
docker load --input "$work_dir/$archive"
docker compose --profile bot up --no-build --pull never -d
docker compose ps
echo 'Контейнеры запущены. Проверьте /start в Telegram и curl --fail http://127.0.0.1:8000/health'
