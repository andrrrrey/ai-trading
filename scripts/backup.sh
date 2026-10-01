#!/usr/bin/env bash
# Резервное копирование (п. 2.1.8 Договора): база данных + конфигурация.
#
#   scripts/backup.sh                 # из корня проекта на сервере
#
# Создаёт в $BACKUP_DIR (по умолчанию ./backups):
#   db_<UTC>.dump        — pg_dump в custom-формате (история сигналов, формулы, мониторинг)
#   config_<UTC>.tar.gz  — .env, config/, docker-compose.yml (содержит секреты: права 600)
# Проверяет читаемость дампа (pg_restore --list) и удаляет копии старше
# $BACKUP_RETENTION_DAYS дней (по умолчанию 14). Исходный код хранится в GitHub.
#
# Переменные:
#   PG_EXEC  — как вызывать утилиты PostgreSQL; по умолчанию внутри контейнера
#              "docker compose exec -T postgres". Пустая строка — локальные pg_dump/
#              pg_restore (подключение через PGHOST/PGPORT/PGPASSWORD).
set -euo pipefail
cd "$(dirname "$0")/.."

env_value() {  # безопасно читает KEY=value из .env (значения могут содержать пробелы)
  [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true
}

PG_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"; PG_USER="${PG_USER:-switch}"
PG_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"; PG_DB="${PG_DB:-switch_trading}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
PG_EXEC="${PG_EXEC-docker compose exec -T postgres}"

umask 077
mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
dump="$BACKUP_DIR/db_${ts}.dump"

echo "[backup] pg_dump ${PG_DB} → ${dump}"
$PG_EXEC pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom --no-owner > "$dump.partial"
# Дамп считается готовым только если архив читается.
$PG_EXEC pg_restore --list < "$dump.partial" > /dev/null
mv "$dump.partial" "$dump"

config_files=()
for path in .env config docker-compose.yml; do
  [ -e "$path" ] && config_files+=("$path")
done
if [ "${#config_files[@]}" -gt 0 ]; then
  tar -czf "$BACKUP_DIR/config_${ts}.tar.gz" "${config_files[@]}"
fi

find "$BACKUP_DIR" -maxdepth 1 \( -name 'db_*.dump' -o -name 'config_*.tar.gz' \) \
  -mtime +"$RETENTION_DAYS" -print -delete | sed 's/^/[backup] удалена старая копия: /'

echo "[backup] готово: $(du -h "$dump" | cut -f1) ${dump}"
