#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../../operations/runtime_paths.sh"
# shellcheck disable=SC1091
source "$DR_STATE_HELPER"
# shellcheck disable=SC1091
source "$DR_DNS_HELPER"
source "$DR_CLOUDFLARE_AUTH_SCRIPT"

LOCK_FILE="${DR_FAILOVER_LOCK_FILE:-${DR_STATE_DIR}/failover.lock}"
WORKFLOW_LOCK_FILE="${DR_WORKFLOW_LOCK_FILE:-${DR_STATE_DIR}/workflow.lock}"
TF_LOCK_FILE="${DR_TERRAFORM_LOCK_FILE:-${DR_STATE_DIR}/terraform.lock}"
TF_DIR="${TF_DIR:-$DR_TERRAFORM_DIR}"
NAT_ENABLE_SCRIPT="${NAT_ENABLE_SCRIPT:-$DR_NAT_DIR/nat_enable.sh}"
NAT_STATUS_SCRIPT="${NAT_STATUS_SCRIPT:-$DR_NAT_DIR/nat_status.sh}"
ANSIBLE_DIR="${ANSIBLE_DIR:-$DR_ANSIBLE_DIR}"
ANSIBLE_SOURCE_DIR="${DR_ANSIBLE_SOURCE_DIR:-$DR_REPO_ROOT/automation/ansible}"
INVENTORY="${INVENTORY:-$ANSIBLE_DIR/inventory.ini}"
LOG_FILE="${LOG_FILE:-$DR_LOG_DIR/failover.log}"
mkdir -p "$(dirname -- "$LOG_FILE")"

if [ -r "$DR_ZBX_SCRIPT" ]; then
  # Metrics are best-effort; Zabbix must never block DR execution.
  # shellcheck disable=SC1091
  source "$DR_ZBX_SCRIPT"
else
  zbx_send() { :; }
  zbx_send_now() { :; }
fi
if [ -r "$DR_TIMING_SCRIPT" ]; then
  # shellcheck disable=SC1091
  source "$DR_TIMING_SCRIPT"
else
  dr_timing_start() { printf '0\n'; }
  dr_timing_end() { :; }
  dr_rto_record() { :; }
fi

FAILOVER_START_TS="${DR_FAILOVER_START_TS:-$(date +%s)}"
if [ "${DR_FAILOVER_METRICS_STARTED:-0}" != "1" ]; then
  export DR_FAILOVER_METRICS_STARTED=1
  export DR_FAILOVER_START_TS="$FAILOVER_START_TS"
  zbx_send dr.failover.status 1 2>/dev/null || true
fi

failover_metric_failed() {
  zbx_send dr.failover.status 3 2>/dev/null || true
}

dr_state_prepare
dr_workflow_lock_prepare
DR_STATE_FAILURE_STATUS=FAILED
trap 'rc=$?; dr_state_mark_failure "${DR_STATE_FAILURE_STATUS:-FAILED}" 2>/dev/null || true; failover_metric_failed; exit "$rc"' ERR
trap 'rc=$?; if [ "$rc" -ne 0 ]; then dr_state_mark_failure "${DR_STATE_FAILURE_STATUS:-FAILED}" 2>/dev/null || true; fi' EXIT

# Hold the shared workflow and Terraform locks in this process so failures
# from the workflow do not pass through a parent wrapper that could overwrite
# the persistent result state.
if [ "${DR_WORKFLOW_LOCK_HELD:-0}" != "1" ]; then
  exec 8>"$WORKFLOW_LOCK_FILE"
  if ! flock -n 8; then
    echo "[DR FAILOVER] Another DR workflow is already in progress."
    echo "[DR 페일오버] 이미 다른 DR 워크플로가 실행 중입니다."
    exit 1
  fi
fi
export DR_WORKFLOW_LOCK_HELD=1

if [ "${DR_FAILOVER_LOCK_HELD:-0}" != "1" ]; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[DR FAILOVER] Another failover workflow is already in progress."
    echo "[DR 페일오버] 이미 다른 페일오버 워크플로가 실행 중입니다."
    exit 1
  fi
