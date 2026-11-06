#!/bin/bash
set -euo pipefail

# ==========================================
# 설정 변수 (백업 스크립트와 동일하게 설정)
# ==========================================
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/db_backups}"
PASS_PHRASE="${DB_BACKUP_ENCRYPTION_KEY:-}"
MYSQL_ROOT_PASS="${MYSQL_ROOT_PASS:-}"
TARGET_DB="${TARGET_DB:-hospital_db}"

for required_secret in DB_BACKUP_ENCRYPTION_KEY MYSQL_ROOT_PASS; do
  if [ -z "${!required_secret:-}" ]; then
    echo "[CONFIG ERROR] Required environment variable is missing: ${required_secret}"
    echo "[설정 오류] 필수 환경변수가 없습니다: ${required_secret}"
    exit 1
  fi
done

echo "[DR DATABASE] Starting database restore workflow."
echo "[DR 데이터베이스] 데이터베이스 복구 워크플로 시작."

MYSQL_DEFAULTS_FILE=$(mktemp)
cleanup_restore_files() {
    rm -f "$MYSQL_DEFAULTS_FILE" "$BACKUP_DIR/recovery.sql"
}
trap cleanup_restore_files EXIT
chmod 600 "$MYSQL_DEFAULTS_FILE"
{
    printf '[client]\n'
    printf 'user=root\n'
    printf 'password=%s\n' "$MYSQL_ROOT_PASS"
} > "$MYSQL_DEFAULTS_FILE"

# 1. 복구 대상 암호화 백업 파일(.enc) 선택
if [ -n "${DB_BACKUP_FILE:-}" ]; then
    LATEST_BACKUP="$DB_BACKUP_FILE"
else
    LATEST_BACKUP=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.enc' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {print $2}')
fi

if [ -z "$LATEST_BACKUP" ]; then
    echo "[RESTORE ERROR] No encrypted backup artifact was found for recovery."
    echo "[복구 오류] 복구에 사용할 암호화 백업 파일을 찾지 못했습니다."
    exit 1
fi

BACKUP_BASENAME=$(basename -- "$LATEST_BACKUP")
BACKUP_PARENT=$(dirname -- "$LATEST_BACKUP")
case "$BACKUP_BASENAME" in
    *.sql.gz.enc) ;;
    *)
        echo "[RESTORE ERROR] Backup artifact must use the .sql.gz.enc naming contract."
        echo "[복구 오류] 백업 파일은 .sql.gz.enc 명명 규칙을 따라야 합니다."
        exit 1
        ;;
esac
BACKUP_STEM=$(printf '%s' "$BACKUP_BASENAME" | sed 's/\.sql\.gz\.enc$//')
MATCHING_STATUS="$BACKUP_PARENT/$BACKUP_STEM.master_status"
if [ ! -s "$MATCHING_STATUS" ]; then
    echo "[RESTORE ERROR] Matching master_status artifact is missing for the selected backup."
    echo "[복구 오류] 선택한 백업과 일치하는 master_status 파일이 없습니다."
    exit 1
fi

# Validate actual values, not only field-name markers. This keeps malformed
# metadata from reaching a later CHANGE REPLICATION SOURCE command.
status_file_name="$(grep -Eo "(MASTER_LOG_FILE|SOURCE_LOG_FILE)[[:space:]]*=[[:space:]]*'[^']+'" "$MATCHING_STATUS" \
    | sed -E "s/^[^=]+='//; s/'$//" | head -n 1 || true)"
if [ -z "$status_file_name" ]; then
    status_file_name="$(awk -F: '/^[[:space:]]*(File|BinlogFile)[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' "$MATCHING_STATUS" || true)"
fi
status_position="$(grep -Eo "(MASTER_LOG_POS|SOURCE_LOG_POS)[[:space:]]*=[[:space:]]*[0-9]+" "$MATCHING_STATUS" \
    | sed -E 's/^[^=]+=//' | head -n 1 || true)"
if [ -z "$status_position" ]; then
    status_position="$(awk -F: '/^[[:space:]]*(Position|BinlogPosition)[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' "$MATCHING_STATUS" || true)"
fi
if [[ ! "$status_file_name" =~ ^[A-Za-z0-9._-]+$ ]] \
    || [[ ! "$status_position" =~ ^[0-9]+$ ]]; then
    echo "[RESTORE ERROR] Matching master_status artifact has no usable file/position pair."
    echo "[복구 오류] 일치하는 master_status 파일에 사용할 수 있는 File/Position 쌍이 없습니다."
    exit 1
fi

echo "[DR DATABASE] Selected backup artifact: $LATEST_BACKUP"
echo "[DR 데이터베이스] 선택된 백업 파일: $LATEST_BACKUP"

# 2. 암호화 해제 (백업 시 사용한 -aes-256-cbc -salt -pbkdf2 방식 대응)
echo "[1/3] Decrypting backup artifact..."
echo "[1/3] 백업 파일 복호화 중."
openssl enc -d -aes-256-cbc -salt -pbkdf2 -in "$LATEST_BACKUP" -out "$BACKUP_DIR/recovery.sql.gz" -k "$PASS_PHRASE"
gzip -t "$BACKUP_DIR/recovery.sql.gz"

# 3. 압축 해제 (.gz -> .sql)
echo "[2/3] Decompressing backup payload..."
echo "[2/3] 백업 페이로드 압축 해제 중."
gunzip -f "$BACKUP_DIR/recovery.sql.gz"
test -s "$BACKUP_DIR/recovery.sql"

MASTER_DATA_LINE=$(grep -m1 -E "CHANGE MASTER TO|CHANGE REPLICATION SOURCE TO" "$BACKUP_DIR/recovery.sql" || true)
if [ -n "$MASTER_DATA_LINE" ]; then
    echo "[DR DATABASE] Capturing replication coordinates from dump metadata."
    echo "[DR 데이터베이스] dump 메타데이터에서 replication 좌표를 기록합니다."
    printf '%s\n' "$MASTER_DATA_LINE" > "$BACKUP_DIR/recovery.master_status"
fi

# 4. 데이터 입력 (MySQL)
echo "[3/3] Restoring database into the DR MySQL instance..."
echo "[3/3] DR MySQL 인스턴스로 데이터베이스 복구 중."
mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${MYSQL_ROOT_PASS}'; FLUSH PRIVILEGES;" 2>/dev/null || true
mysql --defaults-extra-file="$MYSQL_DEFAULTS_FILE" -e "CREATE DATABASE IF NOT EXISTS $TARGET_DB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql --defaults-extra-file="$MYSQL_DEFAULTS_FILE" "$TARGET_DB" < "$BACKUP_DIR/recovery.sql"

echo "[DR DATABASE] Database restore workflow completed successfully."
echo "[DR 데이터베이스] 데이터베이스 복구 워크플로 완료."
