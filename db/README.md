# DB Server

DB Server를 재구성하고 유지보수하기 위한 MySQL 설정, 백업 스크립트, systemd 정의와 인벤토리를 보관한다. 이 디렉터리에는 평문 DB Dump, 암호화 백업 파일, 복호화 Key 또는 운영 비밀번호를 저장하지 않는다.

## 구조

```text
db/
├── .gitignore
├── README.md
├── configs/
│   └── etc/mysql/
│       ├── conf.d/
│       ├── mysql.cnf
│       └── mysql.conf.d/
├── inventory/
│   ├── backup-sources.txt
│   ├── config-sources.txt
│   ├── cron-db-backup.txt
│   ├── enabled-services.txt
│   ├── mysql-tables.txt
│   ├── packages.txt
│   └── server-summary.txt
├── scripts/
│   ├── db_backup.sh
│   └── onprem_db_preflight.sh
└── systemd/
    ├── hospital-db-backup.service
    └── hospital-db-backup.timer
```

`configs/etc/mysql/`는 `/etc/mysql/`의 비밀이 아닌 설정을 원래 경로 기준으로 표현한다. `inventory/`에는 패키지, 서비스, 백업 경로, 테이블과 서버 요약을 저장하며 계정 비밀번호와 Secret은 포함하지 않는다.

## 백업 스크립트

`scripts/db_backup.sh`는 다음 값을 외부 Secret 저장소 또는 보호된 환경에서 주입받는 템플릿이다.

```text
DB_BACKUP_ENCRYPTION_KEY
```

백업 흐름은 `mysqldump → gzip → OpenSSL 암호화 → Master Status 저장 → 원격 전송` 순서다. 암호화 파일과 복호화 Key는 GitHub에 업로드하지 않는다.

`scripts/onprem_db_preflight.sh`는 DB 복구·failback 전에 필요한 호스트, 서비스, 경로와 사전 조건을 점검한다. `systemd/`의 service/timer는 새 서버에서 내용을 검토한 뒤에만 enable/start한다.

## 복구 순서

1. 새 Ubuntu에 동일 계열의 MySQL과 필요한 패키지를 설치한다.
2. `inventory/`와 `configs/etc/mysql/`을 검토한다.
3. DB 이름, 계정, 비밀번호와 암호화 Key를 별도 Secret 저장소에서 준비한다.
4. 별도 보관한 암호화 Dump를 복호화하고 접근 통제된 위치에서 Import한다.
5. Dump 시점의 Master Status와 Binary Log 위치를 확인한다.
6. Replication Source, 권한, 네트워크와 데이터 일관성을 검증한다.
7. service/timer는 검토 후 필요한 경우에만 daemon-reload, enable/start한다.

이 Repository의 DB 디렉터리는 코드와 복구 절차용이며 실제 운영 데이터 보관소가 아니다.

## 제외 항목

다음은 GitHub에 저장하지 않는다.

- `*.sql`, `*.dump`, `*.enc`, `*.gz`, `db_backups/`
- DB raw data directory와 runtime log
- `/etc/mysql/debian.cnf` 및 TLS/SSH Private Key
- DB 계정 비밀번호, 암호화 Key, Cloud/AWS Credential
- `.env`, `*.pem`, `*.key`, `*.secret`

복구 후 Linux owner/group/permission, MySQL version, Secret 파일의 `0600` 권한과 systemd unit의 경로를 별도로 확인한다.