fi

if [ "${DR_TERRAFORM_LOCK_HELD:-0}" != "1" ]; then
  exec 7>"$TF_LOCK_FILE"
  if ! flock -n 7; then
    echo "[PROVISIONING ERROR] Another Terraform operation is already in progress."
    echo "[프로비저닝 오류] 다른 Terraform 작업이 이미 실행 중입니다."
    exit 1
  fi
fi
export DR_TERRAFORM_LOCK_HELD=1

exec > >(tee -a "$LOG_FILE") 2>&1
workflow_timing_start="$(dr_timing_start failover)"
echo "=================================================="
echo "[$(date)] DR failover workflow started."
echo "[$(date)] DR 페일오버 워크플로 시작."
echo "=================================================="
dr_rto_record failover_started_at
dr_state_write FAILOVER_EXECUTING
dr_state_flag_clear

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "[CONFIG ERROR] Required environment variable is missing: $name"
    echo "[설정 오류] 필수 환경변수가 없습니다: $name"
    exit 1
  fi
}

load_secret_env() {
  local secret_file="${DR_SECRET_FILE:-/etc/hospital-dr/dr.env}"
  if [ -f "$secret_file" ]; then
    # shellcheck disable=SC1090
    source "$secret_file"
  fi
  if [ -n "${DR_PROVISIONING_SECRET_FILE:-}" ] \
      && [ "${DR_PROVISIONING_SECRET_FILE}" != "$secret_file" ]; then
    if [ ! -f "$DR_PROVISIONING_SECRET_FILE" ]; then
      echo "[CONFIG ERROR] DR_PROVISIONING_SECRET_FILE does not exist: $DR_PROVISIONING_SECRET_FILE"
      echo "[설정 오류] DR_PROVISIONING_SECRET_FILE이 존재하지 않습니다: $DR_PROVISIONING_SECRET_FILE"
      exit 1
    fi
    # shellcheck disable=SC1090
    source "$DR_PROVISIONING_SECRET_FILE"
  fi
}

wait_for_ssh() {
  local host="$1"
  local user="$2"
  local key="$3"
  local args="${4:-}"
  local max_attempts="${5:-30}"

  echo "[CONNECTIVITY] Waiting for SSH readiness: ${user}@${host}"
  echo "[접속 확인] SSH 접속 가능 상태 대기: ${user}@${host}"
  for attempt in $(seq 1 "$max_attempts"); do
    if ssh -i "$key" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DR_SSH_KNOWN_HOSTS_FILE" -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR $args "${user}@${host}" "true" >/dev/null 2>&1; then
      echo "[CONNECTIVITY] SSH readiness confirmed: ${host}"
      echo "[접속 확인] SSH 접속 가능 확인: ${host}"
      return 0
    fi
    echo "[CONNECTIVITY] SSH not ready yet (${attempt}/${max_attempts}): ${host}"
    echo "[접속 대기] SSH 아직 준비되지 않음 (${attempt}/${max_attempts}): ${host}"
    sleep 10
  done

  echo "[CONNECTIVITY ERROR] SSH readiness check failed: ${host}"
  echo "[접속 오류] SSH 준비 상태 확인 실패: ${host}"
  return 1
}

wait_for_ssh_via_web() {
  local host="$1"
  local user="$2"
  local key="$3"
  local web_ip="$4"
  local max_attempts="${5:-30}"
  local attempt proxy_command

  proxy_command="ssh -i ${key} -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${DR_SSH_KNOWN_HOSTS_FILE} -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ubuntu@${web_ip}"

  echo "[CONNECTIVITY] Waiting for SSH readiness through DR Web bastion: ${user}@${host}"
  echo "[접속 확인] DR Web bastion 경유 SSH 접속 가능 상태 대기: ${user}@${host}"
  for attempt in $(seq 1 "$max_attempts"); do
    if ssh -i "$key" \
      -o BatchMode=yes \
      -o ConnectTimeout=5 \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$DR_SSH_KNOWN_HOSTS_FILE" \
      -o GlobalKnownHostsFile=/dev/null \
      -o LogLevel=ERROR \
      -o "ProxyCommand=${proxy_command}" \
      "${user}@${host}" "true" >/dev/null 2>&1; then
      echo "[CONNECTIVITY] SSH readiness confirmed: ${host}"
      echo "[접속 확인] SSH 접속 가능 확인: ${host}"
      return 0
    fi
    echo "[CONNECTIVITY] SSH not ready yet (${attempt}/${max_attempts}): ${host}"
    echo "[접속 대기] SSH 아직 준비되지 않음 (${attempt}/${max_attempts}): ${host}"
    sleep 10
  done

  echo "[CONNECTIVITY ERROR] SSH readiness check failed: ${host}"
  echo "[접속 오류] SSH 준비 상태 확인 실패: ${host}"
  return 1
}

