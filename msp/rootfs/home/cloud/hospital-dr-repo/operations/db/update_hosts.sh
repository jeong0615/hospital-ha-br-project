#!/bin/bash
WEB_IP="10.20.10.10"
DOC_IP="10.20.20.10"
DB_IP="10.20.30.10"

ssh -i /etc/hospital-dr/ssh/dr_key ubuntu@198.51.100.14 <<EOF
cat <<EOT > ~/scripts/update_hosts.sh
#!/bin/bash
WEB_IP="10.20.10.10"
DB_IP="10.20.30.10"
DOC_IP="10.20.20.10"

cat <<EOT2 > ~/hospital-dr-ansible/hosts
[web_servers]
web_server ansible_host=\$WEB_IP ansible_user=ubuntu ansible_ssh_private_key_file=/etc/hospital-dr/ssh/dr_key

[doc_servers]
doc_server ansible_host=\$DOC_IP ansible_user=ubuntu ansible_ssh_private_key_file=/etc/hospital-dr/ssh/dr_key

[db_servers]
db_server ansible_host=\$DB_IP ansible_user=ubuntu ansible_ssh_private_key_file=/etc/hospital-dr/ssh/dr_key
EOT2
EOT
EOF
