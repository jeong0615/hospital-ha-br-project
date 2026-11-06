# MSP

MSP Ubuntu 환경을 새 서버에서 재구성하기 위한 코드, 배포 정의, 선별한 systemd 설정과 inventory를 보관한다. MSP의 실제 `/` 구조를 `rootfs/`로 표현하며, 백업 스크립트는 원본을 이동·삭제하지 않고 Git commit/push도 자동 수행하지 않는다.

<br>

## 구조

```text
msp/
├── README.md
├── .gitignore
├── backup.sh
├── restore.sh
├── inventory/
│   ├── packages.txt
│   ├── manual-packages.txt
│   ├── enabled-services.txt
│   ├── running-services.txt
│   ├── listening-sockets.txt
│   ├── docker-containers.txt
│   ├── docker-images.txt
│   ├── docker-networks.txt
│   ├── docker-volumes.txt
│   └── system-info.txt
└── rootfs/
    ├── etc/systemd/system/
    └── home/cloud/
        ├── dr_project/
        ├── grafana/
        ├── hospital-dr-ansible/
        ├── hospital-dr-codex/
        ├── hospital-dr-repo/
        ├── hospital-dr-terraform/
        ├── scripts/
        ├── wazuh-docker/
        └── zabbix/
```

예를 들어 다음 경로는 서로 대응한다.

```text
msp/rootfs/home/cloud/grafana/docker-compose.yml
→ /home/cloud/grafana/docker-compose.yml

msp/rootfs/etc/systemd/system/hospital-dr-chatops.service
→ /etc/systemd/system/hospital-dr-chatops.service
```

`hospital-dr-codex`에는 자동화, 문서·runbook, Terraform, Monitoring, 운영 코드와 검토된 `.github/workflows/` CI/CD 정의가 포함된다. Grafana provisioning과 최신 DR 대시보드 JSON도 보관하지만 `grafana.db`와 `.env`는 포함하지 않는다.

<br>

## 백업 갱신

```bash
cd /path/to/hospital-project-share-backup/msp
./backup.sh
git status --short
git diff --stat
```

`backup.sh`는 선별한 `/home/cloud` 원본을 `rootfs/home/cloud/`로 동기화하고 MSP 전용 systemd unit을 `rootfs/etc/systemd/system/`에 복사한다. inventory를 갱신하고 Secret 검사를 수행하지만 `git add`, `git commit`, `git push`, 서비스 조작은 하지 않는다.

<br>

## 복구 순서

```bash
cd /path/to/hospital-project-share-backup/msp
./restore.sh --dry-run
./restore.sh --dry-run-home
./restore.sh --dry-run-etc
```

dry-run 결과를 확인한 뒤에만 명시적으로 적용한다.

```bash
./restore.sh --restore-home
sudo ./restore.sh --restore-etc
sudo ./restore.sh --restore-all
```

권장 순서는 OS·계정·패키지 준비 → home 코드 복원 → Docker/Compose 준비 → 외부 Secret 주입 → Grafana/Zabbix/Wazuh 및 DR 구성 검토 → systemd unit 검토 → health check와 failover/failback 검증이다. Ansible 실행과 Terraform `apply/destroy`는 별도 검토와 승인이 필요하다.

<br>

## 제외 항목

`.env`, `.aws`, `.ssh`, Docker credential, Terraform state/tfvars, `*.pem`, `*.key`, `*.secret`, DB/SQLite runtime, `node_modules`, cache, 로그, `*.sql`, `*.dump`, `*.enc`, archive, Wazuh generated credential/certificate와 과거·중복 backup/legacy 디렉터리는 저장하지 않는다. `.env.example`과 검토된 example/template만 허용한다.

AWS, Cloudflare, Slack, DB, ngrok, SSH/WireGuard/TLS, Firebase, Wazuh와 Grafana의 실제 자격증명은 GitHub 외부 Secret 저장소에서 관리한다. 복구 후 Linux owner/group/permission, systemd `ExecStart`·`EnvironmentFile`, Secret 파일의 `0600` 권한을 확인한다.