load_secret_env
load_cloudflare_ssm_token
CF_ZONE_ID="${CF_ZONE_ID:-}"
DNS_TX_DIR="${DR_DNS_TX_DIR:-${DR_STATE_DIR}/dns/failover-${FAILOVER_START_TS}-${BASHPID}}"
mkdir -p "$DNS_TX_DIR"
chmod 0750 "$DNS_TX_DIR"
DR_DOMAIN="${DR_DOMAIN:-hospital-web.pp.ua}"
EMR_DOMAIN="${EMR_DOMAIN:-emr.hospital-web.pp.ua}"
EMR_HEALTH_CHECK_HEADER="${EMR_HEALTH_CHECK_HEADER:-dr-emr-check-2026}"
SSH_KEY="${SSH_KEY:-${DR_SSH_PRIVATE_KEY:-${DR_SSH_KEY:-}}}"
DR_DOC_PRIVATE_IP="${DR_DOC_PRIVATE_IP:-10.20.20.10}"
DR_DB_PRIVATE_IP="${DR_DB_PRIVATE_IP:-10.20.30.10}"
ANSIBLE_LIMIT="${ANSIBLE_LIMIT:-all}"
DB_PROMOTE_ON_FAILOVER="${DB_PROMOTE_ON_FAILOVER:-true}"
if [ "${DR_PROVISION_ONLY:-false}" = "true" ]; then
  DB_PROMOTE_ON_FAILOVER=false
fi
DB_ALLOW_EXISTING_DATA="${DB_ALLOW_EXISTING_DATA:-false}"
DB_BACKUP_FILE="${DB_BACKUP_FILE:-}"
DB_NR_ENABLED="${DB_NR_ENABLED:-false}"
DB_SOURCE_HOST="${DB_SOURCE_HOST:-}"
DB_SOURCE_PORT="${DB_SOURCE_PORT:-3306}"
DB_SOURCE_LOG_FILE="${DB_SOURCE_LOG_FILE:-}"
DB_SOURCE_LOG_POS="${DB_SOURCE_LOG_POS:-}"
DB_REPL_USER="${DB_REPL_USER:-repl1}"
DB_REPL_SERVER_ID="${DB_REPL_SERVER_ID:-20}"
DB_REPL_MODE="${DB_REPL_MODE:-position}"
ECR_REGION="${ECR_REGION:-${AWS_REGION:-ap-northeast-2}}"
ECR_REGISTRY="${ECR_REGISTRY:-}"
ECR_ACCOUNT_ID="${ECR_ACCOUNT_ID:-}"
ECR_WEB_REPOSITORY="${ECR_WEB_REPOSITORY:-hospital-dr-web}"
ECR_EMR_REPOSITORY="${ECR_EMR_REPOSITORY:-hospital-dr-emr}"
DR_IMAGE_TAG="${DR_IMAGE_TAG:-}"
if [ -z "$DR_IMAGE_TAG" ]; then
  DR_IMAGE_TAG="$(git -C "$DR_REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
