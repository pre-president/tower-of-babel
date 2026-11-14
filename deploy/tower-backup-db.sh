#!/bin/bash
# Daily PostgreSQL backup for Tower of Babel (retains 14 daily dumps)
set -euo pipefail

BACKUP_DIR="/var/backups/tower-of-babel/daily"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${BACKUP_DIR}/tower_hub_${STAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

su - postgres -c "pg_dump --clean --if-exists tower_hub" | gzip -9 > "${OUT_FILE}"
chmod 600 "${OUT_FILE}"

# Prune backups older than 14 days
find "${BACKUP_DIR}" -type f -name "tower_hub_*.sql.gz" -mtime +14 -delete

echo "Backup completed: ${OUT_FILE} ($(stat -c%s "${OUT_FILE}") bytes)"
