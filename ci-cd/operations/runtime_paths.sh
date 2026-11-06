#!/usr/bin/env bash

# Resolve repository-local paths while allowing an installed deployment to
# override the root without changing the workflow scripts.
_dr_paths_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DR_REPO_ROOT="${DR_REPO_ROOT:-$(cd -- "$_dr_paths_source_dir/.." && pwd)}"
DR_TERRAFORM_DIR="${DR_TERRAFORM_DIR:-$DR_REPO_ROOT/infra/terraform}"
DR_ANSIBLE_DIR="${DR_ANSIBLE_DIR:-$DR_REPO_ROOT/automation/ansible}"
# Keep generated inventory/config artifacts separate from the canonical playbook source.
DR_ANSIBLE_SOURCE_DIR="${DR_ANSIBLE_SOURCE_DIR:-$DR_REPO_ROOT/automation/ansible}"
# One controller-side key contract.  SSH_KEY and DR_SSH_KEY remain accepted
# as compatibility aliases for existing operators and installed wrappers.
DR_SSH_PRIVATE_KEY="${DR_SSH_PRIVATE_KEY:-${SSH_KEY:-${DR_SSH_KEY:-}}}"
DR_SECRET_FILE="${DR_SECRET_FILE:-/home/cloud/hospital-dr-secrets/dr.env}"
DR_STATE_DIR="${DR_STATE_DIR:-/var/lib/hospital-dr}"
DR_SSH_KNOWN_HOSTS_FILE="${DR_SSH_KNOWN_HOSTS_FILE:-${DR_STATE_DIR}/ssh/known_hosts}"
# Production provisioning uses the externally managed Cloudflare Origin CA
# pair by default.  Operators must explicitly opt into a non-production
# self-signed fallback instead of silently accepting one on a fresh host.
DR_TLS_CERT_FILE="${DR_TLS_CERT_FILE:-/home/cloud/hospital-dr-secrets/tls/hospital-web.crt}"
DR_TLS_KEY_FILE="${DR_TLS_KEY_FILE:-/home/cloud/hospital-dr-secrets/tls/hospital-web.key}"
REQUIRE_PRODUCTION_TLS="${REQUIRE_PRODUCTION_TLS:-true}"
DR_OPERATIONS_DIR="${DR_OPERATIONS_DIR:-$DR_REPO_ROOT/operations}"
DR_DB_DIR="${DR_DB_DIR:-$DR_OPERATIONS_DIR/db}"
DR_FAILOVER_DIR="${DR_FAILOVER_DIR:-$DR_OPERATIONS_DIR/failover}"
DR_FINOPS_DIR="${DR_FINOPS_DIR:-$DR_OPERATIONS_DIR/finops}"
DR_NAT_DIR="${DR_NAT_DIR:-$DR_OPERATIONS_DIR/nat}"
DR_TIMING_SCRIPT="${DR_TIMING_SCRIPT:-$DR_DB_DIR/dr_timing.sh}"
DR_ZBX_SCRIPT="${DR_ZBX_SCRIPT:-$DR_DB_DIR/zbx_send.sh}"
DR_CLOUDFLARE_AUTH_SCRIPT="${DR_CLOUDFLARE_AUTH_SCRIPT:-$DR_OPERATIONS_DIR/cloudflare_ssm_auth.sh}"
DR_LOG_DIR="${DR_LOG_DIR:-$DR_REPO_ROOT/var/log}"

DR_RUNTIME_DIR="${DR_RUNTIME_DIR:-${DR_OPERATIONS_DIR}/runtime}"
DR_STATE_HELPER="${DR_STATE_HELPER:-${DR_RUNTIME_DIR}/dr_state.sh}"
DR_DNS_HELPER="${DR_DNS_HELPER:-${DR_FAILOVER_DIR}/dns_transaction.sh}"

# Keep host-key verification enabled while allowing a genuinely new DR host
# to be enrolled non-interactively. OpenSSH still rejects a changed key; an
# operator must explicitly rotate that entry when an instance is replaced.
dr_prepare_ssh_known_hosts() {
    local known_hosts_file="${DR_SSH_KNOWN_HOSTS_FILE:-}"
    local known_hosts_dir

    if [ -z "$known_hosts_file" ] || [ "$known_hosts_file" = "/dev/null" ]; then
        echo "[SSH CONFIG ERROR] DR_SSH_KNOWN_HOSTS_FILE must reference a persistent file." >&2
        return 1
    fi

    known_hosts_dir="$(dirname -- "$known_hosts_file")"
    if [ ! -d "$known_hosts_dir" ]; then
        install -d -m 0700 -- "$known_hosts_dir"
    fi
    if [ -e "$known_hosts_file" ] && [ ! -f "$known_hosts_file" ]; then
        echo "[SSH CONFIG ERROR] DR_SSH_KNOWN_HOSTS_FILE is not a regular file: $known_hosts_file" >&2
        return 1
    fi
    if [ ! -e "$known_hosts_file" ]; then
        (umask 077 && touch -- "$known_hosts_file")
    fi
    chmod 0600 -- "$known_hosts_file"
}

unset _dr_paths_source_dir