export MYSQL_ROOT_PASS DB_REPL_PASSWORD DB_BACKUP_ENCRYPTION_KEY WEB_DB_PASSWORD DOC_DB_PASSWORD SESSION_SECRET
export DB_BACKUP_FILE DB_NR_ENABLED DB_SOURCE_HOST DB_SOURCE_PORT DB_SOURCE_LOG_FILE DB_SOURCE_LOG_POS
export DB_REPL_USER DB_REPL_SERVER_ID DB_REPL_MODE DB_ALLOW_EXISTING_DATA DB_PROMOTE_ON_FAILOVER
export DR_IMAGE_TAG ECR_REGION ECR_REGISTRY ECR_ACCOUNT_ID ECR_WEB_REPOSITORY ECR_EMR_REPOSITORY
export DR_TLS_CERT_FILE DR_TLS_KEY_FILE REQUIRE_PRODUCTION_TLS
if [ ! -f "$ANSIBLE_SOURCE_DIR/site.yml" ] || [ ! -f "$ANSIBLE_SOURCE_DIR/ansible.cfg" ]; then
  echo "[CONFIG ERROR] Canonical Ansible source is incomplete: $ANSIBLE_SOURCE_DIR"
  exit 1
fi
require_env "CF_ZONE_ID"
require_env "SSH_KEY"
if [ -z "${TF_VAR_management_ipv4_cidrs:-}" ] \
    && [ -n "${DR_MANAGEMENT_IPV4_CIDRS:-}" ]; then
  export TF_VAR_management_ipv4_cidrs="$DR_MANAGEMENT_IPV4_CIDRS"
fi
require_env "TF_VAR_management_ipv4_cidrs"
if [ ! -r "$SSH_KEY" ]; then
  echo "[CONFIG ERROR] SSH private key is not readable: $SSH_KEY"
  exit 1
fi
TF_VAR_ssh_public_key_path="${TF_VAR_ssh_public_key_path:-${SSH_KEY}.pub}"
if [ ! -r "$TF_VAR_ssh_public_key_path" ]; then
  echo "[CONFIG ERROR] SSH public key is not readable: $TF_VAR_ssh_public_key_path"
  exit 1
fi
export TF_VAR_ssh_public_key_path
export ANSIBLE_CONFIG="${ANSIBLE_CONFIG:-$ANSIBLE_SOURCE_DIR/ansible.cfg}"
dr_prepare_ssh_known_hosts
export ANSIBLE_HOST_KEY_CHECKING="${ANSIBLE_HOST_KEY_CHECKING:-True}"
export ANSIBLE_DEPRECATION_WARNINGS="${ANSIBLE_DEPRECATION_WARNINGS:-False}"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}"
if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "db_servers" ]; then
  require_env "MYSQL_ROOT_PASS"
  require_env "WEB_DB_PASSWORD"
  require_env "DOC_DB_PASSWORD"
  if [ "${DB_NR_ENABLED:-false}" = "true" ]; then
    require_env "DB_SOURCE_HOST"
    require_env "DB_REPL_PASSWORD"
  fi
  if [ "$DB_ALLOW_EXISTING_DATA" = "true" ]; then
    if [ -n "${DB_BACKUP_ENCRYPTION_KEY:-}" ] || [ -n "$DB_BACKUP_FILE" ]; then
      echo "[CONFIG ERROR] DB_ALLOW_EXISTING_DATA=true cannot be combined with backup restore inputs."
      echo "[설정 오류] DB_ALLOW_EXISTING_DATA=true와 backup 복구 입력을 함께 사용할 수 없습니다."
      exit 1
    fi
  elif [ -z "${DB_BACKUP_ENCRYPTION_KEY:-}" ] || [ -z "$DB_BACKUP_FILE" ]; then
    echo "[CONFIG ERROR] Fresh DB recovery requires DB_BACKUP_ENCRYPTION_KEY and DB_BACKUP_FILE, or use DB_ALLOW_EXISTING_DATA=true only for a known pre-populated host."
    echo "[설정 오류] 새 DB 복구에는 DB_BACKUP_ENCRYPTION_KEY와 DB_BACKUP_FILE이 모두 필요하며, DB_ALLOW_EXISTING_DATA=true는 이미 데이터가 있는 host에서만 사용하십시오."
    exit 1
  elif [[ ! "$DB_BACKUP_FILE" =~ \.sql\.gz\.enc$ ]]; then
    echo "[CONFIG ERROR] DB_BACKUP_FILE must use the .sql.gz.enc naming contract."
    echo "[설정 오류] DB_BACKUP_FILE은 .sql.gz.enc 명명 규칙을 따라야 합니다."
    exit 1
  fi
