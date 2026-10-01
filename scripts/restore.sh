#!/usr/bin/env bash
# Восстановление базы данных из дампа scripts/backup.sh.
#
#   scripts/restore.sh backups/db_20261001T030000Z.dump [--yes]
#
# 1) останавливает app и bot (чтобы не было подключений к БД);
# 2) пересоздаёт базу и загружает дамп (pg_restore);
# 3) проверяет число записей в signals и запускает app и bot обратно.
# Конфигурацию (.env, config/) при необходимости распаковать вручную:
#   tar -xzf backups/config_<UTC>.tar.gz
#
# Переменные: PG_EXEC (см. backup.sh); STOP_SERVICES=0 — не трогать контейнеры.
set -euo pipefail
cd "$(dirname "$0")/.."

dump="${1:?укажите файл дампа: scripts/restore.sh backups/db_<UTC>.dump}"
[ -f "$dump" ] || { echo "нет файла $dump" >&2; exit 1; }

env_value() {
  [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true
}
PG_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"; PG_USER="${PG_USER:-switch}"
PG_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"; PG_DB="${PG_DB:-switch_trading}"
PG_EXEC="${PG_EXEC-docker compose exec -T postgres}"
STOP_SERVICES="${STOP_SERVICES:-1}"

if [ "${2:-}" != "--yes" ]; then
  read -r -p "База ${PG_DB} будет ЗАМЕНЕНА содержимым ${dump}. Продолжить? [yes/N] " answer
  [ "$answer" = "yes" ] || { echo "отменено"; exit 1; }
fi

if [ "$STOP_SERVICES" = "1" ]; then
  echo "[restore] остановка app и bot"
  docker compose stop app bot
fi

echo "[restore] пересоздание базы ${PG_DB}"
$PG_EXEC dropdb -U "$PG_USER" --if-exists "$PG_DB"
$PG_EXEC createdb -U "$PG_USER" -O "$PG_USER" "$PG_DB"

echo "[restore] загрузка дампа"
$PG_EXEC pg_restore -U "$PG_USER" -d "$PG_DB" --no-owner --exit-on-error < "$dump"

signals="$($PG_EXEC psql -U "$PG_USER" -d "$PG_DB" -tAc 'SELECT count(*) FROM signals')"
version="$($PG_EXEC psql -U "$PG_USER" -d "$PG_DB" -tAc 'SELECT version_num FROM alembic_version')"
echo "[restore] signals: ${signals}; схема alembic: ${version}"

if [ "$STOP_SERVICES" = "1" ]; then
  echo "[restore] запуск app и bot"
  docker compose start app bot
fi
echo "[restore] готово"
