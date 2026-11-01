#!/usr/bin/env bash
# Restore exclusively into a new temporary database, never the working database.
set -euo pipefail
cd "$(dirname "$0")/.."
backup_path="${1:?Укажите путь к резервной копии}"
test -f "$backup_path"
restore_db="restore_check_$(date -u +%s)_$$"
docker compose exec -T db createdb -U flat "$restore_db"
trap 'docker compose exec -T db dropdb -U flat --if-exists "$restore_db"' EXIT
docker compose exec -T db pg_restore -U flat --dbname="$restore_db" --exit-on-error --no-owner < "$backup_path"
docker compose exec -T db psql -U flat -d "$restore_db" -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version; SELECT count(*) AS expenses FROM expenses; SELECT count(*) AS plans FROM plans;'
echo 'Копия восстановлена и прочитана в отдельной временной базе.'