fi

if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "web_servers" ] || [ "$ANSIBLE_LIMIT" = "doc_servers" ]; then
  if ! [[ "$DR_IMAGE_TAG" =~ ^[0-9a-fA-F]{40}$ ]]; then
    echo "[CONFIG ERROR] DR_IMAGE_TAG must be a 40-character commit SHA for Docker deployment."
    echo "[설정 오류] Docker 배포에는 40자리 commit SHA인 DR_IMAGE_TAG가 필요합니다."
    exit 1
  fi
fi

if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "web_servers" ]; then
  require_env "WEB_DB_PASSWORD"
  require_env "SESSION_SECRET"
fi

if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "doc_servers" ]; then
  require_env "DOC_DB_PASSWORD"
fi

cd "$TF_DIR"

NAT_TERRAFORM_ENABLE=false
state_list=""
if [ -r "$TF_DIR/terraform.tfstate" ]; then
  if ! state_list="$(terraform state list 2>/dev/null)"; then
    echo "[NAT CONFIG ERROR] Operational Terraform state could not be read before full provisioning."
    echo "[NAT 설정 오류] 전체 프로비저닝 전에 운영 Terraform state를 읽지 못했습니다."
    exit 1
  fi
fi

if [ -n "$state_list" ]; then
  if ! printf '%s\n' "$state_list" | grep -Fqx "aws_vpc.dr_vpc"; then
    echo "[NAT CONFIG ERROR] Non-empty Terraform state does not contain the approved DR VPC."
    echo "[NAT 설정 오류] 비어 있지 않은 Terraform state에 승인된 DR VPC가 없습니다."
    exit 1
  fi
  has_nat=0
  has_route=0
  if printf '%s\n' "$state_list" | grep -Eq '^aws_nat_gateway\.nat_gw(\[0\])?$'; then
    has_nat=1
  fi
  if printf '%s\n' "$state_list" | grep -Eq '^aws_route\.private_internet(\[0\])?$'; then
    has_route=1
  fi
  if [ "$has_nat" -ne "$has_route" ]; then
    echo "[NAT CONFIG ERROR] Terraform state contains only one side of the NAT/route pair."
    echo "[NAT 설정 오류] Terraform state에 NAT/route 쌍 중 하나만 존재합니다."
    exit 1
  fi
  if [ "$has_nat" -eq 1 ]; then
    echo "[NAT] Existing NAT state is validated by operations/nat before full provisioning."
    echo "[NAT] 전체 프로비저닝 전에 기존 NAT state를 operations/nat로 검증합니다."
    "$NAT_ENABLE_SCRIPT"
    NAT_TERRAFORM_ENABLE=true
  else
    echo "[NAT] Existing operational state is NAT-OFF; verifying AWS is also OFF."
    echo "[NAT] 기존 운영 state가 NAT-OFF이므로 AWS 상태도 OFF인지 확인합니다."
    "$NAT_STATUS_SCRIPT"
  fi
else
  echo "[NAT] Fresh/base Terraform state detected; Terraform will create the foundation without NAT."
  echo "[NAT] 신규/기초 Terraform state이므로 Terraform은 NAT 없이 기반만 생성합니다."
fi

echo "[PROVISIONING] Applying Terraform plan for DR infrastructure..."
echo "[프로비저닝] DR 인프라 Terraform 적용 시작..."
terraform_timing_start="$(dr_timing_start terraform)"
terraform apply -var="is_dr_mode=true" -var="enable_nat_gateway=$NAT_TERRAFORM_ENABLE" -var="cloudflare_zone_id=$CF_ZONE_ID" -auto-approve
dr_timing_end terraform "$terraform_timing_start"
dr_rto_record terraform_completed_at
echo "[PROVISIONING] Terraform apply completed successfully."
echo "[프로비저닝] Terraform 적용 완료."

