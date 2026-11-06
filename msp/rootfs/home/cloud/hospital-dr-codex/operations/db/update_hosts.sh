#!/bin/bash
set -euo pipefail

DR_SSH_PRIVATE_KEY="${DR_SSH_PRIVATE_KEY:-${SSH_KEY:-${DR_SSH_KEY:-}}}"
if [ -z "$DR_SSH_PRIVATE_KEY" ] || [ ! -r "$DR_SSH_PRIVATE_KEY" ]; then
  echo "[CONFIG ERROR] DR_SSH_PRIVATE_KEY must point to a readable private key."
  exit 1
fi

WEB_IP="10.20.10.10"
DOC_IP="10.20.20.10"
DB_IP="10.20.30.10"

ssh -i "$DR_SSH_PRIVATE_KEY" ubuntu@198.51.100.14 <<EOF
cat <<EOT > ~/scripts/update_hosts.sh
#!/bin/bash
WEB_IP="10.20.10.10"
DB_IP="10.20.30.10"
DOC_IP="10.20.20.10"

cat <<EOT2 > ~/hospital-dr-ansible/hosts
[web_servers]
web_server ansible_host=\$WEB_IP ansible_user=ubuntu ansible_ssh_private_key_file=$DR_SSH_PRIVATE_KEY

[doc_servers]
doc_server ansible_host=\$DOC_IP ansible_user=ubuntu ansible_ssh_private_key_file=$DR_SSH_PRIVATE_KEY

[db_servers]
db_server ansible_host=\$DB_IP ansible_user=ubuntu ansible_ssh_private_key_file=$DR_SSH_PRIVATE_KEY
EOT2
EOT
EOF
