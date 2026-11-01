#!/usr/bin/env bash
# Rebuild only application code on top of the currently installed image.
# Dependency or base-image changes must use scripts/start-offline.sh instead.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo 'Сначала создайте настройки: python3 scripts/configure.py --telegram-user-id ВАШ_ID' >&2
  exit 1
fi
for required in docker git; do
  command -v "$required" > /dev/null || { echo "Не найдена команда: $required" >&2; exit 1; }
done
docker info > /dev/null
docker compose version > /dev/null

base_image='flat-expense:local'
docker image inspect "$base_image" > /dev/null
base_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$base_image")"
if [[ -z "$base_revision" ]] || ! git cat-file -e "$base_revision^{commit}" 2> /dev/null; then
  echo 'Не удалось определить исходный коммит установленного образа. Используйте scripts/start-offline.sh.' >&2
  exit 1
fi
if ! git merge-base --is-ancestor "$base_revision" HEAD; then
  echo 'Текущий код не является продолжением установленной версии. Используйте scripts/start-offline.sh.' >&2
  exit 1
fi
if ! git diff --quiet "$base_revision" HEAD -- pyproject.toml uv.lock Dockerfile; then
  echo 'Изменились зависимости или базовый Dockerfile. Нужен полный запуск scripts/start-offline.sh.' >&2
  exit 1
fi

revision="$(git rev-parse --short=12 HEAD)"
next_image="flat-expense:fast-$revision"
rollback_image='flat-expense:rollback'
docker tag "$base_image" "$rollback_image"
cleanup() {
  docker image rm "$next_image" "$rollback_image" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --network=none --pull=false --build-arg BASE_IMAGE="$base_image" \
  --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
  --file Dockerfile.fast --tag "$next_image" .
docker tag "$next_image" "$base_image"

if ! docker compose run --rm --no-deps migrate alembic upgrade head; then
  docker tag "$rollback_image" "$base_image"
  exit 1
fi
if ! docker compose up --no-build --pull never --force-recreate --no-deps -d api; then
  docker tag "$rollback_image" "$base_image"
  docker compose up --no-build --pull never --force-recreate --no-deps -d api
  exit 1
fi

for attempt in $(seq 1 30); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"; then
    docker compose stop bot > /dev/null 2>&1 || true
    docker compose rm -f bot > /dev/null 2>&1 || true
    docker image rm "$rollback_image" > /dev/null 2>&1 || true
    docker image prune -f
    echo "Быстрое обновление $revision установлено; Telegram оставлен выключенным."
    exit 0
  fi
  sleep 2
done

echo 'Новая версия не стала healthy; возвращаю предыдущий образ.' >&2
docker tag "$rollback_image" "$base_image"
docker compose up --no-build --pull never --force-recreate --no-deps -d api
exit 1