if [ "$NAT_TERRAFORM_ENABLE" = "false" ]; then
  echo "[NAT] Enabling the approved NAT only through operations/nat after foundation provisioning."
  echo "[NAT] 기반 프로비저닝 후 승인된 NAT는 operations/nat를 통해서만 활성화합니다."
  "$NAT_ENABLE_SCRIPT"
fi

if [ -n "${NAT_EIP_PUBLIC_IP:-}" ]; then
  actual_nat_eip="$(terraform output -raw nat_eip_public_ip)"
  if [ "$actual_nat_eip" != "$NAT_EIP_PUBLIC_IP" ]; then
    echo "[CONFIG ERROR] Terraform NAT EIP $actual_nat_eip does not match the required $NAT_EIP_PUBLIC_IP."
    echo "[설정 오류] Terraform NAT EIP가 요구된 값과 일치하지 않습니다."
    exit 1
  fi
fi

echo "[CONFIGURATION] Generating Ansible inventory for DR hosts..."
echo "[구성 관리] DR 대상 호스트 Ansible inventory 생성..."
WEB_PUB_IP=$(terraform output -raw web_server_public_ip)

SSH_COMMON="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${DR_SSH_KNOWN_HOSTS_FILE} -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR"
SSH_PROXY_COMMAND="ssh -i ${SSH_KEY} ${SSH_COMMON} -W %h:%p ubuntu@${WEB_PUB_IP}"
SSH_JUMP_COMMON="${SSH_COMMON} -o ProxyCommand=\"${SSH_PROXY_COMMAND}\""

cat <<EOF > "$INVENTORY"
[web_servers]
$WEB_PUB_IP ansible_connection=ssh ansible_user=ubuntu ansible_ssh_private_key_file=$SSH_KEY ansible_ssh_common_args='${SSH_COMMON}'

[doc_servers]
$DR_DOC_PRIVATE_IP ansible_connection=ssh ansible_user=ubuntu ansible_ssh_private_key_file=$SSH_KEY ansible_ssh_common_args='${SSH_JUMP_COMMON}'

[db_servers]
$DR_DB_PRIVATE_IP ansible_connection=ssh ansible_user=ubuntu ansible_ssh_private_key_file=$SSH_KEY ansible_ssh_common_args='${SSH_JUMP_COMMON}'
EOF
echo "[CONFIGURATION] Ansible inventory generation completed."
echo "[구성 관리] Ansible inventory 생성 완료."

wait_for_ssh "$WEB_PUB_IP" "ubuntu" "$SSH_KEY"
if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "doc_servers" ]; then
  wait_for_ssh_via_web "$DR_DOC_PRIVATE_IP" "ubuntu" "$SSH_KEY" "$WEB_PUB_IP"
fi
if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "db_servers" ]; then
  wait_for_ssh_via_web "$DR_DB_PRIVATE_IP" "ubuntu" "$SSH_KEY" "$WEB_PUB_IP"
fi

echo "[CONFIGURATION] Running Ansible recovery playbook with limit: ${ANSIBLE_LIMIT}"
echo "[구성 관리] Ansible 복구 playbook 실행 범위: ${ANSIBLE_LIMIT}"
ansible_timing_start="$(dr_timing_start ansible)"
cd "$ANSIBLE_SOURCE_DIR"
ansible-playbook -i "$INVENTORY" site.yml --limit "$ANSIBLE_LIMIT"
dr_timing_end ansible "$ansible_timing_start"
dr_rto_record ansible_completed_at
echo "[CONFIGURATION] Ansible recovery playbook completed successfully."
echo "[구성 관리] Ansible 복구 playbook 완료."

