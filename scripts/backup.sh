#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
mkdir -p backups
backup_path="backups/flat-$(date -u +%Y%m%dT%H%M%SZ).dump"
temporary_path="$(mktemp backups/.backup.XXXXXX)"
trap 'rm -f "$temporary_path"' EXIT
docker compose exec -T db pg_dump -U flat -d flat --format=custom > "$temporary_path"
test -s "$temporary_path"
docker compose exec -T db pg_restore --list < "$temporary_path" > /dev/null
mv "$temporary_path" "$backup_path"
echo "Резервная копия: $backup_path"
