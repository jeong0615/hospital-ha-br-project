# EMR (Doc)

EMR 서버를 새 Ubuntu 환경에서 재구성하기 위한 소스, 선별한 시스템 설정과 인벤토리를 보관한다. 원본 서비스의 파일을 이동하거나 삭제하지 않으며, 백업 스크립트가 Git commit/push나 서비스 재시작을 수행하지 않는다.

<br>

## 구조

```text
emr/
├── .gitignore
├── README.md
├── backup.sh
├── restore.sh
├── inventory/
│   ├── active-services.txt
│   ├── config-sources.txt
│   ├── env-variables.txt
│   ├── excluded-items.txt
│   ├── listening-sockets.txt
│   ├── packages.txt
│   ├── projects.txt
│   ├── server-summary.txt
│   └── web-routing.txt
└── rootfs/
    ├── etc/
    │   ├── apache2/
    │   ├── nginx/
    │   ├── openvpn/update-resolv-conf
    │   ├── vsftpd.conf
    │   └── zabbix/zabbix_agentd.conf
    ├── home/hoseo/
    │   ├── doctor_web/
    │   └── hospital_emr/
    └── var/
        ├── ossec/etc/
        └── www/html/
```

`rootfs/`는 EMR Ubuntu의 `/`를 표현한다.

```text
emr/rootfs/home/hoseo/hospital_emr/server.js
→ /home/hoseo/hospital_emr/server.js
```

주요 원본은 `/home/hoseo/hospital_emr`, `/home/hoseo/doctor_web`, Nginx/Apache 설정, `/var/www/html`, Zabbix와 필요한 Wazuh 설정이다.

<br>

## 백업 갱신

```bash
cd /path/to/hospital-project-share-backup/emr
EMR_SSH_TARGET=hoseo@<EMR_HOST> ./backup.sh --dry-run
EMR_SSH_TARGET=hoseo@<EMR_HOST> ./backup.sh --apply
git status --short
git diff --stat
```

`backup.sh`는 `rootfs/`와 `inventory/`를 갱신하고 `.git`, `.env`, 의존성 디렉터리, 로그, 키, Dump, archive와 중복 파일을 제외한다. 알려진 소스의 inline DB/test 비밀번호는 환경변수 참조로 치환한다.

<br>

## 복구

```bash
cd /path/to/hospital-project-share-backup
sudo ./emr/restore.sh --dry-run
sudo ./emr/restore.sh --apply --confirm
```

복구 전 `owner/group/permission`, 대상 경로와 환경변수를 확인한다. 복구 후 필요한 경우에만 `npm ci`, systemd 등록, Nginx 설정 적용과 서비스 시작을 수행한다.

권장 순서는 Ubuntu·런타임 준비 → `rootfs` 복원 → 외부 Secret 주입 → 의존성 설치 → DB 연결 확인 → Nginx/서비스 설정 → EMR health check와 모니터링 검증이다.

<br>

## 제외 항목

`.env`, 실제 DB 비밀번호, 환자 운영 데이터, DB Dump, `node_modules`, 로그, ZIP/설치 패키지, `.git`, SSH/TLS/Wazuh Private Key와 인증서는 저장하지 않는다. `.env.example`과 환경변수 이름만 공개하며 실제 값은 별도 Secret 저장소에서 주입한다.

Linux 계정 UID/GID가 새 서버에서 다를 수 있으므로 파일 복구 후 owner/group과 실행 권한을 확인한다. Apache가 필요한 경우 Nginx와 포트 충돌 여부를 먼저 확인한다.