echo "[VALIDATION] Starting DR web service health check..."
echo "[검증] DR Web 서비스 health check 시작..."
health_timing_start="$(dr_timing_start health)"
for attempt in $(seq 1 30); do
  if curl -kfsS "https://${WEB_PUB_IP}/" >/dev/null; then
    echo "[VALIDATION] DR web origin HTTPS health check passed: https://${WEB_PUB_IP}/"
    echo "[검증] DR Web 원본 HTTPS 정상 응답 확인: https://${WEB_PUB_IP}/"
    break
  fi

  if [ "$attempt" -eq 30 ]; then
    echo "[VALIDATION ERROR] DR web origin HTTPS health check failed: https://${WEB_PUB_IP}/"
    echo "[검증 오류] DR Web 원본 HTTPS health check 실패: https://${WEB_PUB_IP}/"
    exit 1
  fi

  echo "[VALIDATION] Waiting for DR web service response (${attempt}/30)."
  echo "[검증 대기] DR Web 서비스 응답 대기 (${attempt}/30)."
  sleep 5
done

if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "doc_servers" ]; then
  echo "[VALIDATION] Starting DR EMR service health check..."
  echo "[검증] DR EMR 서비스 health check 시작..."
  for attempt in $(seq 1 30); do
    if curl -kfsS -H "X-Hospital-DR-Check: ${EMR_HEALTH_CHECK_HEADER}" "https://${WEB_PUB_IP}/emr/" >/dev/null; then
      echo "[VALIDATION] DR EMR origin HTTPS health check passed: https://${WEB_PUB_IP}/emr/"
      echo "[검증] DR EMR 원본 HTTPS 정상 응답 확인: https://${WEB_PUB_IP}/emr/"
      break
    fi

    if [ "$attempt" -eq 30 ]; then
      echo "[VALIDATION ERROR] DR EMR origin HTTPS health check failed: https://${WEB_PUB_IP}/emr/"
      echo "[검증 오류] DR EMR 원본 HTTPS health check 실패: https://${WEB_PUB_IP}/emr/"
      exit 1
    fi

    echo "[VALIDATION] Waiting for DR EMR service response (${attempt}/30)."
    echo "[검증 대기] DR EMR 서비스 응답 대기 (${attempt}/30)."
    sleep 5
  done

  for attempt in $(seq 1 30); do
    if curl -kfsS -H "Host: ${EMR_DOMAIN}" -H "X-Hospital-DR-Check: ${EMR_HEALTH_CHECK_HEADER}" "https://${WEB_PUB_IP}/" >/dev/null; then
      echo "[VALIDATION] DR EMR subdomain HTTPS health check passed: https://${EMR_DOMAIN}/"
      echo "[검증] DR EMR 서브도메인 HTTPS 정상 응답 확인: https://${EMR_DOMAIN}/"
      break
    fi

    if [ "$attempt" -eq 30 ]; then
      echo "[VALIDATION ERROR] DR EMR subdomain HTTPS health check failed: https://${EMR_DOMAIN}/"
      echo "[검증 오류] DR EMR 서브도메인 HTTPS health check 실패: https://${EMR_DOMAIN}/"
      exit 1
    fi

    echo "[VALIDATION] Waiting for DR EMR subdomain response (${attempt}/30)."
    echo "[검증 대기] DR EMR 서브도메인 응답 대기 (${attempt}/30)."
    sleep 5
  done
fi

if [ "$ANSIBLE_LIMIT" = "all" ] || [ "$ANSIBLE_LIMIT" = "db_servers" ]; then
  echo "[VALIDATION] Checking DR DB service readiness through Ansible."
  echo "[검증] Ansible을 통해 DR DB 서비스 준비 상태 확인."
  ansible -i "$INVENTORY" db_servers \
    -m shell \
    -a "
mysql_defaults_file=\$(mktemp)
cleanup_mysql_defaults() { rm -f \"\$mysql_defaults_file\"; }
trap cleanup_mysql_defaults EXIT
chmod 600 \"\$mysql_defaults_file\"
{
  printf '[client]\\n'
  printf 'user=root\\n'
  printf 'password=%s\\n' '${MYSQL_ROOT_PASS}'
} > \"\$mysql_defaults_file\"
mysqladmin --defaults-extra-file=\"\$mysql_defaults_file\" ping --silent
mysql --defaults-extra-file=\"\$mysql_defaults_file\" -e \"SELECT @@read_only AS read_only, @@super_read_only AS super_read_only;\"
"
fi

echo "[DNS] Origin health checks passed; snapshotting current Cloudflare records before cutover."
echo "[DNS] 원본 health check 통과 후 DNS 전환 전 현재 Cloudflare record를 snapshot합니다."
dns_timing_start="$(dr_timing_start dns.cutover)"
CF_RECORD_ID="$(dr_dns_snapshot_record "$DR_DOMAIN" "$DNS_TX_DIR/web.json" "${CF_RECORD_ID:-}")"
CF_EMR_RECORD_ID="$(dr_dns_snapshot_record "$EMR_DOMAIN" "$DNS_TX_DIR/emr.json" "${CF_EMR_RECORD_ID:-}")"

CF_PAYLOAD="{\"type\":\"A\",\"name\":\"${DR_DOMAIN}\",\"content\":\"${WEB_PUB_IP}\",\"ttl\":1,\"proxied\":true}"
CF_EMR_PAYLOAD="{\"type\":\"A\",\"name\":\"${EMR_DOMAIN}\",\"content\":\"${WEB_PUB_IP}\",\"ttl\":1,\"proxied\":true}"
if dr_dns_apply_records "$DNS_TX_DIR" \
  "$CF_RECORD_ID" "$CF_PAYLOAD" "failover:${DR_DOMAIN}" \
  "$CF_EMR_RECORD_ID" "$CF_EMR_PAYLOAD" "failover:${EMR_DOMAIN}"; then
  :
else
  dns_rc=$?
  if [ "$dns_rc" -eq 2 ]; then
    DR_STATE_FAILURE_STATUS=PARTIAL_FAILURE
  fi
  dr_state_write "$DR_STATE_FAILURE_STATUS" 2>/dev/null || true
  exit "$dns_rc"
fi

echo "[DNS] Cloudflare proxied DNS records switched to the AWS DR web endpoint."
echo "[DNS] Cloudflare proxied DNS record를 AWS DR Web endpoint로 전환했습니다."

public_health_passed=0
public_health_attempts="${PUBLIC_HEALTH_ATTEMPTS:-30}"
for attempt in $(seq 1 "$public_health_attempts"); do
  if curl -fsS --connect-timeout 5 --max-time 15 "https://${DR_DOMAIN}/" >/dev/null \
     && curl -fsS --connect-timeout 5 --max-time 15 \
       -H "X-Hospital-DR-Check: ${EMR_HEALTH_CHECK_HEADER}" "https://${EMR_DOMAIN}/emr/" >/dev/null; then
    public_health_passed=1
    echo "[VALIDATION] Public Web and EMR health checks passed after DNS cutover."
    break
  fi
  echo "[VALIDATION] Waiting for public DNS health (${attempt}/${public_health_attempts})."
  sleep "${PUBLIC_HEALTH_INTERVAL_SECONDS:-5}"
done
if [ "$public_health_passed" -ne 1 ]; then
  echo "[VALIDATION ERROR] Public health failed after DNS cutover; attempting DNS rollback."
  if dr_dns_rollback_snapshots "$DNS_TX_DIR"; then
    DR_STATE_FAILURE_STATUS=FAILED
    dr_state_write FAILED 2>/dev/null || true
  else
    DR_STATE_FAILURE_STATUS=PARTIAL_FAILURE
    dr_state_write PARTIAL_FAILURE 2>/dev/null || true
  fi
  exit 1
fi
dr_timing_end dns.cutover "$dns_timing_start"
dr_state_write DR_ACTIVE
dr_rto_record service_recovered_at

echo "--------------------------------------------------"
dr_timing_end health "$health_timing_start"
zbx_send_now dr.dns.cutover_at
zbx_send_now dr.health.passed_at
zbx_send dr.rto.seconds "$(( $(date +%s) - FAILOVER_START_TS ))"
zbx_send dr.failover.status 2
dr_timing_end failover "$workflow_timing_start"
echo "[DR FAILOVER] Service provisioning and validation completed successfully."
echo "[DR 페일오버] 서비스 프로비저닝 및 검증 완료."
